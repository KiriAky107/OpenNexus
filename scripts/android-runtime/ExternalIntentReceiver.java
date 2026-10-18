package cc.kronecker.opennexus.testdriver;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.os.SystemClock;
import android.widget.TextView;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import org.json.JSONObject;

/** Test-only reserved-domain URI sink. It never loads a URL or reads product data. */
public final class ExternalIntentReceiver extends Activity {
    @Override public void onCreate(Bundle state){super.onCreate(state);receive(getIntent());}
    @Override public void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);receive(intent);}

    private void receive(Intent intent) {
        try {
            String data=intent.getDataString();
            String key=OriginalExternalUri.key(intent.getAction(),data,intent.hasCategory(Intent.CATEGORY_BROWSABLE));
            File root=getFilesDir(),armed=new File(root,"owned-external-"+key+".armed.json");
            if(!armed.isFile()||armed.length()<=0||armed.length()>4096)throw new IllegalStateException("OWNED_EXTERNAL_NOT_ARMED");
            JSONObject original=new JSONObject(new String(Files.readAllBytes(armed.toPath()),StandardCharsets.UTF_8));
            String operation=original.getString("operation_id");
            if(original.length()!=6||original.getInt("schema")!=1||!original.getString("scope").equals("external-links")
                ||!original.getString("key").equals(key)||!operation.matches("[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
                ||!original.getString("original_uri").equals(OriginalExternalUri.original(key))
                ||!original.getString("expected_delivered_uri").equals(OriginalExternalUri.delivered(key)))
                throw new IllegalStateException("OWNED_EXTERNAL_INTENT_CHANGED");
            File record=new File(root,"owned-external-"+key+".received.json");
            JSONObject value=new JSONObject().put("schema",1).put("scope","external-links").put("key",key)
                .put("operation_id",operation).put("actual_action",intent.getAction()).put("actual_browsable",true)
                .put("actual_data_uri",data).put("receiver_package",getPackageName()).put("elapsed_realtime_ms",SystemClock.elapsedRealtime())
                .put("original_uri",original.getString("original_uri")).put("browser_or_network_or_physical_verified",false);
            File claim=new File(root,"owned-external-"+key+".delivery.claim"),pending=new File(root,"owned-external-"+key+".received.pending");
            if(record.exists()||!claim.createNewFile()||!pending.createNewFile())throw new IllegalStateException("OWNED_EXTERNAL_DUPLICATE_DELIVERY");
            try(FileOutputStream output=new FileOutputStream(pending)){output.write((value.toString()+"\n").getBytes(StandardCharsets.UTF_8));output.flush();output.getFD().sync();}
            // Default move refuses an existing target; expose only the flushed complete record.
            Files.move(pending.toPath(),record.toPath());
            TextView text=new TextView(this);text.setText("Owned external URI recorded");setContentView(text);
        } catch(Exception error) {
            try {
                File marker=new File(getFilesDir(),"owned-external-receiver-error.json");
                if(marker.createNewFile())try(FileOutputStream output=new FileOutputStream(marker)){
                    output.write((new JSONObject().put("schema",1).put("error_class",error.getClass().getSimpleName())
                        .put("no_product_file_or_network_access",true).toString()+"\n").getBytes(StandardCharsets.UTF_8));
                    output.flush();output.getFD().sync();
                }
            }catch(Exception ignored){}
        } finally {finish();}
    }
}
