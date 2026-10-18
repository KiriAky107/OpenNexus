package cc.kronecker.opennexus.testdriver;
public final class OriginalExternalUriCheck {
    private static int count;
    private static void need(boolean good){if(!good)throw new AssertionError("OWNED_URI_CONTRACT_FAILED");count++;}
    private static void refused(String action,String uri,boolean browsable){
        try{OriginalExternalUri.key(action,uri,browsable);throw new AssertionError("FOREIGN_URI_ACCEPTED");}
        catch(IllegalArgumentException expected){count++;}
    }
    public static void main(String[] args){
        need(OriginalExternalUri.key("android.intent.action.VIEW",OriginalExternalUri.MD,true).equals("markdown"));
        need(OriginalExternalUri.key("android.intent.action.VIEW",OriginalExternalUri.CANVAS_DELIVERED,true).equals("canvas"));
        need(OriginalExternalUri.original("canvas").startsWith("HTTPS://"));
        need(OriginalExternalUri.delivered("canvas").equals("https"+OriginalExternalUri.original("canvas").substring(5)));
        need(OriginalExternalUri.delivered("canvas").endsWith("?literal=%252F&utf8=%F0%9F%98%80#original"));
        for(String uri:new String[]{null,"",OriginalExternalUri.CANVAS,OriginalExternalUri.MD+" ",OriginalExternalUri.MD+"extra",
            OriginalExternalUri.MD.replace("https:","file:"),OriginalExternalUri.MD.replace("https:","javascript:"),
            OriginalExternalUri.MD.replace("/original-md","/other"),OriginalExternalUri.MD.replace(OriginalExternalUri.HOST,"localhost"),
            OriginalExternalUri.MD.replace(OriginalExternalUri.HOST,"user:secret@"+OriginalExternalUri.HOST),new String(new char[4097]).replace('\0','x')})
            refused("android.intent.action.VIEW",uri,true);
        refused("android.intent.action.SEND",OriginalExternalUri.MD,true);refused(null,OriginalExternalUri.MD,true);
        refused("android.intent.action.VIEW",OriginalExternalUri.MD,false);
        try{OriginalExternalUri.original("other");throw new AssertionError();}catch(IllegalArgumentException expected){count++;}
        System.out.println("OWNED_EXTERNAL_URI_CONTRACT_OK "+count+" no_OS_dispatch_or_network_claim=1");
        System.out.println("ANDROID_DOMAIN_STATES "+android.content.pm.verify.domain.DomainVerificationUserState.DOMAIN_STATE_NONE
            +" "+android.content.pm.verify.domain.DomainVerificationUserState.DOMAIN_STATE_SELECTED
            +" "+android.content.pm.verify.domain.DomainVerificationUserState.DOMAIN_STATE_VERIFIED);
        System.out.println("ANDROID_MATCH_DEFAULT_ONLY "+android.content.pm.PackageManager.MATCH_DEFAULT_ONLY);
    }
}
