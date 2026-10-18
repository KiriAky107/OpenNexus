package cc.kronecker.opennexus.testdriver;

/** Two explicitly owned URI intents. No URL fetching, parsing side effect or execution. */
final class OriginalExternalUri {
    static final String HOST="owned-android-reference.invalid";
    static final String MD="https://"+HOST+"/original-md?literal=%25_%26&utf8=%E4%B8%AD%E6%96%87#original";
    static final String CANVAS="HTTPS://"+HOST+"/original-canvas?literal=%252F&utf8=%F0%9F%98%80#original";
    static final String CANVAS_DELIVERED="https"+CANVAS.substring(5);

    static String key(String action,String data,boolean browsable) {
        if(!"android.intent.action.VIEW".equals(action)||!browsable||data==null||data.length()>4096)
            throw new IllegalArgumentException("OWNED_EXTERNAL_INTENT_REFUSED");
        if(MD.equals(data))return "markdown";
        if(CANVAS_DELIVERED.equals(data))return "canvas";
        throw new IllegalArgumentException("OWNED_EXTERNAL_URI_REFUSED");
    }

    static String original(String key) {
        if("markdown".equals(key))return MD;
        if("canvas".equals(key))return CANVAS;
        throw new IllegalArgumentException("OWNED_EXTERNAL_KEY_REFUSED");
    }

    static String delivered(String key) {
        original(key);return "markdown".equals(key)?MD:CANVAS_DELIVERED;
    }
}
