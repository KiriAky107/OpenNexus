package cc.kronecker.opennexus.testdriver;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashSet;

/** Synthetic complete channels only; no actual Android UI or Native execution. */
public final class ReleaseSyncTimeoutAlertWhitespaceCheck {
    private static int assertions;
    private static void need(boolean value) {
        if(!value)throw new AssertionError("Complete public whitespace diagnostic changed");
        assertions++;
    }
    private static void match(String value,int index,boolean exact) {
        ReleaseSyncTimeoutAlertWhitespace.Match result=ReleaseSyncTimeoutAlertWhitespace.capture(value);
        need(result!=null && result.catalogueIndex==index && result.rawExact==exact);
    }
    private static void reject(String value) {need(ReleaseSyncTimeoutAlertWhitespace.capture(value)==null);}
    private static String quoted(String value) {return "\""+value+"\"";}

    public static void main(String[] args) throws Exception {
        need(args.length==0 && ReleaseSyncTimeoutAlerts.size()==50);
        need("complete-public-alert-ascii-ws-v1".equals(ReleaseSyncTimeoutAlertWhitespace.RULE_ID));
        need(ReleaseSyncTimeoutAlertWhitespace.MAX_CHANNEL_CHARS==512);
        String[] codes=ReleaseSyncTimeoutAlerts.codes();
        HashSet<String> unique=new HashSet<>();StringBuilder catalogue=new StringBuilder("[");
        for(int i=0;i<50;i++) {
            String label=ReleaseSyncTimeoutAlerts.label(i);
            need(unique.add(label) && label.length()<=131);
            if(i>0)catalogue.append(',');
            catalogue.append('[').append(quoted(codes[i])).append(',').append(quoted(label)).append(']');
            match(label,i,true);
            for(String separator:new String[]{" ","\t","\n","\r"," \t\r\n "}) {
                String variant="\r\n"+label.replace(" ",separator)+"\t ";
                match(variant,i,false);
                // Existing exact full-label and strict NETWORK admission is unchanged.
                need(ReleaseSyncTimeoutAlerts.kind(variant)==-1);
                need(ReleaseSyncTimeoutAlerts.strictPair(variant,null)==-1);
            }
            for(int position=0;position<label.length();position++)if(label.charAt(position)==' ') {
                match(label.substring(0,position)+"\t\r\n"+label.substring(position+1),i,false);
                reject(label.substring(0,position)+label.substring(position+1));
            }
            for(String invalid:new String[]{codes[i],ReleaseSyncTimeoutAlerts.bodyLabel(i),
                    label.substring(1),label.substring(0,label.length()-1),label.replace(" ",""),
                    "private-token "+label,label+" private-token",label+"\nprivate-secret=value",
                    label+"\n"+label,label.replace("Last","La st"),
                    label.replace(codes[i],codes[i].substring(0,1)+"\n"+codes[i].substring(1)),
                    label.replace(" · "," "),label.replace(" · "," . "),label.toLowerCase(java.util.Locale.ROOT)})reject(invalid);
            // Two partial channels are never combined into a complete candidate.
            int split=label.indexOf(" · ");
            reject(label.substring(0,split));reject(label.substring(split+3));
        }
        catalogue.append(']');
        byte[] digest=MessageDigest.getInstance("SHA-256").digest(catalogue.toString().getBytes(StandardCharsets.UTF_8));
        StringBuilder hex=new StringBuilder();for(byte value:digest)hex.append(String.format("%02x",value&255));
        need(hex.toString().equals(ReleaseSyncTimeoutAlerts.CATALOG_SHA256));
        String network=ReleaseSyncTimeoutAlerts.label(ReleaseSyncTimeoutAlerts.NETWORK);
        String bound=network+" ".repeat(512-network.length());
        need(bound.length()==512);match(bound,ReleaseSyncTimeoutAlerts.NETWORK,false);reject(bound+" ");
        reject(" ".repeat(512));reject(null);reject("");
        for(int code=0;code<160;code++)if((code<32 && code!=9 && code!=10 && code!=13) || code>=127) {
            String bad=String.valueOf((char)code);
            reject(bad+network);reject(network+bad);reject(network.replace(" · ",bad+"· "));
        }
        for(String bad:new String[]{"\u00a0","\u1680","\u2000","\u2007","\u200b","\u2028","\u2029", "\u202e",
                "\u202f","\u205f","\u2066","\u3000","\ufeff","\ud800","\udc00","\ud83d\ude00","\u0301","\u79c1"}) {
            reject(bad+network);reject(network+bad);reject(network.replace(" · ",bad+"· "));
        }
        reject(ReleaseSyncTimeoutAlerts.headerLabel());reject(ReleaseSyncTimeoutAlerts.detailLabel());
        reject(network.replace("SYNC_NETWORK_ERROR","SYNC_NETWORK_ERROR extra"));
        reject(network.replace("SYNC_NETWORK_ERROR","SYNC_NETWORK_\tERROR"));
        reject(network.replace("(SYNC_NETWORK_ERROR)","(SYNC_NETWORK_ERROR) private"));
        ReleaseSyncTimeoutAlertWhitespace.Match retained=ReleaseSyncTimeoutAlertWhitespace.capture(network);
        reject("another private channel");
        need(retained.catalogueIndex==2 && retained.rawExact);
        Field[] fields=ReleaseSyncTimeoutAlertWhitespace.Match.class.getDeclaredFields();
        need(fields.length==2 && Modifier.isFinal(ReleaseSyncTimeoutAlertWhitespace.Match.class.getModifiers()));
        for(Field field:fields)need(Modifier.isFinal(field.getModifiers()) &&
                (field.getType()==int.class || field.getType()==boolean.class));
        need(ReleaseSyncTimeoutAlerts.kind(network)==2 && ReleaseSyncTimeoutAlerts.strictPair(network,null)==2);
        System.out.println("COMPLETE_PUBLIC_ALERT_WHITESPACE_OK assertions="+assertions+
                " kinds=50 synthetic_channels_only=true integrated=false runtime_verified=false");
    }
}
