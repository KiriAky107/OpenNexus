package cc.kronecker.opennexus.testdriver;

import android.view.accessibility.AccessibilityNodeInfo;

/** Original node reads, synthetic channels and optional counters only. */
public final class ReleaseSyncTimeoutAlertWhitespaceIntegrationCheck {
    private static int assertions;
    private static void need(boolean value) {
        if(!value)throw new AssertionError("Same-read whitespace diagnostics changed");
        assertions++;
    }
    private static int sum(int[] values) {int n=0;for(int value:values)n+=value;return n;}
    private static String variant(String label) {return "\t"+label.replace(" ","\r\n ")+"\r";}
    private static int[][] record(String a,String b,boolean editable,boolean expected) {
        ReleaseSyncTimeoutAlerts.Tally tally=new ReleaseSyncTimeoutAlerts.Tally();
        AccessibilityNodeInfo node=new AccessibilityNodeInfo(a,b,editable);
        need(ReleaseSyncTimeoutRuntimeProbe.networkAlertNode(node,tally)==expected);
        need(node.editableReads==1 && node.textReads==(editable?0:1) && node.descriptionReads==(editable?0:1));
        return tally.snapshot();
    }
    public static void main(String[] args) {
        need(args.length==0 && ReleaseSyncTimeoutAlerts.size()==50);
        for(int i=0;i<50;i++) {
            String raw=ReleaseSyncTimeoutAlerts.label(i),changed=variant(raw);
            for(String[] pair:new String[][]{{changed,null},{null,changed},{changed,changed}}) {
                int[][] data=record(pair[0],pair[1],false,false);
                need(data.length==10 && data[8][i]==(pair[0]==null?0:1) && data[9][i]==(pair[1]==null?0:1));
                need(sum(data[0])+sum(data[1])+sum(data[2])==0);
                int[][] excluded=record(pair[0],pair[1],true,false);
                for(int[] row:excluded)need(sum(row)==0);
            }
            int[][] exact=record(raw,raw,false,i==ReleaseSyncTimeoutAlerts.NETWORK);
            need(exact[0][i]==1 && exact[1][i]==1 && exact[2][i]==1 && sum(exact[8])+sum(exact[9])==0);
            int[][] conflict=record(changed,"private extra channel",false,false);
            need(conflict[8][i]==1 && sum(conflict[9])==0 && sum(conflict[2])==0);
            ReleaseSyncTimeoutAlerts.Tally saturated=new ReleaseSyncTimeoutAlerts.Tally();
            for(int n=0;n<7;n++)saturated.record(changed,changed);
            int[][] copy=saturated.snapshot();need(copy[8][i]==2 && copy[9][i]==2 && copy[2][i]==0);
            for(int[] row:copy)for(int n=0;n<row.length;n++)row[n]=99;
            int[][] original=saturated.snapshot();need(original[8][i]==2 && original[9][i]==2 && original[2][i]==0);
            for(String bad:new String[]{changed+" private", "private "+changed,changed+changed,
                    raw+"\u00a0",raw+"\u0000",raw+"\u202e",raw+" ".repeat(513),ReleaseSyncTimeoutAlerts.codes()[i]}) {
                int[][] data=record(bad,null,false,false);need(sum(data[8])+sum(data[9])==0);
            }
            int split=raw.indexOf(" · ");
            int[][] pieces=record(raw.substring(0,split),raw.substring(split+3),false,false);
            need(sum(pieces[8])+sum(pieces[9])+sum(pieces[2])==0);
        }
        String network=ReleaseSyncTimeoutAlerts.label(ReleaseSyncTimeoutAlerts.NETWORK);
        String[] values=new String[105];values[0]=null;values[1]="";values[2]="private";
        values[3]=ReleaseSyncTimeoutAlerts.bodyLabel(2);values[4]=network+"\u000b";
        for(int i=0;i<50;i++){values[5+i]=ReleaseSyncTimeoutAlerts.label(i);values[55+i]=variant(values[5+i]);}
        for(String first:values)for(String second:values) {
            String a=first==null?"":first,b=second==null?"":second;
            boolean exact=(network.equals(a)||network.equals(b)) && (a.isEmpty()||network.equals(a)) && (b.isEmpty()||network.equals(b));
            int[][] data=record(first,second,false,exact);
            need(data[2][2]==(exact?1:0));
        }
        need(!OriginalSyncTimeoutActions.failedScene(1,1,1,0,0,0,0,0));
        System.out.println("SAME_READ_PUBLIC_ALERT_WHITESPACE_OK assertions="+assertions+
            " kinds=50 extra_UI_reads=0 exact_NETWORK_unchanged=true actual_Android_UI_or_Native=0");
    }
}
