package cc.kronecker.opennexus

import android.os.Bundle
import android.content.Context
import android.util.Log
import androidx.activity.enableEdgeToEdge

class MainActivity : TauriActivity() {
  private external fun initialize_sync_tls(context: Context)

  override fun onCreate(savedInstanceState: Bundle?) {
    enableEdgeToEdge()
    super.onCreate(savedInstanceState)
    try {
      // Tauri has now loaded our Rust library. Keep only an application context
      // in the verifier, and check its matching JVM component before enabling it.
      Class.forName("org.rustls.platformverifier.CertificateVerifier", false, applicationContext.classLoader)
      initialize_sync_tls(applicationContext)
    } catch (_: RuntimeException) {
      Log.w("OpenNexusSync", "SYNC_TLS_UNAVAILABLE")
    } catch (_: LinkageError) {
      // Offline notes remain usable; native Sync stays unavailable and cannot
      // fall back to an unverified connection or another credential store.
      Log.w("OpenNexusSync", "SYNC_TLS_UNAVAILABLE")
    } catch (_: ClassNotFoundException) {
      Log.w("OpenNexusSync", "SYNC_TLS_UNAVAILABLE")
    }
  }
}
