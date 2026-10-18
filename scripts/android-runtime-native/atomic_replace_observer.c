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
static int pinned=-1,held=0;
static char nonce[33],target[MAX_PATH];
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
    if(size<0||(size_t)size>=capacity)return -1;body[size]=0;return size;
}
static int identity(pid_t tid,int main_process) {
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
    if(!WIFSTOPPED(status)||!identity(tid,0))return -1;
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
        if(!remaining)break;usleep(1000);
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
                if((size_t)bytes<offsetof(struct ptrace_syscall_info,entry.args)+sizeof info.entry.args)goto refused;
                uint64_t from=0,to=0;unsigned long nr=(unsigned long)info.entry.nr;
                if(nr==__NR_rename) { from=info.entry.args[0];to=info.entry.args[1]; }
                else if(nr==__NR_renameat||(nr==__NR_renameat2&&!info.entry.args[4])) { from=info.entry.args[1];to=info.entry.args[3]; }
                if(to) {
                    char destination[MAX_PATH],source[MAX_PATH];
                    if(path_argument(tid,to,destination)&&!strcmp(destination,target)) {
                        struct stat old,temp;
                        if(!path_argument(tid,from,source)||!temporary_path(source,destination)
                            ||lstat(target,&old)||lstat(source,&temp)||!S_ISREG(old.st_mode)||!S_ISREG(temp.st_mode)
                            ||old.st_uid!=owner||temp.st_uid!=owner||old.st_nlink!=1||temp.st_nlink!=1
                            ||old.st_dev!=temp.st_dev||old.st_ino==temp.st_ino)goto refused;
                        held=1;if(!freeze_others(tid))goto refused;
                        printf("{\"event\":\"entry\",\"nonce\":\"%s\",\"pid\":%d,\"tid\":%d,\"syscall\":%lu,\"temporary\":\"%s\",\"old_inode\":%llu,\"temp_inode\":%llu,\"device\":%llu,\"old_bytes\":%lld,\"temp_bytes\":%lld}\n",
                            nonce,original,tid,nr,strrchr(source,'/')+1,(unsigned long long)old.st_ino,(unsigned long long)temp.st_ino,
                            (unsigned long long)old.st_dev,(long long)old.st_size,(long long)temp.st_size);
                        if(!command("stop\n",60000)||!identity(original,1)||syscall(__NR_pidfd_send_signal,pinned,SIGKILL,NULL,0))goto refused;
                        held=0;for(size_t i=0;i<count;i++)tasks[i].alive=0;
                        printf("{\"event\":\"stopped\",\"nonce\":\"%s\",\"pid\":%d,\"original_syscall_resumed\":false}\n",nonce,original);return 0;
                    }
                }
            } else if(info.op!=PTRACE_SYSCALL_INFO_EXIT)goto refused;
        } else if(((unsigned int)status>>16)==0)delivered=WSTOPSIG(status);
        if(!resume_task((size_t)index,delivered))goto refused;
    }
refused:
    printf("{\"event\":\"refused\",\"nonce\":\"%s\",\"pid\":%d,\"held\":%s,\"raw_error_exported\":false}\n",nonce,original,held?"true":"false");return 2;
}
