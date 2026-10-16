package cc.kronecker.opennexus.testdriver;

import android.inputmethodservice.InputMethodService;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.widget.TextView;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Test-only real Android IME; never packaged in the OpenNexus product APK. */
public final class CompositionIme extends InputMethodService {
    private static volatile CompositionIme active;
    private interface Edit { boolean apply(InputConnection connection); }

    @Override public void onCreate() { super.onCreate(); active = this; }
    @Override public void onDestroy() { if (active == this) active = null; super.onDestroy(); }
    @Override public boolean onEvaluateInputViewShown() { return true; }
    @Override public View onCreateInputView() {
        TextView view = new TextView(this);
        view.setText("OpenNexus CI Chinese composition");
        view.setTextSize(16);
        view.setPadding(20, 20, 20, 20);
        view.setMinimumHeight((int) (100 * getResources().getDisplayMetrics().density));
        return view;
    }

    static void awaitTarget(String packageName) throws Exception {
        long until = SystemClock.uptimeMillis() + 10_000;
        while (SystemClock.uptimeMillis() < until) {
            try { edit(packageName, connection -> true); return; }
            catch (IllegalStateException unavailable) { SystemClock.sleep(100); }
        }
        throw new IllegalStateException("The test IME was not bound to the owned app editor");
    }

    static void selectEnd(String packageName, int end) throws Exception {
        edit(packageName, connection -> connection.setSelection(end, end));
    }
    static void compose(String packageName, String text) throws Exception {
        edit(packageName, connection -> connection.setComposingText(text, 1));
    }
    static void confirm(String packageName) throws Exception {
        edit(packageName, InputConnection::finishComposingText);
    }

    private static void edit(String packageName, Edit operation) throws Exception {
        CompositionIme ime = active;
        if (ime == null) throw new IllegalStateException("Test IME has not started");
        AtomicReference<Throwable> failure = new AtomicReference<>();
        CountDownLatch completed = new CountDownLatch(1);
        new Handler(Looper.getMainLooper()).post(() -> {
            try {
                EditorInfo editor = ime.getCurrentInputEditorInfo();
                InputConnection connection = ime.getCurrentInputConnection();
                if (active != ime || editor == null || !packageName.equals(editor.packageName) || connection == null) {
                    throw new IllegalStateException("Refusing an IME operation on an unrelated or missing editor");
                }
                if (!operation.apply(connection)) throw new IllegalStateException("InputConnection rejected the IME operation");
            } catch (Throwable error) { failure.set(error); }
            finally { completed.countDown(); }
        });
        if (!completed.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("Test IME acknowledgement timed out");
        if (failure.get() != null) throw new IllegalStateException("Test IME operation failed", failure.get());
    }
}
