package cc.kronecker.opennexus.testime;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import java.util.LinkedHashMap;
import java.util.UUID;
import cc.kronecker.opennexus.testcontract.ImeTargetPolicy;

/** Explicit, signature-protected and bounded CI commands; no text is logged or returned. */
public final class ImeControlReceiver extends BroadcastReceiver {
    private static final LinkedHashMap<String, Boolean> seen = new LinkedHashMap<>();

    @Override public void onReceive(Context context, Intent intent) {
        setResultCode(0);
        String id = intent.getStringExtra("id");
        if (id == null || !canonical(id)) return;
        setResultData("owned-ime:" + id);
        String operation = intent.getStringExtra("operation"), text = intent.getStringExtra("text");
        String target = intent.getStringExtra("target");
        int end = intent.getIntExtra("end", -1);
        if (!"cc.kronecker.opennexus.testime.CONTROL".equals(intent.getAction())
                || !ImeTargetPolicy.allowed(target)
                || !valid(operation, text, end) || seen.containsKey(id)) return;
        seen.put(id, true);
        if (seen.size() > 256) seen.remove(seen.keySet().iterator().next());
        CompositionInputMethod ime = CompositionInputMethod.active;
        if (ime != null && ime.execute(target, operation, text, end)) setResultCode(1);
    }

    private static boolean canonical(String id) {
        try { return UUID.fromString(id).toString().equals(id); }
        catch (IllegalArgumentException invalid) { return false; }
    }

    private static boolean valid(String operation, String text, int end) {
        if (operation == null) return false;
        switch (operation) {
            case "check": case "finish": return text == null && end == -1;
            case "select": return text == null && end >= 0 && end <= 65536;
            case "compose": case "commit": return end == -1 && text != null
                && text.length() > 0 && text.length() <= 32768 && text.indexOf('\0') < 0;
            default: return false;
        }
    }
}
