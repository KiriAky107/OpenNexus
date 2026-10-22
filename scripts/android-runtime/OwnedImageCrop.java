package cc.kronecker.opennexus.testdriver;

import android.graphics.Bitmap;
import android.graphics.ColorSpace;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.hardware.HardwareBuffer;
import android.os.Build;
import android.os.Process;
import android.os.SystemClock;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.Arrays;
import org.json.JSONArray;
import org.json.JSONObject;

/** Root-owned API36 app_process helper, not product code or an instrumentation entry.
 * Reads one fixed driver ready file and one closed stdin binding. No coordinates,
 * paths, package names, commands or output filenames are accepted on argv/stdin.
 * The future runner must also prove its exact owned AVD and live GUI connection.
 */
public final class OwnedImageCrop {
    // Reflection contract inspected at AOSP frameworks/base android16-release
    // 99b01a65cc4c104933788b3143285ab6bae65827 (WMS preserves nonempty crop).
    // A matching owned API36 runtime must still be tested; no hidden-API exemptions.
    private static final String APP="cc.kronecker.opennexus";
    private static final String READY="/data/user/0/cc.kronecker.opennexus.testdriver/no_backup/owned-sync-image-ready.json";
    private static final String UUID="[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}";
    private static String stage="request";
    private static JSONObject request,ready;
    private static byte[] originalReady;
    private static int pid,uid;
    private OwnedImageCrop() {}

    public static void main(String[] args) {
        try {
            need(args.length==0);
            ByteArrayOutputStream input=new ByteArrayOutputStream();byte[] block=new byte[512];int length;
            while((length=System.in.read(block))!=-1){input.write(block,0,length);need(input.size()<=2048);}
            request=closedJSON(input.toByteArray());
            keys(request,"nonce","boot_id","ready_sha256","pid");
            need(text(request,"nonce").matches(UUID) && text(request,"boot_id").matches(UUID)
                && text(request,"ready_sha256").matches("[0-9a-f]{64}"));pid=integer(request,"pid");
            stage="identity";
            need(Process.myUid()==0 && Build.VERSION.SDK_INT==36 && ("eng".equals(Build.TYPE)||"userdebug".equals(Build.TYPE)));
            need("1".equals(Class.forName("android.os.SystemProperties").getMethod("get",String.class).invoke(null,"ro.kernel.qemu")));
            File file=new File(READY);need(!Files.isSymbolicLink(file.toPath()));
            String canonical=file.getCanonicalPath();
            need(canonical.equals(READY)||canonical.equals(READY.replace("/data/user/0/","/data/data/")));
            need(file.isFile() && file.length()>0 && file.length()<=4096);
            originalReady=Files.readAllBytes(file.toPath());need(hex(originalReady).equals(text(request,"ready_sha256")));
            ready=closedJSON(originalReady);
            keys(ready,"nonce","phase","package","uid","window","display","screen","canvas","image","elapsed_ms","stable_ms",
                "title_verified","exact_image_alt","visible_unobscured","no_keyboard");
            need(text(ready,"nonce").equals(text(request,"nonce")) && text(ready,"exact_image_alt").equals(ReleaseSyncReadScene.ASSET));
            for(String name:new String[]{"title_verified","visible_unobscured","no_keyboard"})need(Boolean.TRUE.equals(ready.get(name)));
            uid=integer(ready,"uid");stage="region";
            OriginalSyncImageRegion.Region region=OriginalSyncImageRegion.admit(text(ready,"package"),text(ready,"phase"),text(ready,"nonce"),
                uid,pid,integer(ready,"window"),integer(ready,"display"),rectangle("screen"),rectangle("canvas"),rectangle("image"),
                number(ready,"elapsed_ms"),SystemClock.elapsedRealtime(),number(ready,"stable_ms"));
            ownerStillCurrent();
            byte[] packet=OriginalSyncImageRegion.capture(region,new CropBackend());
            stage="packet";ownerStillCurrent();System.out.write(packet);System.out.flush();
        } catch(Throwable error) {
            // No exception text/stack, private JSON, AX, full-screen or fallback.
            System.err.println("OWNED_IMAGE_CROP_FAILED stage="+stage+" no_fallback=1");System.exit(1);
        }
    }
    private static final class CropBackend implements OriginalSyncImageRegion.Backend {
        public Object arguments(int[] crop,int appUid) throws Exception {
            stage="arguments";ownerStillCurrent();
            Class<?> builderClass=Class.forName("android.window.ScreenCapture$CaptureArgs$Builder");
            Object builder=builderClass.getConstructor().newInstance();
            builderClass.getMethod("setSourceCrop",Rect.class).invoke(builder,new Rect(crop[0],crop[1],crop[2],crop[3]));
            builderClass.getMethod("setUid",long.class).invoke(builder,(long)appUid);
            builderClass.getMethod("setPixelFormat",int.class).invoke(builder,PixelFormat.RGBA_8888);
            builderClass.getMethod("setFrameScale",float.class).invoke(builder,1f);
            builderClass.getMethod("setCaptureSecureLayers",boolean.class).invoke(builder,false);
            builderClass.getMethod("setAllowProtected",boolean.class).invoke(builder,false);
            return builderClass.getMethod("build").invoke(builder);
        }
        public boolean exactArguments(Object args,int[] crop,int appUid) throws Exception {
            Class<?> type=Class.forName("android.window.ScreenCapture$CaptureArgs");
            Rect actual=(Rect)type.getField("mSourceCrop").get(args);
            return actual.equals(new Rect(crop[0],crop[1],crop[2],crop[3])) && actual.width()==8 && actual.height()==8
                && type.getField("mUid").getLong(args)==appUid && type.getField("mPixelFormat").getInt(args)==PixelFormat.RGBA_8888
                && type.getField("mFrameScaleX").getFloat(args)==1f && type.getField("mFrameScaleY").getFloat(args)==1f
                && !type.getField("mCaptureSecureLayers").getBoolean(args) && !type.getField("mAllowProtected").getBoolean(args)
                && !type.getField("mGrayscale").getBoolean(args);
        }
        public Object capture(Object args) throws Exception {
            stage="capture";ownerStillCurrent();
            Class<?> capture=Class.forName("android.window.ScreenCapture");
            Object listener=capture.getMethod("createSyncCaptureListener").invoke(null);
            Object wm=Class.forName("android.view.WindowManagerGlobal").getMethod("getWindowManagerService").invoke(null);
            Class.forName("android.view.IWindowManager").getMethod("captureDisplay",int.class,
                Class.forName("android.window.ScreenCapture$CaptureArgs"),Class.forName("android.window.ScreenCapture$ScreenCaptureListener"))
                .invoke(wm,0,args,listener);
            Object result=Class.forName("android.window.ScreenCapture$SynchronousScreenCaptureListener").getMethod("getBuffer").invoke(listener);
            need(result!=null);return result;
        }
        public byte[] png(Object result,int width,int height) throws Exception {
            stage="pixels";Class<?> type=Class.forName("android.window.ScreenCapture$ScreenshotHardwareBuffer");
            HardwareBuffer hardware=(HardwareBuffer)type.getMethod("getHardwareBuffer").invoke(result);
            Bitmap wrapper=null,software=null;
            try {
                need(hardware!=null && hardware.getWidth()==width && hardware.getHeight()==height);
                need(!(Boolean)type.getMethod("containsSecureLayers").invoke(result) && !(Boolean)type.getMethod("containsHdrLayers").invoke(result));
                ColorSpace space=(ColorSpace)type.getMethod("getColorSpace").invoke(result);need(space!=null && space.isSrgb());
                wrapper=(Bitmap)type.getMethod("asBitmap").invoke(result);need(wrapper!=null);
                software=wrapper.copy(Bitmap.Config.ARGB_8888,false);need(software!=null && software.getWidth()==8 && software.getHeight()==8);
                ownerStillCurrent();ByteArrayOutputStream bytes=new ByteArrayOutputStream();
                need(software.compress(Bitmap.CompressFormat.PNG,100,bytes) && bytes.size()<=4096);return bytes.toByteArray();
            } finally { if(software!=null)software.recycle();if(wrapper!=null)wrapper.recycle();if(hardware!=null)hardware.close(); }
        }
    }
    private static void ownerStillCurrent() throws Exception {
        need(new String(Files.readAllBytes(new File("/proc/sys/kernel/random/boot_id").toPath()),StandardCharsets.US_ASCII).trim().equals(text(request,"boot_id")));
        need(Arrays.equals(Files.readAllBytes(new File(READY).toPath()),originalReady));
        long age=SystemClock.elapsedRealtime()-number(ready,"elapsed_ms");need(age>=0 && age<=2000);
        byte[] command=Files.readAllBytes(new File("/proc/"+pid+"/cmdline").toPath());
        need(Arrays.equals(command,(APP+"\0").getBytes(StandardCharsets.US_ASCII)));
        String status=new String(Files.readAllBytes(new File("/proc/"+pid+"/status").toPath()),StandardCharsets.US_ASCII);
        int matches=0;for(String line:status.split("\n"))if(line.startsWith("Uid:")) {
            String[] values=line.trim().split("\\s+");need(values.length==5);
            for(int i=1;i<5;i++)need(Integer.parseInt(values[i])==uid);matches++;
        }need(matches==1);
    }
    private static int[] rectangle(String name) throws Exception {
        JSONArray value=ready.getJSONArray(name);need(value.length()==4);int[] result=new int[4];
        for(int i=0;i<4;i++){Object item=value.get(i);need(item instanceof Integer);result[i]=(Integer)item;}return result;
    }
    private static void keys(JSONObject value,String... names) {
        need(value.length()==names.length);for(String name:names)need(value.has(name));
    }
    private static JSONObject closedJSON(byte[] bytes) throws Exception {
        String text=new String(bytes,StandardCharsets.UTF_8);JSONObject value=new JSONObject(text);
        // Own Java ready and future Python stdin use compact canonical JSON;
        // duplicate keys, comments, trailing bytes and coercion are not accepted.
        need(value.toString().equals(text));return value;
    }
    private static String text(JSONObject value,String key) throws Exception { Object v=value.get(key);need(v instanceof String);return (String)v; }
    private static int integer(JSONObject value,String key) throws Exception { Object v=value.get(key);need(v instanceof Integer);return (Integer)v; }
    private static long number(JSONObject value,String key) throws Exception { Object v=value.get(key);need(v instanceof Integer||v instanceof Long);return ((Number)v).longValue(); }
    private static String hex(byte[] value) throws Exception { byte[] hash=MessageDigest.getInstance("SHA-256").digest(value);StringBuilder out=new StringBuilder();for(byte b:hash)out.append(String.format("%02x",b&255));return out.toString(); }
    private static void need(boolean value) { if(!value)throw new IllegalStateException("OWNED_IMAGE_CROP_REFUSED"); }
}
