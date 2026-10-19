package cc.kronecker.opennexus.testdriver;

import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.SystemClock;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import cc.kronecker.opennexus.testcontract.ImeTargetPolicy;

/** CI-only client for the separate test IME; never part of the product APK. */
final class CompositionIme {
    private static final String PACKAGE = "cc.kronecker.opennexus.testime";
    private static final String PERMISSION = PACKAGE + ".permission.CONTROL";
    private static Context context;
    private static Handler replies;
    private static String targetPackage;

    static void attach(Context source, String packageName) {
        if (!ImeTargetPolicy.allowed(packageName))
            throw new IllegalStateException("Refusing an unrelated test IME attachment");
        context = source;
        targetPackage = packageName;
        HandlerThread thread = new HandlerThread("owned-ime-replies");
        thread.start();
        replies = new Handler(thread.getLooper());
    }

    static void awaitTarget(String packageName) throws Exception {
        long until = SystemClock.uptimeMillis() + 10_000;
        while (SystemClock.uptimeMillis() < until) {
            if (send(packageName, "check", null, -1) == 1) return;
            SystemClock.sleep(100); // Only the read-only binding check may repeat.
        }
        throw new IllegalStateException("The separate test IME was not bound to the owned app editor");
    }

    static void selectEnd(String packageName, int end) throws Exception { edit(packageName, "select", null, end); }
    static void commit(String packageName, String text) throws Exception { edit(packageName, "commit", text, -1); }
    static void compose(String packageName, String text) throws Exception { edit(packageName, "compose", text, -1); }
    static void confirm(String packageName) throws Exception { edit(packageName, "finish", null, -1); }

    private static void edit(String packageName, String operation, String text, int end) throws Exception {
        // A timed-out mutation has an unknown result. Never send it again here.
        if (send(packageName, operation, text, end) != 1)
            throw new IllegalStateException("Separate test IME rejected the original operation; no retry");
    }

    private static int send(String packageName, String operation, String text, int end) throws Exception {
        if (context == null || !ImeTargetPolicy.matches(targetPackage, packageName))
            throw new IllegalStateException("Refusing an unrelated test IME target");
        String id = UUID.randomUUID().toString();
        Intent intent = new Intent(PACKAGE + ".CONTROL")
            .setComponent(new ComponentName(PACKAGE, PACKAGE + ".ImeControlReceiver"))
            .putExtra("id", id).putExtra("target", packageName).putExtra("operation", operation).putExtra("end", end);
        if (text != null) intent.putExtra("text", text);
        CountDownLatch completed = new CountDownLatch(1);
        AtomicInteger code = new AtomicInteger(-1);
        context.sendOrderedBroadcast(intent, PERMISSION, new BroadcastReceiver() {
            @Override public void onReceive(Context ignored, Intent original) {
                if (("owned-ime:" + id).equals(getResultData())) code.set(getResultCode());
                completed.countDown();
            }
        }, replies, -1, null, null);
        if (!completed.await(5, TimeUnit.SECONDS))
            throw new IllegalStateException("Separate test IME acknowledgement timed out; original result unknown, no retry");
        if (code.get() < 0) throw new IllegalStateException("Separate test IME reply was not bound to the original request");
        return code.get();
    }
}
