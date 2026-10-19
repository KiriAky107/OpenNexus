package cc.kronecker.opennexus.testime;

import android.inputmethodservice.InputMethodService;
import android.text.InputType;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.widget.TextView;
import cc.kronecker.opennexus.testcontract.ImeTargetPolicy;

/** Real CI InputConnection in a UID that am instrument cannot force-stop. */
public final class CompositionInputMethod extends InputMethodService {
    static volatile CompositionInputMethod active;
    private boolean started;

    @Override public void onCreate() { super.onCreate(); active = this; }
    @Override public void onDestroy() { if (active == this) active = null; started = false; super.onDestroy(); }
    @Override public void onStartInput(EditorInfo info, boolean restarting) { super.onStartInput(info, restarting); started = true; }
    @Override public void onFinishInput() { started = false; super.onFinishInput(); }
    @Override public boolean onEvaluateInputViewShown() { return true; }
    @Override public View onCreateInputView() {
        TextView view = new TextView(this);
        view.setText("OpenNexus CI Chinese composition");
        view.setTextSize(16); view.setPadding(20, 20, 20, 20);
        view.setMinimumHeight((int) (100 * getResources().getDisplayMetrics().density));
        return view;
    }

    boolean execute(String target, String operation, String text, int end) {
        EditorInfo info = getCurrentInputEditorInfo();
        InputConnection connection = getCurrentInputConnection();
        if (active != this || !started || info == null || connection == null
                || !ImeTargetPolicy.matches(target, info.packageName) || password(info.inputType)) return false;
        switch (operation) {
            case "check": return true;
            case "select": return connection.setSelection(end, end);
            case "commit": return connection.commitText(text, 1);
            case "compose": return connection.setComposingText(text, 1);
            case "finish": return connection.finishComposingText();
            default: return false;
        }
    }

    private static boolean password(int type) {
        int kind = type & InputType.TYPE_MASK_CLASS, variation = type & InputType.TYPE_MASK_VARIATION;
        return kind == InputType.TYPE_CLASS_TEXT && (variation == InputType.TYPE_TEXT_VARIATION_PASSWORD
            || variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD || variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD)
            || kind == InputType.TYPE_CLASS_NUMBER && variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD;
    }
}
