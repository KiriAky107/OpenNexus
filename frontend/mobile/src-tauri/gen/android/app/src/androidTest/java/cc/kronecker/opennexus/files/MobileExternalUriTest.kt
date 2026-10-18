package cc.kronecker.opennexus.files

import java.io.IOException
import org.junit.Assert.*
import org.junit.Test

/** Actual Android Uri behavior; no Activity, network, note write or OS handoff is performed here. */
class MobileExternalUriTest {
    @Test fun onlySchemeChangesWhileOriginalAndEncodedRemainderStayExact() {
        val remainder = "://Example.invalid/original?literal=%252F_%25&utf8=%F0%9F%98%80#original"
        for ((input, expected) in listOf("HTTPS" to "https", "HtTp" to "http", "https" to "https")) {
            val original = input + remainder
            val result = externalLinkUri(original)
            assertEquals(expected, result.scheme)
            assertEquals(expected + remainder, result.toString())
            assertEquals(input + remainder, original)
        }
    }

    @Test fun unsupportedSchemesCredentialsWhitespaceAndLimitsRemainRejected() {
        val invalid = listOf("file:///private", "content://provider/original", "javascript:alert(1)",
            "mailto:reader@example.invalid", "https:///path", "HTTPS://user:pass@example.invalid/",
            "HTTPS://example.invalid/white space", "https://example.invalid/\u2003", "https://example.invalid/" + "x".repeat(4096))
        for (original in invalid) {
            try {
                externalLinkUri(original)
                fail("Unsupported original was admitted")
            } catch (error: IOException) {
                assertEquals("MOBILE_LINK_UNSUPPORTED", error.message)
            }
        }
    }
}
