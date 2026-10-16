package cc.kronecker.opennexus.sync

import android.content.Context

/** Signed instrumentation exercises the Rust worker/JNI path, never the WebView. */
object NativeCredentialProbe {
    init { System.loadLibrary("opennexus_mobile") }
    @JvmStatic external fun roundTrip(context: Context, namespace: String, account: String, device: String, input: ByteArray): ByteArray
}
