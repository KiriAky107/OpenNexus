package cc.kronecker.opennexus

import android.os.Bundle
import android.content.Context
import android.util.Log
import androidx.activity.enableEdgeToEdge

class MainActivity : TauriActivity() {
  private external fun initializeSyncTls(context: Context)
  private external fun syncForegroundChanged(active: Boolean)

  override fun onStart() {
    super.onStart()
    try {
      syncForegroundChanged(true)
    } catch (_: LinkageError) {
      Log.w("OpenNexusSync", "SYNC_FOREGROUND_UNAVAILABLE")
    }
  }

  override fun onStop() {
    try {
      // Cancel native work before delegating the transition. JS visibility and
      // process destructors cannot provide this ordering on Android.
      syncForegroundChanged(false)
    } catch (_: LinkageError) {
      Log.w("OpenNexusSync", "SYNC_FOREGROUND_UNAVAILABLE")
    } finally {
      super.onStop()
    }
  }

  override fun onCreate(savedInstanceState: Bundle?) {
    enableEdgeToEdge()
    super.onCreate(savedInstanceState)
    MobileWindowInsets.install(findViewById(android.R.id.content))
    try {
      // Tauri has now loaded our Rust library. Keep only an application context
      // in the verifier, and check its matching JVM component before enabling it.
      Class.forName("org.rustls.platformverifier.CertificateVerifier", false, applicationContext.classLoader)
      initializeSyncTls(applicationContext)
      Log.i("OpenNexusSync", "SYNC_TLS_READY")
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
