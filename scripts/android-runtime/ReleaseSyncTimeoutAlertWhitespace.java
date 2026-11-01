package cc.kronecker.opennexus.testdriver;

/** Authored complete-channel diagnostic equivalence, not a WebView or admission rule. */
final class ReleaseSyncTimeoutAlertWhitespace {
    static final String RULE_ID="complete-public-alert-ascii-ws-v1";
    static final int MAX_CHANNEL_CHARS=512;

    static final class Match {
        final int catalogueIndex;
        final boolean rawExact;
        private Match(int index,boolean exact) {catalogueIndex=index;rawExact=exact;}
    }

    private ReleaseSyncTimeoutAlertWhitespace() {}

    static Match capture(String channel) {
        if(channel==null || channel.length()>MAX_CHANNEL_CHARS || ReleaseSyncTimeoutAlerts.size()!=50)return null;
        for(int i=0;i<channel.length();i++) {
            char value=channel.charAt(i);
            if(!space(value) && (value<0x20 || value>0x7e) && value!='\u00b7')return null;
        }
        int found=-1;
        for(int i=0;i<ReleaseSyncTimeoutAlerts.size();i++) {
            if(sameProjection(ReleaseSyncTimeoutAlerts.label(i),channel)) {
                if(found>=0)return null;
                found=i;
            }
        }
        return found<0?null:new Match(found,ReleaseSyncTimeoutAlerts.label(found).equals(channel));
    }

    private static boolean space(char value) {
        return value==' ' || value=='\t' || value=='\n' || value=='\r';
    }
    private static int start(String value) {
        int index=0;
        while(index<value.length() && space(value.charAt(index)))index++;
        return index;
    }
    private static int end(String value) {
        int index=value.length();
        while(index>0 && space(value.charAt(index-1)))index--;
        return index;
    }
    private static boolean sameProjection(String candidate,String channel) {
        int left=start(candidate),right=start(channel),leftEnd=end(candidate),rightEnd=end(channel);
        while(left<leftEnd && right<rightEnd) {
            char a=candidate.charAt(left),b=channel.charAt(right);
            if(space(a) || space(b)) {
                if(!space(a) || !space(b))return false;
                while(left<leftEnd && space(candidate.charAt(left)))left++;
                while(right<rightEnd && space(channel.charAt(right)))right++;
            } else {
                if(a!=b)return false;
                left++;right++;
            }
        }
        return left==leftEnd && right==rightEnd && leftEnd>0;
    }
}
