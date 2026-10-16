package cc.kronecker.opennexus.sync

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import java.io.File
import java.io.FileNotFoundException
import java.security.KeyStore
import java.security.MessageDigest
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Native-only opaque session storage. No WebView command exposes stored bytes.
 * The caller supplies canonical endpoint/account/device AAD and clears read buffers.
 * put consumes and clears its input buffer, including on failure. */
class SyncCredentialStore @JvmOverloads constructor(context: Context, private val namespace: String = "opennexus.sync.v1") {
    private val directory: File
    private val alias: String

    init {
        require(namespace.matches(Regex("opennexus\\.sync\\.[a-zA-Z0-9.-]{1,80}"))) { "SYNC_CREDENTIALS_INVALID" }
        directory = try { File(context.applicationContext.noBackupFilesDir.canonicalFile, "sync-credentials/" + hash(namespace.toByteArray(Charsets.UTF_8))) }
            catch (_: Exception) { throw IllegalStateException("SYNC_CREDENTIALS_UNAVAILABLE") }
        alias = "$namespace.aes-gcm"
    }

    private fun associatedData(scope: String): ByteArray {
        require(scope.isNotBlank() && Charsets.UTF_8.newEncoder().canEncode(scope)) { "SYNC_CREDENTIALS_INVALID" }
        val bytes = (namespace + "\n" + scope).toByteArray(Charsets.UTF_8)
        require(bytes.size <= 2048) { "SYNC_CREDENTIALS_INVALID" }
        return bytes
    }

    private fun record(aad: ByteArray): AtomicFile {
        check((directory.isDirectory || directory.mkdirs()) && directory.canonicalFile == directory.absoluteFile) { "SYNC_CREDENTIALS_UNAVAILABLE" }
        val file = File(directory, hash(aad) + ".bin")
        check(listOf(file, File(file.path + ".bak"), File(file.path + ".new")).all { it.canonicalFile == it.absoluteFile }) { "SYNC_CREDENTIALS_UNAVAILABLE" }
        return AtomicFile(file)
    }

    private fun key(create: Boolean): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val existing = store.getEntry(alias, null)
        if (existing != null) return (existing as? KeyStore.SecretKeyEntry)?.secretKey
            ?: throw IllegalStateException("SYNC_CREDENTIALS_UNAVAILABLE")
        check(create) { "SYNC_CREDENTIALS_UNAVAILABLE" }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256).setRandomizedEncryptionRequired(true).build())
            generateKey()
        }
    }

    private fun ciphertext(file: AtomicFile): ByteArray? = try {
        file.openRead().use { input ->
            val bytes = ByteArray(MAX_RECORD + 1)
            var count = 0
            while (count < bytes.size) {
                val read = input.read(bytes, count, bytes.size - count)
                if (read < 0) break
                count += read
            }
            if (count > MAX_RECORD) throw InvalidRecord()
            bytes.copyOf(count)
        }
    } catch (_: FileNotFoundException) {
        check(!file.baseFile.exists() && !File(file.baseFile.path + ".bak").exists() && !File(file.baseFile.path + ".new").exists()) { "SYNC_CREDENTIALS_UNAVAILABLE" }
        null
    }

    fun put(scope: String, content: ByteArray) {
        try {
            require(content.size in 1..MAX_SECRET) { "SYNC_CREDENTIALS_INVALID" }
            val aad = associatedData(scope)
            try {
                synchronized(lock) {
                    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                    cipher.init(Cipher.ENCRYPT_MODE, key(true)); cipher.updateAAD(aad)
                    check(cipher.iv.size == IV_SIZE) { "SYNC_CREDENTIALS_UNAVAILABLE" }
                    val encrypted = cipher.doFinal(content)
                    val file = record(aad)
                    val output = file.startWrite()
                    val expected = MAGIC + cipher.iv + encrypted
                    try {
                        output.write(expected); output.fd.sync()
                        file.finishWrite(output)
                    } catch (failure: Exception) { file.failWrite(output); throw failure }
                    check(ciphertext(file)?.contentEquals(expected) == true) { "SYNC_CREDENTIALS_UNAVAILABLE" }
                }
            } catch (_: Exception) { throw IllegalStateException("SYNC_CREDENTIALS_UNAVAILABLE") }
        }
        finally { content.fill(0) }
    }

    fun read(scope: String): ByteArray? {
        val aad = associatedData(scope)
        return synchronized(lock) {
            val encoded = try { ciphertext(record(aad)) }
                catch (_: InvalidRecord) { throw IllegalStateException("SYNC_SESSION_INVALID") }
                catch (_: Exception) { throw IllegalStateException("SYNC_CREDENTIALS_UNAVAILABLE") }
                ?: return@synchronized null
            check(encoded.size >= MAGIC.size + IV_SIZE + 17 && encoded.copyOfRange(0, MAGIC.size).contentEquals(MAGIC)) { "SYNC_SESSION_INVALID" }
            try {
                val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                cipher.init(Cipher.DECRYPT_MODE, key(false), GCMParameterSpec(128, encoded, MAGIC.size, IV_SIZE))
                cipher.updateAAD(aad)
                cipher.doFinal(encoded, MAGIC.size + IV_SIZE, encoded.size - MAGIC.size - IV_SIZE)
            } catch (_: AEADBadTagException) { throw IllegalStateException("SYNC_SESSION_INVALID") }
            catch (_: Exception) { throw IllegalStateException("SYNC_CREDENTIALS_UNAVAILABLE") }
        }
    }

    fun remove(scope: String) {
        val aad = associatedData(scope)
        synchronized(lock) {
            try {
                val file = record(aad); file.delete()
                check(listOf(file.baseFile, File(file.baseFile.path + ".bak"), File(file.baseFile.path + ".new")).none { it.exists() }) { "SYNC_CREDENTIALS_UNAVAILABLE" }
            } catch (_: Exception) { throw IllegalStateException("SYNC_CREDENTIALS_UNAVAILABLE") }
        }
    }

    private companion object {
        val lock = Any()
        const val MAX_SECRET = 64 * 1024
        const val IV_SIZE = 12
        val MAGIC = byteArrayOf(0x4f, 0x4e, 0x58, 0x53, 0x01)
        const val MAX_RECORD = 5 + IV_SIZE + MAX_SECRET + 16
        fun hash(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it.toInt() and 0xff) }
    }
    private class InvalidRecord : Exception()
}
