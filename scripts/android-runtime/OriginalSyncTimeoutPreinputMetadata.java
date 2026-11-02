package cc.kronecker.opennexus.testdriver;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.Map;
import java.util.TreeMap;

/** Closed RAM metadata only; caller reports are not independent UI/file/Native ACKs. */
final class OriginalSyncTimeoutPreinputMetadata {
    static final String POLICY="timeout-preinput-current-public-region-v1";
    private static final String PHASE="cycle-timeout-original",APP="cc.kronecker.opennexus";
    private static final long MAX=Long.MAX_VALUE;
    private OriginalSyncTimeoutPreinputMetadata() {}

    private static final class Payload {
        final TreeMap<String,Object> value;
        final byte[] encoded;
        Payload(TreeMap<String,Object> value) {this.value=value;encoded=encode(value);}
    }
    static final class Parent {
        private final String parent,input;
        private final Payload payload;
        private Parent(String parent,String input,Payload value) {this.parent=parent;this.input=input;payload=value;}
        byte[] bytes() {return payload.encoded.clone();}
    }
    static final class Ready {
        private final Parent parent;
        private final int uid,window;
        private final int[] original,visible;
        private final Payload payload;
        private Ready(Parent parent,int uid,int window,int[] original,int[] visible,Payload value) {
            this.parent=parent;this.uid=uid;this.window=window;
            this.original=original;this.visible=visible;payload=value;
        }
        byte[] bytes() {return payload.encoded.clone();}
    }
    private enum Kind {INTENT,ATTEMPT}
    static final class WriteRecord {
        private final Kind kind;
        private final Parent parent;
        private final int uid,window;
        private final long start;
        private final String intentHash;
        private final Payload payload;
        private WriteRecord(Kind kind,Parent parent,int uid,int window,long start,String intentHash,Payload payload) {
            this.kind=kind;this.parent=parent;this.uid=uid;this.window=window;this.start=start;
            this.intentHash=intentHash;this.payload=payload;
        }
        byte[] bytes() {return payload.encoded.clone();}
    }
    static final class Written {
        private final WriteRecord record;
        private final String rawHash;
        private final TreeMap<String,Object> value;
        private Written(WriteRecord record,String hash) {
            this.record=record;rawHash=hash;value=map("metadata",record.payload.value,"raw_sha256",hash,"writer_returned",true);
        }
    }
    static final class Wire {
        private final byte[] encoded;
        private Wire(byte[] value) {encoded=value;}
        byte[] bytes() {return encoded.clone();}
    }

    static Parent parent(String parent,String input) {
        if(!uuid(parent) || !uuid(input) || parent.equals(input))return null;
        Payload value=new Payload(map("schema",1,"phase",PHASE,"parent_intent_id",parent,
            "input_intent_id",input,"native_invocation_id_observed",false));
        return value.encoded==null?null:new Parent(parent,input,value);
    }
    static Ready ready(Parent parent,int uid,int window,int[] original,int[] visible,int x,int y,long elapsed) {
        if(parent==null || !owner(uid,window) || elapsed<0 || elapsed>MAX-36000
                || !rect(original) || !rect(visible) || !contains(original,visible)
                || x<0 || y<0 || visible[0]+visible[2]<0 || visible[1]+visible[3]<0
                || x!=(visible[0]+visible[2])/2 || y!=(visible[1]+visible[3])/2
                || x<visible[0] || x>=visible[2] || y<visible[1] || y>=visible[3])return null;
        int[] a=original.clone(),b=visible.clone();
        Payload value=new Payload(map("schema",1,"phase",PHASE,"action","cycle-start",
            "parent_intent_id",parent.parent,"driver_intent_id",parent.input,"package",APP,
            "uid",uid,"window",window,"original",a,"visible",b,"x",x,"y",y,"elapsed_ms",elapsed));
        return value.encoded==null?null:new Ready(parent,uid,window,a,b,value);
    }
    static WriteRecord intent(Parent parent,int uid,int window,long startedUptime) {
        if(parent==null || !owner(uid,window) || startedUptime<0 || startedUptime>MAX-5000)return null;
        TreeMap<String,Object> value=scope(parent,uid,window,startedUptime);
        value.put("preinput_only",true);value.put("section_reveal_limit",1);
        value.put("native_timeout_verified",false);
        Payload payload=new Payload(value);
        return payload.encoded==null?null:new WriteRecord(Kind.INTENT,parent,uid,window,startedUptime,null,payload);
    }
    static WriteRecord attempt(Written intent) {
        if(intent==null || intent.record.kind!=Kind.INTENT)return null;
        WriteRecord record=intent.record;
        TreeMap<String,Object> value=scope(record.parent,record.uid,record.window,record.start);
        value.put("attempt",1);value.put("action","SHOW_ON_SCREEN");value.put("preinput_only",true);
        value.put("intent_raw_sha256",intent.rawHash);
        Payload payload=new Payload(value);
        return payload.encoded==null?null:new WriteRecord(Kind.ATTEMPT,record.parent,record.uid,record.window,record.start,intent.rawHash,payload);
    }
    /** Hash the supplied writer buffer only after checking it against the frozen encoding. */
    static Written reportedWritten(WriteRecord record,byte[] suppliedWriterBuffer,Boolean returned) {
        if(record==null || suppliedWriterBuffer==null || !Boolean.TRUE.equals(returned)
                || !Arrays.equals(record.payload.encoded,suppliedWriterBuffer))return null;
        String hash=digest(suppliedWriterBuffer);
        return hash==null?null:new Written(record,hash);
    }
    static Wire project(Parent parent,Ready ready,Written intent,Written attempt,Boolean revealReturned,long finalUptime,
            Boolean currentPairStable,Boolean finalPairCheckedBeforeReady) {
        if(!Boolean.TRUE.equals(currentPairStable) || !Boolean.TRUE.equals(finalPairCheckedBeforeReady)
                || parent==null || ready==null || intent==null || intent.record.kind!=Kind.INTENT
                || !same(parent,ready.parent) || !same(parent,intent.record.parent)
                || ready.uid!=intent.record.uid || ready.window!=intent.record.window
                || !Arrays.equals(ready.original,ready.visible) || ready.original[0]<0 || ready.original[1]<0
                || finalUptime<intent.record.start || finalUptime-intent.record.start<600
                || finalUptime-intent.record.start>5000)return null;
        if(attempt==null) {if(revealReturned!=null)return null;}
        else if(!Boolean.TRUE.equals(revealReturned) || attempt.record.kind!=Kind.ATTEMPT
                || !same(parent,attempt.record.parent) || attempt.record.uid!=ready.uid || attempt.record.window!=ready.window
                || attempt.record.start!=intent.record.start || !intent.rawHash.equals(attempt.record.intentHash))return null;
        String parentHash=digest(parent.payload.encoded),readyHash=digest(ready.payload.encoded);
        if(parentHash==null || readyHash==null)return null;
        TreeMap<String,Object> value=scope(parent,ready.uid,ready.window,intent.record.start);
        value.put("parent_metadata_canonical_sha256",parentHash);value.put("ready_metadata_canonical_sha256",readyHash);
        value.put("final_sample_uptime_ms",finalUptime);value.put("intent_written",intent.value);
        value.put("attempt_written",attempt==null?null:attempt.value);value.put("reveal_returned",revealReturned);
        value.put("preparation_budget_ms",5000);value.put("stable_policy_ms",600);value.put("min_samples_policy",3);
        value.put("section_reveal_limit",1);value.put("current_pair_stable_reported",true);
        value.put("final_original_button_hit_and_pair_checked_before_ready",true);value.put("current_geometry_only",true);
        value.put("future_warning_visibility_guaranteed",false);value.put("network_admission",false);
        value.put("native_timeout_verified",false);value.put("stable_geometry_duration_independently_recomputed",false);
        byte[] wire=encode(map("schema",2,"original_ready",ready.payload.value,"preinput_current_fit",value));
        return wire==null?null:new Wire(wire);
    }
    private static TreeMap<String,Object> scope(Parent p,int uid,int window,long start) {
        return map("schema",1,"policy_id",POLICY,"phase",PHASE,"parent_intent_id",p.parent,"input_intent_id",p.input,
            "package",APP,"uid",uid,"window",window,"started_uptime_ms",start,
            "raw_UI_or_section_geometry_recorded",false,"automatic_retry",false);
    }
    private static boolean uuid(String value) {return value!=null && value.matches("[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}");}
    private static boolean owner(int uid,int window) {return uid>=10000 && uid<100000 && window>=0 && window<=65535;}
    private static boolean same(Parent a,Parent b) {return a.parent.equals(b.parent) && a.input.equals(b.input);}
    private static boolean rect(int[] value) {
        if(value==null || value.length!=4)return false;
        for(int n:value)if(n< -16384 || n>16384)return false;
        return value[0]<value[2] && value[1]<value[3];
    }
    private static boolean contains(int[] a,int[] b) {return a[0]<=b[0] && a[1]<=b[1] && b[2]<=a[2] && b[3]<=a[3];}
    private static TreeMap<String,Object> map(Object... pairs) {
        TreeMap<String,Object> value=new TreeMap<>();
        for(int i=0;i<pairs.length;i+=2)value.put((String)pairs[i],pairs[i+1]);
        return value;
    }
    private static String digest(byte[] bytes) {
        try {
            byte[] hash=MessageDigest.getInstance("SHA-256").digest(bytes);char[] out=new char[64];
            for(int i=0;i<hash.length;i++) {out[2*i]=Character.forDigit((hash[i]>>>4)&15,16);out[2*i+1]=Character.forDigit(hash[i]&15,16);}
            return new String(out);
        } catch(NoSuchAlgorithmException unavailable) {return null;}
    }
    private static byte[] encode(TreeMap<String,Object> value) {
        StringBuilder out=new StringBuilder();if(!append(out,value))return null;
        byte[] bytes=out.toString().getBytes(StandardCharsets.UTF_8);
        return bytes.length<=4096?bytes:null;
    }
    private static boolean append(StringBuilder out,Object value) {
        if(value==null) {out.append("null");return true;}
        if(value.getClass()==String.class) {
            String text=(String)value;
            for(int i=0;i<text.length();i++)if(text.charAt(i)<32 || text.charAt(i)>126 || text.charAt(i)=='"' || text.charAt(i)=='\\')return false;
            out.append('"').append(text).append('"');return true;
        }
        if(value.getClass()==Integer.class || value.getClass()==Long.class || value.getClass()==Boolean.class) {out.append(value);return true;}
        if(value.getClass()==int[].class) {
            out.append('[');int[] array=(int[])value;
            for(int i=0;i<array.length;i++) {if(i>0)out.append(',');out.append(array[i]);}
            out.append(']');return true;
        }
        if(value.getClass()!=TreeMap.class)return false;
        out.append('{');boolean first=true;
        for(Map.Entry<?,?> entry:((TreeMap<?,?>)value).entrySet()) {
            if(!first)out.append(',');first=false;
            if(!append(out,entry.getKey()))return false;out.append(':');if(!append(out,entry.getValue()))return false;
        }
        out.append('}');return true;
    }
}
