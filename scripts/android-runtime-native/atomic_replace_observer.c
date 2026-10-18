/* Hosted, same-UID debug observer. No register/memory writes or syscall rewrite.
 * The exact accepted target is held at syscall ENTRY, never after replacement.
 * Only the owning parent can request the pinned original process's one stop.
 */
#define _GNU_SOURCE
#include <sys/ptrace.h>
#include <linux/ptrace.h>
#include <linux/audit.h>
#include <sys/syscall.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <sys/poll.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define APP "cc.kronecker.opennexus.dev"
#define MAX_THREADS 256
#define MAX_PATH 512
static struct { pid_t tid; int stopped,alive; } tasks[MAX_THREADS];
static size_t count;
static pid_t original;
static uid_t owner;
static unsigned long long start_ticks;
static int pinned=-1,held=0,parent_fd=-1;
static unsigned long long entries_seen,renames_seen,candidates_seen,directories_seen,destinations_seen,identity_checks;
static char nonce[33],target[MAX_PATH],target_parent[MAX_PATH];
static struct stat parent_identity;
static volatile sig_atomic_t expired;
static void timeout_signal(int signal_number) { (void)signal_number;expired=1; }
static int number(const char *text,unsigned long long *result) {
    if(!text||!*text||strlen(text)>20)return 0;
    for(const char *p=text;*p;p++)if(*p<'0'||*p>'9')return 0;
    errno=0;char *end;*result=strtoull(text,&end,10);return !errno&&!*end&&*result>0;
}
static int hex_nonce(const char *text) {
    if(!text||strlen(text)!=32)return 0;
    for(size_t i=0;i<32;i++)if(!((text[i]>='0'&&text[i]<='9')||(text[i]>='a'&&text[i]<='f')))return 0;
    return 1;
}
static ssize_t small_file(const char *path,char *body,size_t capacity) {
    int fd=open(path,O_RDONLY|O_CLOEXEC);if(fd<0)return -1;
    ssize_t size=read(fd,body,capacity);int saved=errno;close(fd);errno=saved;
    if(size<0||(size_t)size>=capacity)return -1;
    body[size]=0;return size;
}
static int identity(pid_t tid,int main_process) {
    identity_checks++;
    char path[96],body[16384];snprintf(path,sizeof path,"/proc/%d/status",tid);
    if(small_file(path,body,sizeof body)<0)return 0;
    unsigned int a,b,c,d;int tgid;char *group=strstr(body,"\nTgid:"),*uids=strstr(body,"\nUid:");
    if(!group||!uids||sscanf(group,"\nTgid: %d",&tgid)!=1||tgid!=original
        ||sscanf(uids,"\nUid: %u %u %u %u",&a,&b,&c,&d)!=4||a!=owner||b!=owner||c!=owner||d!=owner)return 0;
    if(!main_process)return 1;
    snprintf(path,sizeof path,"/proc/%d/cmdline",original);
    ssize_t size=small_file(path,body,sizeof body);
    if(size<(ssize_t)sizeof APP||memcmp(body,APP,sizeof APP))return 0;
    for(size_t i=sizeof APP;i<(size_t)size;i++)if(body[i])return 0;
    snprintf(path,sizeof path,"/proc/%d/stat",original);
    if(small_file(path,body,sizeof body)<0)return 0;
    char *last=strrchr(body,')');if(!last||last[1]!=' ')return 0;
    char *save=NULL,*field=strtok_r(last+2," ",&save);
    for(int n=3;n<22&&field;n++)field=strtok_r(NULL," ",&save);
    unsigned long long seen;return field&&number(field,&seen)&&seen==start_ticks;
}
static int task_index(pid_t tid) {
    for(size_t i=0;i<count;i++)if(tasks[i].tid==tid&&tasks[i].alive)return (int)i;
    return -1;
}
static int add_task(pid_t tid,int stopped) {
    int index=task_index(tid);if(index>=0)return index;
    if(count==MAX_THREADS||!identity(tid,0))return -1;
    tasks[count].tid=tid;tasks[count].stopped=stopped;tasks[count].alive=1;return (int)count++;
}
static int observe_stop(pid_t tid,int status) {
    int index=task_index(tid);
    if(index<0&&(index=add_task(tid,0))<0)return -1;
    if(WIFEXITED(status)||WIFSIGNALED(status)) { tasks[index].alive=0;return index; }
    /* waitpid reports only our ptrace-owned thread lifetimes. New/clone TIDs
     * were checked by add_task; an exited lifetime is never revived without
     * a new identity check. Do not open /proc twice for every syscall. */
    if(!WIFSTOPPED(status))return -1;
    tasks[index].stopped=1;
    if((unsigned int)status>>16==PTRACE_EVENT_CLONE) {
        unsigned long child=0;
        if(ptrace(PTRACE_GETEVENTMSG,tid,0,&child)<0||child>2147483647UL||add_task((pid_t)child,0)<0)return -1;
    }
    return index;
}
static int attach_all(void) {
    for(int round=0;round<8;round++) {
        char path[96];snprintf(path,sizeof path,"/proc/%d/task",original);
        DIR *directory=opendir(path);if(!directory)return 0;
        struct dirent *entry;int added=0;
        while((entry=readdir(directory))) {
            unsigned long long parsed;if(!number(entry->d_name,&parsed)||parsed>2147483647ULL)continue;
            pid_t tid=(pid_t)parsed;if(task_index(tid)>=0)continue;
            if(!identity(tid,0)||count==MAX_THREADS) { closedir(directory);return 0; }
            if(ptrace(PTRACE_SEIZE,tid,0,(void *)(uintptr_t)PTRACE_O_TRACESYSGOOD)<0) { closedir(directory);return 0; }
            int index=add_task(tid,0);if(index<0) { closedir(directory);return 0; }
            if(ptrace(PTRACE_INTERRUPT,tid,0,0)<0) { closedir(directory);return 0; }
            int status;pid_t seen=waitpid(tid,&status,__WALL);
            if(seen!=tid||!WIFSTOPPED(status)||observe_stop(tid,status)<0) { closedir(directory);return 0; }
            added++;
        }
        closedir(directory);
        if(!added) {
            if(task_index(original)<0||!identity(original,1))return 0;
            for(size_t i=0;i<count;i++)if(tasks[i].alive&&(!tasks[i].stopped||ptrace(PTRACE_SETOPTIONS,tasks[i].tid,0,
                (void *)(uintptr_t)(PTRACE_O_TRACESYSGOOD|PTRACE_O_TRACECLONE))<0))return 0;
            return 1;
        }
    }
    return 0;
}
static int resume_task(size_t index,int signal_number) {
    if(!tasks[index].alive)return 1;
    if(!tasks[index].stopped||ptrace(PTRACE_SYSCALL,tasks[index].tid,0,(void *)(uintptr_t)signal_number)<0)return 0;
    tasks[index].stopped=0;return 1;
}
static void cleanup(void) {
    if(parent_fd>=0) { close(parent_fd);parent_fd=-1; }
    if(held) { if(pinned>=0)syscall(__NR_pidfd_send_signal,pinned,SIGKILL,NULL,0);return; }
    /* An unarmed capability refusal must not leave this debug app suspended. */
    for(size_t i=0;i<count;i++)if(tasks[i].alive&&!tasks[i].stopped)ptrace(PTRACE_INTERRUPT,tasks[i].tid,0,0);
    for(int round=0;round<100;round++) {
        int remaining=0;
        for(size_t i=0;i<count;i++)if(tasks[i].alive) {
            if(!tasks[i].stopped) {
                int status;pid_t seen=waitpid(tasks[i].tid,&status,__WALL|WNOHANG);
                if(seen==tasks[i].tid)observe_stop(seen,status);
            }
            if(tasks[i].alive&&tasks[i].stopped) { ptrace(PTRACE_DETACH,tasks[i].tid,0,0);tasks[i].alive=0; }
            else if(tasks[i].alive)remaining++;
        }
        if(!remaining)break;
        usleep(1000);
    }
}
static int command(const char *expected,int milliseconds) {
    struct pollfd input={STDIN_FILENO,POLLIN,0};
    if(poll(&input,1,milliseconds)!=1||!(input.revents&POLLIN))return 0;
    char body[32];size_t length=0;
    while(length<sizeof body-1) {
        char c;ssize_t n=read(STDIN_FILENO,&c,1);if(n!=1)return 0;body[length++]=c;if(c=='\n')break;
    }
    body[length]=0;return !strcmp(body,expected);
}
static int path_argument(pid_t tid,uint64_t address,char *path) {
    if(!address||address>UINT64_C(0x7fffffffffff))return 0;
    for(size_t offset=0;offset<MAX_PATH;offset+=sizeof(long)) {
        errno=0;long word=ptrace(PTRACE_PEEKDATA,tid,(void *)(uintptr_t)(address+offset),0);
        if(word==-1&&errno)return 0;
        for(size_t i=0;i<sizeof word&&offset+i<MAX_PATH;i++) {
            char c=((char *)&word)[i];path[offset+i]=c;if(!c)return offset+i>0;
        }
    }
    return 0;
}
static int temporary_path(const char *source,const char *destination) {
    if(strcmp(destination,target))return 0;
    const char *parent=strrchr(target,'/'),*base=strrchr(source,'/');
    if(!parent||!base||base-source!=parent-target||memcmp(source,target,(size_t)(parent-target)+1))return 0;
    base++;size_t length=strlen(base);if(length<9||length>35||strncmp(base,".tmp",4))return 0;
    for(const char *p=base+4;*p;p++)if(!((*p>='0'&&*p<='9')||(*p>='a'&&*p<='z')||(*p>='A'&&*p<='Z')))return 0;
    return 1;
}
static int owned_absolute(const char *path) {
    return !strncmp(path,"/data/user/0/" APP "/",sizeof("/data/user/0/" APP "/")-1)
        ||!strncmp(path,"/data/data/" APP "/",sizeof("/data/data/" APP "/")-1);
}
static int path_components(const char *path) {
    if(!path||!*path||strlen(path)>=MAX_PATH)return 0;
    for(const char *p=path;*p;p++)if((unsigned char)*p<32||*p==127||*p=='\\')return 0;
    const char *part=path;
    for(const char *p=path;;p++)if(*p=='/'||!*p) {
        if(p-part==2&&part[0]=='.'&&part[1]=='.')return 0;
        if(!*p)break;
        part=p+1;
    }
    return 1;
}
static int directory_argument(uint64_t value,int *fd) {
    int32_t low=(int32_t)(uint32_t)value;
    if(value!=(uint64_t)(uint32_t)low&&value!=(uint64_t)(int64_t)low)return 0;
    if(low<0&&low!=AT_FDCWD)return 0;
    *fd=(int)low;return 1;
}
static int same_directory(int fd) {
    struct stat current;
    return fd>=0&&!fstat(fd,&current)&&S_ISDIR(current.st_mode)&&current.st_uid==owner
        &&current.st_dev==parent_identity.st_dev&&current.st_ino==parent_identity.st_ino;
}
static int parent_still_original(void) {
    struct stat current;
    return same_directory(parent_fd)&&!lstat(target_parent,&current)&&S_ISDIR(current.st_mode)
        &&current.st_uid==owner&&current.st_dev==parent_identity.st_dev&&current.st_ino==parent_identity.st_ino;
}
/* Resolve only a declared original basename or its same-parent temporary.
 * No syscall argument is rewritten. Directory FDs are read through the original
 * stopped task, and parent dev/inode must equal our pinned owned directory.
 * Absolute Android aliases therefore name the same object without trusting
 * their spelling. Unrelated paths are never exported or opened as files. */
static int resolved_argument(pid_t tid,uint64_t directory,const char *argument,int temporary,char *canonical,int *form) {
    if(!path_components(argument))return 0;
    const char *slash=strrchr(argument,'/'),*name=slash?slash+1:argument;
    if(temporary) {
        size_t length=strlen(name);if(length<9||length>35||strncmp(name,".tmp",4))return 0;
        for(const char *p=name+4;*p;p++)if(!((*p>='0'&&*p<='9')||(*p>='a'&&*p<='z')||(*p>='A'&&*p<='Z')))return 0;
    } else if(strcmp(name,"original.md"))return 0;
    char parent[MAX_PATH];size_t length=slash?(size_t)(slash-argument):0;
    if(length) { memcpy(parent,argument,length);parent[length]=0; } else strcpy(parent,".");
    int opened=-1,base=-1,kind;
    if(argument[0]=='/') {
        if(!owned_absolute(argument)||!slash||!length)return 0;
        opened=open(parent,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);kind=1;
    } else {
        int fd;if(!directory_argument(directory,&fd))return 0;
        char reference[96];
        if(fd==AT_FDCWD)snprintf(reference,sizeof reference,"/proc/%d/cwd",tid);
        else snprintf(reference,sizeof reference,"/proc/%d/fd/%d",tid,fd);
        base=open(reference,O_RDONLY|O_DIRECTORY|O_CLOEXEC);if(base<0)return 0;
        opened=openat(base,parent,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
        kind=fd==AT_FDCWD?2:3;
    }
    int matches=same_directory(opened);
    if(opened>=0)close(opened);
    if(base>=0)close(base);
    if(!matches)return 0;
    int written=snprintf(canonical,MAX_PATH,"%s/%s",target_parent,name);
    if(written<=0||written>=MAX_PATH)return 0;
    *form=kind;return 1;
}
static int freeze_others(pid_t matched) {
    for(size_t i=0;i<count;i++)if(tasks[i].alive&&!tasks[i].stopped&&tasks[i].tid!=matched
        &&ptrace(PTRACE_INTERRUPT,tasks[i].tid,0,0)<0)return 0;
    while(!expired) {
        int pending=0;for(size_t i=0;i<count;i++)if(tasks[i].alive&&!tasks[i].stopped)pending++;
        if(!pending)return identity(original,1);
        int status;pid_t tid=waitpid(-1,&status,__WALL);if(tid<0||observe_stop(tid,status)<0)return 0;
        for(size_t i=0;i<count;i++)if(tasks[i].alive&&!tasks[i].stopped&&ptrace(PTRACE_INTERRUPT,tasks[i].tid,0,0)<0)return 0;
    }
    return 0;
}
static int self_test(void) {
    unsigned long long n;int checks=0;
#define CHECK(value) do { if(!(value))return 3;checks++; } while(0)
    CHECK(number("123",&n)&&n==123);CHECK(!number("-1",&n));CHECK(!number("0",&n));CHECK(!number("1x",&n));
    CHECK(hex_nonce("0123456789abcdef0123456789abcdef"));CHECK(!hex_nonce("0123456789ABCDEF0123456789ABCDEF"));
    strcpy(target,"/data/user/0/" APP "/files/vault/atomic-area/original.md");
    CHECK(temporary_path("/data/user/0/" APP "/files/vault/atomic-area/.tmpA12345",target));
    CHECK(!temporary_path("/data/user/0/" APP "/files/vault/other/.tmpA12345",target));
    CHECK(!temporary_path("/data/user/0/" APP "/files/vault/atomic-area/original.md",target));
    CHECK(!temporary_path("/data/user/0/" APP "/files/vault/atomic-area/.tmp../a",target));
    CHECK(!temporary_path("/data/user/0/" APP "/files/vault/atomic-area/.tmpA12345","/foreign/original.md"));
    original=2147483000;owner=10123;count=1;tasks[0].tid=original;tasks[0].alive=1;
    CHECK(observe_stop(original,((SIGTRAP|0x80)<<8)|0x7f)==0&&tasks[0].stopped);
    CHECK(observe_stop(original-1,((SIGTRAP|0x80)<<8)|0x7f)==-1);
    CHECK(observe_stop(original,0)==0&&!tasks[0].alive);
    CHECK(observe_stop(original,((SIGTRAP|0x80)<<8)|0x7f)==-1);
    CHECK(owned_absolute("/data/user/0/" APP "/files/vault/atomic-area/original.md"));
    CHECK(owned_absolute("/data/data/" APP "/files/vault/atomic-area/original.md"));
    CHECK(!owned_absolute("/data/user/0/foreign/files/original.md"));
    CHECK(!owned_absolute("/data/user/0/" APP "-foreign/files/original.md"));
    CHECK(path_components("./files/vault/atomic-area/original.md"));
    CHECK(!path_components(""));CHECK(!path_components("../original.md"));
    CHECK(!path_components("files/../original.md"));CHECK(!path_components("files/\noriginal.md"));
    int descriptor;
    CHECK(directory_argument((uint64_t)(uint32_t)AT_FDCWD,&descriptor)&&descriptor==AT_FDCWD);
    CHECK(directory_argument((uint64_t)(int64_t)AT_FDCWD,&descriptor)&&descriptor==AT_FDCWD);
    CHECK(directory_argument(42,&descriptor)&&descriptor==42);
    CHECK(!directory_argument((uint64_t)(int64_t)-3,&descriptor));
    CHECK(!directory_argument(UINT64_C(0x12340000002a),&descriptor));
    char root[]="/tmp/opennexus-atomic-path-XXXXXX";CHECK(mkdtemp(root)!=NULL);
    owner=getuid();original=getpid();strcpy(target_parent,root);
    snprintf(target,sizeof target,"%s/original.md",root);
    parent_fd=open(root,O_RDONLY|O_DIRECTORY|O_CLOEXEC);
    CHECK(parent_fd>=0&&!fstat(parent_fd,&parent_identity)&&same_directory(parent_fd));
    int previous_cwd=open(".",O_RDONLY|O_DIRECTORY|O_CLOEXEC);CHECK(previous_cwd>=0);
    CHECK(parent_still_original());
    int file=openat(parent_fd,"original.md",O_CREAT|O_EXCL|O_WRONLY|O_CLOEXEC,0600);CHECK(file>=0);
    CHECK(!mkdirat(parent_fd,"foreign",0700));
    int foreign=openat(parent_fd,"foreign",O_RDONLY|O_DIRECTORY|O_CLOEXEC);CHECK(foreign>=0&&!same_directory(foreign));
    char resolved[MAX_PATH];int form=0;
    CHECK(resolved_argument(original,(uint64_t)parent_fd,"original.md",0,resolved,&form)&&form==3&&!strcmp(resolved,target));
    CHECK(resolved_argument(original,(uint64_t)parent_fd,"./original.md",0,resolved,&form)&&form==3&&!strcmp(resolved,target));
    CHECK(resolved_argument(original,(uint64_t)parent_fd,".tmpA12345",1,resolved,&form)&&form==3&&temporary_path(resolved,target));
    CHECK(!resolved_argument(original,(uint64_t)parent_fd,".tmpBAD",1,resolved,&form));
    CHECK(!resolved_argument(original,(uint64_t)parent_fd,"original.md",1,resolved,&form));
    CHECK(!resolved_argument(original,(uint64_t)parent_fd,"other.md",0,resolved,&form));
    CHECK(!resolved_argument(original,(uint64_t)parent_fd,"../original.md",0,resolved,&form));
    CHECK(!resolved_argument(original,(uint64_t)foreign,"original.md",0,resolved,&form));
    CHECK(!resolved_argument(original,(uint64_t)file,"original.md",0,resolved,&form));
    CHECK(!fchdir(parent_fd));
    CHECK(resolved_argument(original,(uint64_t)(int64_t)AT_FDCWD,"original.md",0,resolved,&form)&&form==2&&!strcmp(resolved,target));
    CHECK(!fchdir(previous_cwd));
    CHECK(!symlinkat(".",parent_fd,"alias"));
    CHECK(!resolved_argument(original,(uint64_t)parent_fd,"alias/original.md",0,resolved,&form));
    CHECK(!resolved_argument(original,(uint64_t)parent_fd,"/tmp/not-owned/original.md",0,resolved,&form));
    CHECK(same_directory(parent_fd)&&parent_still_original());
    CHECK(!unlinkat(parent_fd,"alias",0));CHECK(!unlinkat(parent_fd,"original.md",0));
    CHECK(!close(file));CHECK(!close(foreign));CHECK(!unlinkat(parent_fd,"foreign",AT_REMOVEDIR));
    CHECK(!close(parent_fd));parent_fd=-1;CHECK(!rmdir(root));CHECK(!close(previous_cwd));
    printf("ORIGINAL_ATOMIC_OBSERVER_DATA_OK %d\n",checks);return 0;
}
int main(int argc,char **argv) {
    if(argc==2&&!strcmp(argv[1],"--self-test"))return self_test();
    unsigned long long pid,uid;
    if(argc!=6||!number(argv[1],&pid)||pid>2147483647ULL||!number(argv[2],&uid)||uid<10000||uid>=100000
        ||!number(argv[3],&start_ticks)||!hex_nonce(argv[4])||strlen(argv[5])>=MAX_PATH||getuid()!=uid)return 2;
    original=(pid_t)pid;owner=(uid_t)uid;strcpy(nonce,argv[4]);strcpy(target,argv[5]);
    if(original==getpid()||(strncmp(target,"/data/user/0/" APP "/",sizeof("/data/user/0/" APP "/")-1)
        &&strncmp(target,"/data/data/" APP "/",sizeof("/data/data/" APP "/")-1))
        ||strstr(target,"/../")||strstr(target,"/./")||strlen(target)<24
        ||strcmp(target+strlen(target)-24,"/atomic-area/original.md"))return 2;
    struct sigaction action;memset(&action,0,sizeof action);action.sa_handler=timeout_signal;sigemptyset(&action.sa_mask);
    if(sigaction(SIGALRM,&action,NULL))return 2;
    setvbuf(stdout,NULL,_IOLBF,0);alarm(180);atexit(cleanup);
    const char *parent_end=strrchr(target,'/');
    if(!parent_end||(size_t)(parent_end-target)>=sizeof target_parent)goto refused;
    memcpy(target_parent,target,(size_t)(parent_end-target));target_parent[parent_end-target]=0;
    parent_fd=open(target_parent,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
    if(parent_fd<0||fstat(parent_fd,&parent_identity)||!S_ISDIR(parent_identity.st_mode)
        ||parent_identity.st_uid!=owner||!parent_still_original())goto refused;
    pinned=(int)syscall(__NR_pidfd_open,original,0);
    if(pinned<0||!identity(original,1)||!attach_all())goto refused;
    printf("{\"event\":\"ready\",\"nonce\":\"%s\",\"pid\":%d,\"uid\":%u,\"threads\":%zu}\n",nonce,original,owner,count);
    if(!command("arm\n",10000))goto refused;
    for(size_t i=0;i<count;i++)if(!resume_task(i,0))goto refused;
    printf("{\"event\":\"armed\",\"nonce\":\"%s\",\"pid\":%d}\n",nonce,original);
    while(!expired) {
        int status;pid_t tid=waitpid(-1,&status,__WALL);if(tid<0)break;
        int index=observe_stop(tid,status);if(index<0)goto refused;
        if(!tasks[index].alive) { if(tid==original)goto refused;continue; }
        int delivered=0;
        if(WSTOPSIG(status)==(SIGTRAP|0x80)) {
            struct ptrace_syscall_info info;memset(&info,0,sizeof info);
            long bytes=ptrace(PTRACE_GET_SYSCALL_INFO,tid,(void *)sizeof info,&info);
            if(bytes<0||info.arch!=AUDIT_ARCH_X86_64)goto refused;
            if(info.op==PTRACE_SYSCALL_INFO_ENTRY) {
                if(++entries_seen>1000000)goto refused;
                if((size_t)bytes<offsetof(struct ptrace_syscall_info,entry.args)+sizeof info.entry.args)goto refused;
                uint64_t from=0,to=0,from_directory=(uint64_t)(int64_t)AT_FDCWD,to_directory=from_directory;
                unsigned long nr=(unsigned long)info.entry.nr;
                if(nr==__NR_rename) { from=info.entry.args[0];to=info.entry.args[1]; }
                else if(nr==__NR_renameat||(nr==__NR_renameat2&&!info.entry.args[4])) {
                    from_directory=info.entry.args[0];from=info.entry.args[1];
                    to_directory=info.entry.args[2];to=info.entry.args[3];
                }
                if(to) {
                    renames_seen++;
                    if(!identity(tid,0)||!identity(original,1))goto refused;
                    char destination[MAX_PATH],source[MAX_PATH],raw_destination[MAX_PATH],raw_source[MAX_PATH];
                    int destination_form=0,source_form=0;
                    if(path_argument(tid,to,raw_destination)) {
                        const char *last=strrchr(raw_destination,'/'),*base=last?last+1:raw_destination;
                        if(strcmp(base,"original.md"))goto next_syscall;
                        candidates_seen++;
                        if(!resolved_argument(tid,to_directory,raw_destination,0,destination,&destination_form)
                            ||strcmp(destination,target))goto next_syscall;
                        directories_seen++;
                        held=1;if(!freeze_others(tid))goto refused;
                        /* Every app thread is now stopped before this original
                         * syscall executes. Re-read its actual arguments and
                         * directory identity; do not trust a pre-freeze path. */
                        if(!parent_still_original()||!path_argument(tid,to,raw_destination)
                            ||!path_argument(tid,from,raw_source)
                            ||!resolved_argument(tid,to_directory,raw_destination,0,destination,&destination_form)
                            ||!resolved_argument(tid,from_directory,raw_source,1,source,&source_form)
                            ||!temporary_path(source,destination))goto refused;
                        struct stat old,temp;
                        const char *temporary=strrchr(source,'/')+1;
                        if(fstatat(parent_fd,"original.md",&old,AT_SYMLINK_NOFOLLOW)
                            ||fstatat(parent_fd,temporary,&temp,AT_SYMLINK_NOFOLLOW)
                            ||!S_ISREG(old.st_mode)||!S_ISREG(temp.st_mode)
                            ||old.st_uid!=owner||temp.st_uid!=owner||old.st_nlink!=1||temp.st_nlink!=1
                            ||old.st_dev!=parent_identity.st_dev||old.st_dev!=temp.st_dev||old.st_ino==temp.st_ino)goto refused;
                        destinations_seen++;
                        const char *form=destination_form==1?"absolute":destination_form==2?"relative_cwd":"relative_dirfd";
                        printf("{\"event\":\"entry\",\"nonce\":\"%s\",\"pid\":%d,\"tid\":%d,\"syscall\":%lu,\"temporary\":\"%s\",\"old_inode\":%llu,\"temp_inode\":%llu,\"device\":%llu,\"old_bytes\":%lld,\"temp_bytes\":%lld,\"destination_form\":\"%s\",\"parent_inode\":%llu,\"parent_device\":%llu,\"trace_counts\":{\"entries\":%llu,\"renames\":%llu,\"candidates\":%llu,\"directories\":%llu,\"destinations\":%llu,\"identity_checks\":%llu}}\n",
                            nonce,original,tid,nr,temporary,(unsigned long long)old.st_ino,(unsigned long long)temp.st_ino,
                            (unsigned long long)old.st_dev,(long long)old.st_size,(long long)temp.st_size,form,
                            (unsigned long long)parent_identity.st_ino,(unsigned long long)parent_identity.st_dev,
                            entries_seen,renames_seen,candidates_seen,directories_seen,destinations_seen,identity_checks);
                        if(!command("stop\n",60000)||!identity(original,1)||syscall(__NR_pidfd_send_signal,pinned,SIGKILL,NULL,0))goto refused;
                        held=0;for(size_t i=0;i<count;i++)tasks[i].alive=0;
                        printf("{\"event\":\"stopped\",\"nonce\":\"%s\",\"pid\":%d,\"original_syscall_resumed\":false}\n",nonce,original);return 0;
                    }
                }
            } else if(info.op!=PTRACE_SYSCALL_INFO_EXIT)goto refused;
        } else if(((unsigned int)status>>16)==0)delivered=WSTOPSIG(status);
next_syscall:
        if(!resume_task((size_t)index,delivered))goto refused;
    }
refused:
    printf("{\"event\":\"refused\",\"nonce\":\"%s\",\"pid\":%d,\"held\":%s,\"raw_error_exported\":false,\"trace_counts\":{\"entries\":%llu,\"renames\":%llu,\"candidates\":%llu,\"directories\":%llu,\"destinations\":%llu,\"identity_checks\":%llu}}\n",
        nonce,original,held?"true":"false",entries_seen,renames_seen,candidates_seen,directories_seen,destinations_seen,identity_checks);return 2;
}
