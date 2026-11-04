package cc.kronecker.opennexus.testdriver;

import java.util.Arrays;

public final class ReleaseSyncQuotaSceneCheck {
    private static int assertions;
    private static void check(boolean value) {assertions++;if(!value)throw new AssertionError("Quota public scene contract");}
    public static void main(String[] args) {
        String full="Last recorded sync error \u00b7 Remote storage quota is insufficient. Adjust it or clean up remote history, then retry. (QUOTA_EXCEEDED)";
        String body="Remote storage quota is insufficient. Adjust it or clean up remote history, then retry. (QUOTA_EXCEEDED)";
        check(ReleaseSyncQuotaScene.QUOTA.equals(full));
        check(ReleaseSyncQuotaScene.RULE.equals("complete-public-quota-alert-exact-v1"));
        String[] channels={null,"",full,body,"QUOTA_EXCEEDED","Last recorded sync error",
            full+" ","\n"+full,"\u00a0"+full,full.replace('\u00b7','.')};
        for(int i=0;i<channels.length;i++)for(int j=0;j<channels.length;j++)
            check(ReleaseSyncQuotaScene.strictPair(channels[i],channels[j])==((i==2 || j==2) && i<=2 && j<=2));
        for(int i=0;i<full.length();i++) {
            String changed=full.substring(0,i)+'\ufffd'+full.substring(i+1);
            check(!ReleaseSyncQuotaScene.strictPair(changed,null));
            check(!ReleaseSyncQuotaScene.strictPair(null,changed));
            check(!ReleaseSyncQuotaScene.strictPair(full,changed));
            check(!ReleaseSyncQuotaScene.strictPair(changed,full));
        }
        for(String extra:new String[]{" ","\t","\n","\r","\0","\u00a0","\u200b","private-content"}) {
            check(!ReleaseSyncQuotaScene.strictPair(full+extra,null));
            check(!ReleaseSyncQuotaScene.strictPair(extra+full,null));
            check(!ReleaseSyncQuotaScene.strictPair(full,extra));
        }
        // Neither header/body in two channels nor two separate calls creates a warning.
        check(!ReleaseSyncQuotaScene.strictPair("Last recorded sync error",body));
        check(!ReleaseSyncQuotaScene.strictPair("Last recorded sync error",null));
        check(!ReleaseSyncQuotaScene.strictPair(body,null));
        int[] expected={1,1,1,1,0,0,0,0};
        check(ReleaseSyncQuotaScene.failed(expected));
        int[] original=expected.clone();
        for(int slot=0;slot<8;slot++)for(int n:new int[]{-1,0,1,2,Integer.MAX_VALUE}) {
            int[] value=expected.clone();value[slot]=n;
            check(ReleaseSyncQuotaScene.failed(value)==(n==expected[slot]));
        }
        check(Arrays.equals(expected,original));
        check(!ReleaseSyncQuotaScene.failed(null));
        check(!ReleaseSyncQuotaScene.failed(new int[7]));
        check(!ReleaseSyncQuotaScene.failed(new int[9]));
        check(!ReleaseSyncQuotaScene.failed(new int[8]));
        System.out.println("QUOTA_SCENE_CHECK_OK assertions="+assertions);
    }
}
