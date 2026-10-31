package cc.kronecker.opennexus.testdriver;

import android.view.accessibility.AccessibilityNodeInfo;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/** Pure closed labels and counted synthetic channels; no actual Android UI. */
public final class ReleaseSyncTimeoutAlertsCheck {
    private static int checks;
    private static void need(boolean value) {if(!value)throw new AssertionError("Closed alert diagnostics changed");checks++;}
    private static String plain(String value) {return value==null?"":value;}
    private static String quoted(String value) {return "\""+value.replace("\\","\\\\").replace("\"","\\\"")+"\"";}
    public static void main(String[] args) throws Exception {
        int size=ReleaseSyncTimeoutAlerts.size();String[] codes=ReleaseSyncTimeoutAlerts.codes();
        need(args.length==size+1 && size==50 && "SYNC_NETWORK_ERROR".equals(codes[ReleaseSyncTimeoutAlerts.NETWORK]));
        StringBuilder json=new StringBuilder("[");
        for(int i=0;i<size;i++) {
            String label=ReleaseSyncTimeoutAlerts.label(i);need(label.equals(args[i+1]));need(ReleaseSyncTimeoutAlerts.kind(label)==i);
            if(i>0)json.append(',');json.append('[').append(quoted(codes[i])).append(',').append(quoted(label)).append(']');
            for(String[] pair:new String[][]{{label,null},{null,label},{label,label}}) {
                ReleaseSyncTimeoutAlerts.Tally tally=new ReleaseSyncTimeoutAlerts.Tally();
                AccessibilityNodeInfo node=new AccessibilityNodeInfo(pair[0],pair[1],false);
                boolean result=ReleaseSyncTimeoutRuntimeProbe.networkAlertNode(node,tally);
                need(result==(i==ReleaseSyncTimeoutAlerts.NETWORK));need(node.textReads==1 && node.descriptionReads==1 && node.editableReads==1);
                int[][] snapshot=tally.snapshot();need(snapshot[2][i]==1 && snapshot[3][0]==0);
                snapshot[2][i]=999;need(tally.snapshot()[2][i]==1);
                AccessibilityNodeInfo editable=new AccessibilityNodeInfo(pair[0],pair[1],true);
                ReleaseSyncTimeoutAlerts.Tally ignored=new ReleaseSyncTimeoutAlerts.Tally();
                need(!ReleaseSyncTimeoutRuntimeProbe.networkAlertNode(editable,ignored));
                need(editable.textReads==0 && editable.descriptionReads==0 && editable.editableReads==1 && ignored.snapshot()[2][i]==0);
            }
            for(String unknown:new String[]{codes[i],label+"\n","prefix "+label,label+" extra","\u79c1\u6709\u5185\u5bb9"})
                need(ReleaseSyncTimeoutAlerts.kind(unknown)==-1);
        }
        json.append(']');byte[] digest=MessageDigest.getInstance("SHA-256").digest(json.toString().getBytes(StandardCharsets.UTF_8));
        StringBuilder hex=new StringBuilder();for(byte b:digest)hex.append(String.format("%02x",b&255));
        need(hex.toString().equals(args[0]) && args[0].equals(ReleaseSyncTimeoutAlerts.CATALOG_SHA256));
        String[] values=new String[size+5];values[0]=null;values[1]="";values[2]="unapproved-private";
        values[3]=ReleaseSyncTimeoutAlerts.label(2)+"\n";values[4]="SYNC_NETWORK_ERROR";
        for(int i=0;i<size;i++)values[i+5]=ReleaseSyncTimeoutAlerts.label(i);
        String network=ReleaseSyncTimeoutAlerts.label(ReleaseSyncTimeoutAlerts.NETWORK);
        for(String a:values)for(String b:values) {
            String first=plain(a),second=plain(b);
            boolean legacy=(network.equals(first)||network.equals(second)) && (first.isEmpty()||network.equals(first)) &&
                (second.isEmpty()||network.equals(second));
            ReleaseSyncTimeoutAlerts.Tally tally=new ReleaseSyncTimeoutAlerts.Tally();
            need(ReleaseSyncTimeoutRuntimeProbe.networkAlertNode(new AccessibilityNodeInfo(a,b,false),tally)==legacy);
            need(tally.snapshot()[2][ReleaseSyncTimeoutAlerts.NETWORK]==(legacy?1:0));
            if(!first.isEmpty() && !second.isEmpty() && !first.equals(second))need(ReleaseSyncTimeoutAlerts.strictPair(a,b)==-1);
        }
        ReleaseSyncTimeoutAlerts.Tally conflict=new ReleaseSyncTimeoutAlerts.Tally();
        for(int i=0;i<7;i++)conflict.record(network,"private occupied channel");
        int[][] data=conflict.snapshot();need(data[0][2]==2 && data[1][2]==0 && data[2][2]==0 && data[3][0]==2);
        codes[0]="private mutation";need(!ReleaseSyncTimeoutAlerts.codes()[0].equals(codes[0]));
        need(ReleaseSyncTimeoutAlerts.kind("x".repeat(513))==-1);
        System.out.println("RELEASE_SYNC_TIMEOUT_ALERT_DIAGNOSTICS_OK checks="+checks+" kinds="+size+" actual_UI_or_Native=0");
    }
}
