package cc.kronecker.opennexus

import android.view.View
import androidx.core.graphics.Insets
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat

/** Keep the WebView layout, including its bottom-anchored inspector, above the IME. */
object MobileWindowInsets {
    fun install(content: View) {
        val left = content.paddingLeft
        val top = content.paddingTop
        val right = content.paddingRight
        val bottom = content.paddingBottom
        val handled = WindowInsetsCompat.Type.systemBars() or
            WindowInsetsCompat.Type.displayCutout() or WindowInsetsCompat.Type.ime()
        ViewCompat.setOnApplyWindowInsetsListener(content) { view, windowInsets ->
            // The union takes the maximum overlapping inset, not keyboard +
            // navigation bar. Recompute from the original padding on every
            // change, including keyboard dismissal, rotation and multi-window.
            val insets = windowInsets.getInsets(handled)
            view.setPadding(left + insets.left, top + insets.top,
                right + insets.right, bottom + insets.bottom)
            // These dimensions have already reduced the native content area.
            // Forward zeroes (rather than CONSUMED) so WebView updates its own
            // safe areas/visual viewport without applying the same inset twice.
            WindowInsetsCompat.Builder(windowInsets).setInsets(handled, Insets.NONE).build()
        }
        ViewCompat.requestApplyInsets(content)
    }
}
