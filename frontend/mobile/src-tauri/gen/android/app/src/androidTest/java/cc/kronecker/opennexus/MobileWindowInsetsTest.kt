package cc.kronecker.opennexus

import android.widget.EditText
import android.widget.FrameLayout
import androidx.core.graphics.Insets
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test

/** Synthetic native inset delivery; actual keyboard/Canvas input is a separate runtime check. */
class MobileWindowInsetsTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val handled = WindowInsetsCompat.Type.systemBars() or
        WindowInsetsCompat.Type.displayCutout() or WindowInsetsCompat.Type.ime()

    private fun frame(block: (FrameLayout) -> Unit) = instrumentation.runOnMainSync {
        val content = FrameLayout(instrumentation.targetContext)
        content.setPadding(2, 3, 4, 5)
        MobileWindowInsets.install(content)
        block(content)
    }

    private fun deliver(content: FrameLayout, bars: Insets, ime: Insets): WindowInsetsCompat {
        val insets = WindowInsetsCompat.Builder()
            .setInsets(WindowInsetsCompat.Type.systemBars(), bars)
            .setInsets(WindowInsetsCompat.Type.ime(), ime)
            .setInsets(WindowInsetsCompat.Type.systemGestures(), Insets.of(9, 0, 9, 10))
            .build()
        return ViewCompat.dispatchApplyWindowInsets(content, insets)
    }

    @Test fun keyboardUsesTheOverlapUnionAndForwardsUpdatedZeroes() = frame { content ->
        val result = deliver(content, Insets.of(0, 24, 0, 48), Insets.of(0, 0, 0, 320))
        assertEquals(27, content.paddingTop)
        assertEquals(325, content.paddingBottom) // Not 320 + 48 + 5.
        assertEquals(Insets.NONE, result.getInsets(handled))
        assertFalse(result.isConsumed)
        assertEquals(Insets.of(9, 0, 9, 10), result.getInsets(WindowInsetsCompat.Type.systemGestures()))
    }

    @Test fun repeatedKeyboardDeliveryAndDismissalRestoreTheOriginalPadding() = frame { content ->
        repeat(3) { deliver(content, Insets.of(0, 24, 0, 48), Insets.of(0, 0, 0, 320)) }
        assertEquals(325, content.paddingBottom)
        val hidden = deliver(content, Insets.of(0, 24, 0, 48), Insets.NONE)
        assertEquals(53, content.paddingBottom)
        assertEquals(Insets.NONE, hidden.getInsets(handled))
        deliver(content, Insets.NONE, Insets.NONE)
        assertEquals(listOf(2, 3, 4, 5), listOf(content.paddingLeft, content.paddingTop,
            content.paddingRight, content.paddingBottom))
    }

    @Test fun changedWindowEdgesKeepTheExistingEditorAndSelection() = frame { content ->
        val editor = EditText(instrumentation.targetContext)
        content.addView(editor)
        editor.setText("original \u4e2d\u6587 \ud83d\ude00")
        editor.setSelection(2, 5)
        deliver(content, Insets.of(36, 0, 0, 0), Insets.of(0, 0, 0, 200))
        assertEquals(listOf(38, 3, 4, 205), listOf(content.paddingLeft, content.paddingTop,
            content.paddingRight, content.paddingBottom))
        assertSame(editor, content.getChildAt(0))
        assertEquals("original \u4e2d\u6587 \ud83d\ude00", editor.text.toString())
        assertEquals(2, editor.selectionStart)
        assertEquals(5, editor.selectionEnd)
    }
}
