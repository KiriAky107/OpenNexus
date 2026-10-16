package cc.kronecker.opennexus.sync

import android.util.AtomicFile
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.After
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.security.KeyStore
import java.security.MessageDigest
import java.util.UUID

/** Runs only in an owned Android test installation, using a fresh test namespace. */
class SyncCredentialStoreTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val namespace = "opennexus.sync.test." + UUID.randomUUID()
    private val scope = "{\"endpoint\":\"https://owned.invalid\",\"account\":\"reader\",\"device\":\"device-one\"}"
    private val store = SyncCredentialStore(context, namespace)
    private val secret = "owned-test-access-and-refresh-会话😀".toByteArray(Charsets.UTF_8)
    private fun hash(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it.toInt() and 0xff) }
    private fun record(scope: String) = File(context.noBackupFilesDir,
        "sync-credentials/" + hash(namespace.toByteArray(Charsets.UTF_8)) + "/" + hash((namespace + "\n" + scope).toByteArray(Charsets.UTF_8)) + ".bin")

    @After fun cleanOwnedFixture() {
        val root = record(scope).parentFile!!
        // No enumeration/deletion outside this test's unpredictable namespace.
        if (root.isDirectory) root.listFiles()?.forEach { assertTrue(it.delete()) }
        if (root.exists()) assertTrue(root.delete())
        KeyStore.getInstance("AndroidKeyStore").apply { load(null); if (containsAlias("$namespace.aes-gcm")) deleteEntry("$namespace.aes-gcm") }
        secret.fill(0)
    }

    @Test fun reopensUsingRealKeystoreWithoutPlaintextAndUsesFreshNonce() {
        assertNull(store.read(scope))
        val consumed = secret.copyOf(); store.put(scope, consumed)
        assertTrue(consumed.all { it == 0.toByte() })
        val original = record(scope).readBytes()
        assertFalse(original.toString(Charsets.UTF_8).contains("owned-test-access"))
        val reopened = SyncCredentialStore(context, namespace).read(scope)!!
        try { assertArrayEquals(secret, reopened) } finally { reopened.fill(0) }
        val key = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.getKey("$namespace.aes-gcm", null)
        assertNull(key.encoded)
        store.put(scope, secret.copyOf())
        assertFalse(original.contentEquals(record(scope).readBytes()))
    }

    @Test fun rejectsDeviceAccountEndpointSubstitutionAndTamperedCiphertext() {
        store.put(scope, secret.copyOf()); val original=record(scope).readBytes()
        for (other in listOf(scope.replace("device-one", "device-two"), scope.replace("reader", "other"), scope.replace("owned.invalid", "different.invalid"))) {
            val path=record(other); path.writeBytes(original)
            assertThrows(IllegalStateException::class.java) { store.read(other) }.also { assertEquals("SYNC_SESSION_INVALID",it.message) }
        }
        val corrupt=original.copyOf(); corrupt[corrupt.lastIndex]=(corrupt.last().toInt() xor 1).toByte();record(scope).writeBytes(corrupt)
        assertThrows(IllegalStateException::class.java) { store.read(scope) }.also { assertEquals("SYNC_SESSION_INVALID",it.message) }
        assertArrayEquals(corrupt,record(scope).readBytes()) // No fallback or silent deletion.
    }

    @Test fun failedAtomicReplacementKeepsTheAcknowledgedRecordAndRemoveIsScoped() {
        store.put(scope, secret.copyOf()); val before=record(scope).readBytes()
        val file=AtomicFile(record(scope)); val pending=file.startWrite();pending.write(byteArrayOf(1,2,3));file.failWrite(pending)
        assertArrayEquals(before,record(scope).readBytes())
        val read=store.read(scope)!!;try { assertArrayEquals(secret,read) } finally { read.fill(0) }
        val other=scope.replace("device-one","device-two");store.put(other,secret.copyOf());store.remove(scope)
        assertNull(store.read(scope));assertNotNull(store.read(other)?.also { it.fill(0) })
    }

    @Test fun refusesInvalidOrOversizedInputsAndClearsRejectedSecrets() {
        for (invalidScope in listOf("", "x".repeat(2049), "\ud800")) {
            val input=secret.copyOf()
            assertThrows(IllegalArgumentException::class.java) { store.put(invalidScope,input) }
            assertTrue(input.all { it==0.toByte() })
        }
        val large=ByteArray(65537) { 1 }
        assertThrows(IllegalArgumentException::class.java) { store.put(scope,large) }
        assertTrue(large.all { it==0.toByte() });assertFalse(record(scope).exists())
    }
}
