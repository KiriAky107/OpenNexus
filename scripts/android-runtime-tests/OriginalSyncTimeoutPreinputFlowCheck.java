package cc.kronecker.opennexus.testdriver;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;

public final class OriginalSyncTimeoutPreinputFlowCheck {
    private static int checks,cases;
    private static final String P="11111111-1111-1111-1111-111111111111",I="22222222-2222-2222-2222-222222222222";
    private static void check(boolean value) {checks++;if(!value)throw new AssertionError("Preinput flow contract");}
    private static final class Port implements OriginalSyncTimeoutPreinputFlow.Port {
        final Path root;final List<String> events=new ArrayList<>();final IOException original=new IOException("synthetic write/read unknown");
        long time=1000;boolean clipped,known=true,closed,freeze,changed,lateAction;int uid=12001,reads,reveals;
        String mode="";
        Port(Path parent,boolean reveal) throws Exception {root=Files.createDirectory(parent.resolve("case-"+(cases++)));clipped=reveal;}
        private void live() {if(closed)throw new AssertionError("Port queried after ready");}
        public long uptime() {live();events.add("clock");return time;}
        public void pause(long millis) {live();check(millis==50);events.add("pause");if(!freeze)time+=millis;}
        public OriginalSyncTimeoutPreinputFlow.Frame read() throws Exception {
            live();reads++;events.add("read");if(mode.equals("read-unknown"))throw original;
            int[] own={0,0,1000,1000},button={changed?11:10,10,20,20},section={10,30,90,clipped?1100:80};
            return new OriginalSyncTimeoutPreinputFlow.Frame(uid,7,own,button,own,section,own);
        }
        public void write(OriginalSyncTimeoutPreinputFlow.Record kind,byte[] bytes) throws Exception {
            live();String name=kind==OriginalSyncTimeoutPreinputFlow.Record.INTENT?"intent":"attempt";
            events.add(name);File target=root.resolve(name+".json").toFile();
            if(target.exists())OriginalSyncStopActions.writeOnce(target,bytes);
            if(mode.equals(name+"-partial")) {
                check(target.createNewFile());try(FileOutputStream out=new FileOutputStream(target)) {out.write(bytes,0,bytes.length/2);out.getFD().sync();}
                throw original;
            }
            OriginalSyncStopActions.writeOnce(target,bytes);
            if(mode.equals(name+"-unknown"))throw original;
            if(mode.equals(name+"-mutated-buffer"))bytes[0]=' ';
        }
        public boolean revealSection(long previous,long deadline) throws Exception {
            live();events.add("reveal");reveals++;
            check(Files.isRegularFile(root.resolve("intent.json")) && Files.isRegularFile(root.resolve("attempt.json")));
            check(previous<=time && time<=deadline && deadline-previous<=5000);
            if(mode.equals("action-unknown"))throw original;
            if(lateAction)time=6001;
            if(known)clipped=false;
            return known;
        }
    }
    private static OriginalSyncTimeoutPreinputFlow prepare(Port port) throws Exception {return OriginalSyncTimeoutPreinputFlow.prepare(P,I,port);}
    private static String hash(byte[] raw) throws Exception {
        byte[] bytes=MessageDigest.getInstance("SHA-256").digest(raw);StringBuilder out=new StringBuilder();
        for(byte n:bytes) {out.append(Character.forDigit((n>>>4)&15,16));out.append(Character.forDigit(n&15,16));}return out.toString();
    }
    private static String positive(Path root,boolean reveal) throws Exception {
        Port port=new Port(root,reveal);OriginalSyncTimeoutPreinputFlow flow=prepare(port);
        check(port.time>=1600 && port.reveals==(reveal?1:0));
        port.time+=600;flow.finalCheck(new int[]{10,10,20,20},new int[]{10,10,20,20},7);
        int calls=port.events.size();port.closed=true;
        byte[] raw=flow.readyBytes(12001,7,new int[]{10,10,20,20},new int[]{10,10,20,20},15,15,5000);
        check(raw.length<=4096 && port.events.size()==calls);
        String wire=new String(raw,StandardCharsets.UTF_8);
        check(wire.contains(hash(Files.readAllBytes(port.root.resolve("intent.json")))));
        if(reveal)check(wire.contains(hash(Files.readAllBytes(port.root.resolve("attempt.json")))));
        try {flow.readyBytes(12001,7,new int[]{10,10,20,20},new int[]{10,10,20,20},15,15,5000);throw new AssertionError();}
        catch(IllegalStateException refused) {check(port.events.size()==calls);}
        return wire;
    }
    public static void main(String[] args) throws Exception {
        Path root=new File(args[0]).toPath();check(Files.isDirectory(root));String a=positive(root,false),b=positive(root,true);
        for(String mode:new String[]{"intent-partial","intent-unknown","attempt-partial","attempt-unknown","action-unknown"}) {
            Port port=new Port(root,mode.startsWith("attempt") || mode.startsWith("action"));port.mode=mode;
            try {prepare(port);throw new AssertionError();}catch(IOException original) {check(original==port.original);}
            check(Files.isRegularFile(port.root.resolve("intent.json")));check(port.reveals==(mode.startsWith("action")?1:0));
            byte[] before=Files.readAllBytes(port.root.resolve("intent.json"));
            try {prepare(port);throw new AssertionError();}catch(IllegalStateException exists) {check(java.util.Arrays.equals(before,Files.readAllBytes(port.root.resolve("intent.json"))));}
        }
        for(String mode:new String[]{"intent-mutated-buffer","attempt-mutated-buffer"}) {
            Port port=new Port(root,mode.startsWith("attempt"));port.mode=mode;
            try {prepare(port);throw new AssertionError();}catch(IllegalStateException refused) {check(port.reveals==0);}
        }
        Port declined=new Port(root,true);declined.known=false;
        try {prepare(declined);throw new AssertionError();}catch(IllegalStateException refused) {check(declined.reveals==1);}
        Port late=new Port(root,true);late.lateAction=true;
        try {prepare(late);throw new AssertionError();}catch(IllegalStateException refused) {check(late.reveals==1);}
        Port frozen=new Port(root,false);frozen.freeze=true;
        try {prepare(frozen);throw new AssertionError();}catch(IllegalStateException refused) {check(frozen.reads==102);}
        for(String mode:new String[]{"moved","late","uid","read-unknown","clipped"}) {
            Port port=new Port(root,false);OriginalSyncTimeoutPreinputFlow flow=prepare(port);
            if(mode.equals("moved"))port.changed=true;
            if(mode.equals("late"))port.time=6001;
            if(mode.equals("uid"))port.uid++;
            if(mode.equals("read-unknown"))port.mode=mode;
            if(mode.equals("clipped"))port.clipped=true;
            int oldReads=port.reads;
            try {flow.finalCheck(new int[]{10,10,20,20},new int[]{10,10,20,20},7);throw new AssertionError();}
            catch(Exception refused) {check(!mode.equals("read-unknown") || refused==port.original);}
            check(port.reveals==0);if(mode.equals("late"))check(port.reads==oldReads);
            int count=port.reads;
            try {flow.finalCheck(new int[]{10,10,20,20},new int[]{10,10,20,20},7);throw new AssertionError();}
            catch(IllegalStateException refused) {check(port.reads==count);}
        }
        System.out.println("{\"passed\":true,\"assertions\":"+checks+",\"actual_Android_or_Native\":false,\"vectors\":["+a+","+b+"]}");
    }
}
