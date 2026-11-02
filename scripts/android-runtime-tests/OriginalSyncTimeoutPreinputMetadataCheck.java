package cc.kronecker.opennexus.testdriver;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;

public final class OriginalSyncTimeoutPreinputMetadataCheck {
    private static int assertions;
    private static final String P="11111111-1111-1111-1111-111111111111",I="22222222-2222-2222-2222-222222222222";
    private static void check(boolean value) {assertions++;if(!value)throw new AssertionError("Closed preinput metadata contract");}
    private static String text(byte[] value) {return new String(value,StandardCharsets.UTF_8);}
    private static String hex(byte[] value) {
        StringBuilder out=new StringBuilder();for(byte n:value) {out.append(Character.forDigit((n>>>4)&15,16));out.append(Character.forDigit(n&15,16));}return out.toString();
    }
    public static void main(String[] args) {
        OriginalSyncTimeoutPreinputMetadata.Parent parent=OriginalSyncTimeoutPreinputMetadata.parent(P,I);
        check(parent!=null);
        check(text(parent.bytes()).equals("{\"input_intent_id\":\""+I+"\",\"native_invocation_id_observed\":false,\"parent_intent_id\":\""+P+"\",\"phase\":\"cycle-timeout-original\",\"schema\":1}"));
        for(String bad:new String[]{null,"","AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA","private/provider/path",P+"\n",P+" ",P+"\u200b"})
            if(!P.equals(bad))check(OriginalSyncTimeoutPreinputMetadata.parent(bad,I)==null);
        check(OriginalSyncTimeoutPreinputMetadata.parent(P,P)==null);
        int[] rect={10,10,20,20};
        OriginalSyncTimeoutPreinputMetadata.Ready ready=OriginalSyncTimeoutPreinputMetadata.ready(parent,12001,7,rect,rect,15,15,2000);
        check(ready!=null);byte[] readyBefore=ready.bytes();rect[0]=11;check(Arrays.equals(readyBefore,ready.bytes()));
        byte[] exposed=ready.bytes();exposed[0]=0;check(Arrays.equals(readyBefore,ready.bytes()));
        for(int[] bad:new int[][]{null,new int[0],new int[]{1,2,3},new int[]{0,0,1,1,2},new int[]{0,0,0,1},new int[]{0,0,16385,1}})
            check(OriginalSyncTimeoutPreinputMetadata.ready(parent,12001,7,bad,new int[]{10,10,20,20},15,15,2000)==null);
        check(OriginalSyncTimeoutPreinputMetadata.ready(parent,9999,7,new int[]{10,10,20,20},new int[]{10,10,20,20},15,15,2000)==null);
        check(OriginalSyncTimeoutPreinputMetadata.ready(parent,12001,65536,new int[]{10,10,20,20},new int[]{10,10,20,20},15,15,2000)==null);
        check(OriginalSyncTimeoutPreinputMetadata.ready(parent,12001,7,new int[]{-5,0,4,2},new int[]{-5,0,4,2},0,1,2000)==null);
        check(OriginalSyncTimeoutPreinputMetadata.ready(parent,12001,7,new int[]{10,10,20,20},new int[]{10,10,20,20},14,15,2000)==null);
        check(OriginalSyncTimeoutPreinputMetadata.ready(parent,12001,7,new int[]{10,10,20,20},new int[]{10,10,20,20},15,15,Long.MAX_VALUE-35999)==null);
        OriginalSyncTimeoutPreinputMetadata.WriteRecord intent=OriginalSyncTimeoutPreinputMetadata.intent(parent,12001,7,1000);
        byte[] buffer=intent.bytes();
        check(OriginalSyncTimeoutPreinputMetadata.reportedWritten(intent,buffer,false)==null);
        check(OriginalSyncTimeoutPreinputMetadata.reportedWritten(intent,buffer,null)==null);
        byte[] damaged=buffer.clone();damaged[0]=' ';
        check(OriginalSyncTimeoutPreinputMetadata.reportedWritten(intent,damaged,true)==null);
        check(OriginalSyncTimeoutPreinputMetadata.reportedWritten(intent,null,true)==null);
        check(OriginalSyncTimeoutPreinputMetadata.reportedWritten(null,buffer,true)==null);
        OriginalSyncTimeoutPreinputMetadata.Written written=OriginalSyncTimeoutPreinputMetadata.reportedWritten(intent,buffer,true);
        check(written!=null);buffer[0]=' ';check(OriginalSyncTimeoutPreinputMetadata.reportedWritten(intent,buffer,true)==null);
        OriginalSyncTimeoutPreinputMetadata.Wire noReveal=OriginalSyncTimeoutPreinputMetadata.project(parent,ready,written,null,null,1600,true,true);
        check(noReveal!=null);check(noReveal.bytes().length<=4096);
        for(Boolean unknown:new Boolean[]{false,null}) {
            check(OriginalSyncTimeoutPreinputMetadata.project(parent,ready,written,null,null,1600,unknown,true)==null);
            check(OriginalSyncTimeoutPreinputMetadata.project(parent,ready,written,null,null,1600,true,unknown)==null);
        }
        OriginalSyncTimeoutPreinputMetadata.WriteRecord attempt=OriginalSyncTimeoutPreinputMetadata.attempt(written);
        check(attempt!=null);
        OriginalSyncTimeoutPreinputMetadata.Written tried=OriginalSyncTimeoutPreinputMetadata.reportedWritten(attempt,attempt.bytes(),true);
        check(tried!=null);check(OriginalSyncTimeoutPreinputMetadata.attempt(tried)==null);
        OriginalSyncTimeoutPreinputMetadata.Wire revealed=OriginalSyncTimeoutPreinputMetadata.project(parent,ready,written,tried,true,1600,true,true);
        check(revealed!=null);
        for(Boolean result:new Boolean[]{false,true})check(OriginalSyncTimeoutPreinputMetadata.project(parent,ready,written,null,result,1600,true,true)==null);
        for(Boolean result:new Boolean[]{false,null})check(OriginalSyncTimeoutPreinputMetadata.project(parent,ready,written,tried,result,1600,true,true)==null);
        for(long end:new long[]{-1,999,1599,6001,Long.MAX_VALUE})check(OriginalSyncTimeoutPreinputMetadata.project(parent,ready,written,null,null,end,true,true)==null);
        check(OriginalSyncTimeoutPreinputMetadata.project(parent,ready,written,null,null,6000,true,true)!=null);
        check(OriginalSyncTimeoutPreinputMetadata.project(parent,ready,tried,null,null,1600,true,true)==null);
        OriginalSyncTimeoutPreinputMetadata.Parent other=OriginalSyncTimeoutPreinputMetadata.parent("33333333-3333-3333-3333-333333333333",I);
        OriginalSyncTimeoutPreinputMetadata.WriteRecord otherIntent=OriginalSyncTimeoutPreinputMetadata.intent(other,12001,7,1000);
        OriginalSyncTimeoutPreinputMetadata.Written otherWritten=OriginalSyncTimeoutPreinputMetadata.reportedWritten(otherIntent,otherIntent.bytes(),true);
        check(OriginalSyncTimeoutPreinputMetadata.project(parent,ready,otherWritten,null,null,1600,true,true)==null);
        check(OriginalSyncTimeoutPreinputMetadata.project(parent,ready,written,
            OriginalSyncTimeoutPreinputMetadata.reportedWritten(OriginalSyncTimeoutPreinputMetadata.attempt(otherWritten),OriginalSyncTimeoutPreinputMetadata.attempt(otherWritten).bytes(),true),true,1600,true,true)==null);
        OriginalSyncTimeoutPreinputMetadata.Ready partial=OriginalSyncTimeoutPreinputMetadata.ready(parent,12001,7,new int[]{0,0,20,20},new int[]{10,10,20,20},15,15,2000);
        check(partial!=null);check(OriginalSyncTimeoutPreinputMetadata.project(parent,partial,written,null,null,1600,true,true)==null);
        check(OriginalSyncTimeoutPreinputMetadata.intent(parent,12001,7,-1)==null);
        check(OriginalSyncTimeoutPreinputMetadata.intent(parent,12001,7,Long.MAX_VALUE-4999)==null);
        long start=Long.MAX_VALUE-5000;
        int[] big={16383,16383,16384,16384};
        OriginalSyncTimeoutPreinputMetadata.Ready maxReady=OriginalSyncTimeoutPreinputMetadata.ready(parent,99999,65535,big,big,16383,16383,Long.MAX_VALUE-36000);
        OriginalSyncTimeoutPreinputMetadata.WriteRecord maxIntent=OriginalSyncTimeoutPreinputMetadata.intent(parent,99999,65535,start);
        OriginalSyncTimeoutPreinputMetadata.Written maxWritten=OriginalSyncTimeoutPreinputMetadata.reportedWritten(maxIntent,maxIntent.bytes(),true);
        OriginalSyncTimeoutPreinputMetadata.WriteRecord maxAttempt=OriginalSyncTimeoutPreinputMetadata.attempt(maxWritten);
        OriginalSyncTimeoutPreinputMetadata.Written maxTried=OriginalSyncTimeoutPreinputMetadata.reportedWritten(maxAttempt,maxAttempt.bytes(),true);
        OriginalSyncTimeoutPreinputMetadata.Wire maxWire=OriginalSyncTimeoutPreinputMetadata.project(parent,maxReady,maxWritten,maxTried,true,Long.MAX_VALUE,true,true);
        check(maxWire!=null);check(maxWire.bytes().length<=4096);
        byte[] returned=noReveal.bytes();returned[0]=0;check(noReveal.bytes()[0]=='{');
        System.out.println("{\"passed\":true,\"assertions\":"+assertions+",\"actual_UI_or_Native_verified\":false,\"largest_fixture_bytes\":"
            +Math.max(maxWire.bytes().length,Math.max(noReveal.bytes().length,revealed.bytes().length))+","
            +"\"parent\":"+text(parent.bytes())+",\"parent_canonical_hex\":\""+hex(parent.bytes())+"\","
            +"\"ready_canonical_hex\":\""+hex(ready.bytes())+"\",\"vectors\":["+text(noReveal.bytes())+","+text(revealed.bytes())+","+text(maxWire.bytes())+"]}");
    }
}
