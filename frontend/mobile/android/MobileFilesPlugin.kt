package cc.kronecker.opennexus.files

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.util.AtomicFile
import androidx.activity.result.ActivityResult
import app.tauri.annotation.ActivityCallback
import app.tauri.annotation.Command
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.Executors

/** Only native-issued sealed tickets and explicitly chosen SAF documents are accessible. */
@TauriPlugin
class MobileFilesPlugin(private val owner: Activity) : Plugin(owner) {
    private val worker = Executors.newSingleThreadExecutor()
    private val gate = Any()
    private var active: String? = null
    override fun onDestroy() { worker.shutdown() }
    private data class Ticket(val id: String, val file: File, val sha: String, val size: Long, val filename: String)

    private fun root(): File {
        val privateRoot = File(owner.applicationInfo.dataDir).canonicalFile
        val root = File(privateRoot, "exports").canonicalFile
        if (root.parentFile != privateRoot || !root.isDirectory) throw IOException("MOBILE_EXPORT_INVALID")
        return root
    }
    private fun uuid(value: String): String {
        if (UUID.fromString(value).toString() != value) throw IOException("MOBILE_EXPORT_INVALID")
        return value
    }
    private fun file(name: String): File {
        val file = File(root(), name)
        if (file.canonicalFile != file.absoluteFile) throw IOException("MOBILE_EXPORT_INVALID")
        return file
    }
    private fun read(name: String): JSONObject {
        val source = file(name)
        if (source.length() > 8192) throw IOException("MOBILE_EXPORT_INVALID")
        return JSONObject(source.readText(Charsets.UTF_8))
    }
    private fun write(name: String, value: JSONObject) {
        val atomic = AtomicFile(file(name))
        val stream = atomic.startWrite()
        try { stream.write(value.toString().toByteArray(Charsets.UTF_8)); atomic.finishWrite(stream) }
        catch (error: Exception) { atomic.failWrite(stream); throw error }
    }
    private fun digest(input: java.io.InputStream): Pair<String,Long> {
        val digest = MessageDigest.getInstance("SHA-256")
        var size = 0L
        val buffer = ByteArray(65536)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            size += count
            if (size > 264L * 1024 * 1024) throw IOException("MOBILE_EXPORT_TOO_LARGE")
            digest.update(buffer, 0, count)
        }
        return Pair(digest.digest().joinToString("") { (it.toInt() and 255).toString(16).padStart(2,'0') },size)
    }
    private fun ticket(args: JSObject): Ticket {
        val id = uuid(args.getString("ticket"))
        val metadata = read("$id.json")
        val name = "OpenNexus-content-$id.zip"
        val sha = metadata.getString("sha256")
        val size = metadata.getLong("size")
        if (metadata.getString("ticket") != id || metadata.getString("filename") != name ||
            !sha.matches(Regex("[0-9a-f]{64}")) || size < 0 || size > 264L * 1024 * 1024) throw IOException("MOBILE_EXPORT_INVALID")
        val source = file("$id.zip")
        if (!source.isFile || source.length() != size) throw IOException("MOBILE_EXPORT_CHANGED")
        return Ticket(id,source,sha,size,name)
    }
    private fun deliveryName(ticket: String, delivery: String) = "$ticket.${uuid(delivery)}.delivery.json"
    private fun publicResult(value: JSONObject): JSObject {
        val result = JSObject()
        for (key in listOf("ticket","delivery_id","state","sha256","size","verified_at","error")) {
            if (value.has(key)) result.put(key,value.get(key))
        }
        return result
    }
    private fun record(ticket: Ticket, value: JSONObject) { write(deliveryName(ticket.id,value.getString("delivery_id")),value) }

    @Command
    fun saveExport(invoke: Invoke) {
        try {
            val ticket = ticket(invoke.getArgs())
            val delivery = UUID.randomUUID().toString()
            synchronized(gate) {
                if (active != null) { invoke.reject("MOBILE_EXPORT_BUSY"); return }
                active = delivery
            }
            val intent = JSONObject().put("ticket",ticket.id).put("delivery_id",delivery).put("state","choosing")
                .put("sha256",ticket.sha).put("size",ticket.size)
            record(ticket,intent)
            write("${ticket.id}.current.json",JSONObject().put("delivery_id",delivery))
            val chooser = Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                .setType("application/zip").putExtra(Intent.EXTRA_TITLE,ticket.filename)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
            startActivityForResult(invoke,chooser,"exportChosen")
        } catch (_: Exception) {
            synchronized(gate) { active = null }
            invoke.reject("MOBILE_EXPORT_PICKER_FAILED")
        }
    }

    @ActivityCallback
    private fun exportChosen(invoke: Invoke, result: ActivityResult) {
        try {
            val ticket = ticket(invoke.getArgs())
            val delivery = synchronized(gate) { active } ?: throw IOException("MOBILE_EXPORT_INVALID")
            val intent = read(deliveryName(ticket.id,delivery))
            val uri = result.data?.data
            if (result.resultCode != Activity.RESULT_OK || uri == null) {
                intent.put("state","cancelled");record(ticket,intent)
                synchronized(gate) { active = null };invoke.resolve(publicResult(intent));return
            }
            if (uri.scheme != "content") throw IOException("MOBILE_EXPORT_INVALID")
            val flags = (result.data?.flags ?: 0) and (Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
            if (flags != 0 && ((result.data?.flags ?: 0) and Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION) != 0) {
                try { owner.contentResolver.takePersistableUriPermission(uri,flags) } catch (_: Exception) { /* Some providers grant this document only for the current process. */ }
            }
            // The private URI is frozen before the first output byte. It never reaches JavaScript or the ZIP.
            intent.put("uri",uri.toString()).put("state","writing");record(ticket,intent)
            worker.execute {
                try {
                    val source = ticket.file.inputStream().use { digest(it) }
                    if (source.first != ticket.sha || source.second != ticket.size) throw IOException("MOBILE_EXPORT_CHANGED")
                    val output = owner.contentResolver.openOutputStream(uri,"wt") ?: throw IOException("MOBILE_EXPORT_WRITE_FAILED")
                    output.use { destination -> ticket.file.inputStream().use { it.copyTo(destination,65536) };destination.flush() }
                    val actual = owner.contentResolver.openInputStream(uri)?.use { digest(it) } ?: throw IOException("MOBILE_EXPORT_READBACK_FAILED")
                    if (actual.first != ticket.sha || actual.second != ticket.size) throw IOException("MOBILE_EXPORT_READBACK_FAILED")
                    intent.put("state","saved").put("verified_at",System.currentTimeMillis()).remove("error")
                    record(ticket,intent);invoke.resolve(publicResult(intent))
                } catch (_: Exception) {
                    intent.put("state","unknown").put("error","MOBILE_EXPORT_RESULT_UNKNOWN")
                    try { record(ticket,intent) } catch (_: Exception) { /* The existing writing intent still forbids a success claim. */ }
                    invoke.resolve(publicResult(intent))
                } finally { synchronized(gate) { active = null } }
            }
        } catch (_: Exception) {
            synchronized(gate) { active = null };invoke.reject("MOBILE_EXPORT_RESULT_UNKNOWN")
        }
    }

    @Command
    fun readDelivery(invoke: Invoke) {
        worker.execute {
            try {
                val ticket = ticket(invoke.getArgs())
                val requested = invoke.getArgs().getString("delivery_id")
                val delivery = if (requested.isNotEmpty()) uuid(requested) else {
                    val current = file("${ticket.id}.current.json")
                    if (!current.exists()) { invoke.resolve(JSObject().apply { put("ticket",ticket.id);put("state","not_started") });return@execute }
                    uuid(read(current.name).getString("delivery_id"))
                }
                val value = read(deliveryName(ticket.id,delivery))
                if (value.getString("ticket") != ticket.id || value.getString("delivery_id") != delivery || value.getString("sha256") != ticket.sha || value.getLong("size") != ticket.size) throw IOException("MOBILE_EXPORT_INVALID")
                if (value.getString("state") == "choosing" && synchronized(gate) { active != delivery }) {
                    value.put("state","unknown").put("error","MOBILE_EXPORT_RESULT_UNKNOWN")
                    record(ticket,value)
                }
                if (value.getString("state") in listOf("writing","unknown")) {
                    val uri = Uri.parse(value.optString("uri"))
                    if (uri.scheme == "content") {
                        try {
                            val actual = owner.contentResolver.openInputStream(uri)?.use { digest(it) }
                            if (actual != null && actual.first == ticket.sha && actual.second == ticket.size) {
                                value.put("state","saved").put("verified_at",System.currentTimeMillis()).remove("error")
                                record(ticket,value)
                            } else value.put("state","unknown").put("error","MOBILE_EXPORT_RESULT_UNKNOWN")
                        } catch (_: Exception) { value.put("state","unknown").put("error","MOBILE_EXPORT_RESULT_UNKNOWN") }
                    }
                }
                // Checking an original result performs no output write, backup, import or automatic retry.
                invoke.resolve(publicResult(value))
            } catch (_: Exception) { invoke.reject("MOBILE_EXPORT_RESULT_UNKNOWN") }
        }
    }

    @Command
    fun openExternal(invoke: Invoke) {
        try {
            val original = invoke.getArgs().getString("href")
            val uri = Uri.parse(original)
            if (original.length > 4096 || original.any { it.isWhitespace() } || uri.scheme !in listOf("http","https") || uri.host.isNullOrBlank() || !uri.userInfo.isNullOrEmpty()) throw IOException("MOBILE_LINK_UNSUPPORTED")
            owner.startActivity(Intent(Intent.ACTION_VIEW,uri).addCategory(Intent.CATEGORY_BROWSABLE))
            invoke.resolve(JSObject().apply { put("state","opened") })
        } catch (_: Exception) { invoke.reject("MOBILE_LINK_OPEN_FAILED") }
    }
}
