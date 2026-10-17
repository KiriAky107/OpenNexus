package cc.kronecker.opennexus.testdriver;

/** Read the exact original property value; never coerce arbitrary AX text. */
final class OriginalCanvasProperty {
    static boolean matches(String name, String expected, String observed, boolean named) {
        if (name == null || expected == null || observed == null) return false;
        if (name.equals("x") || name.equals("y")) {
            if (!expected.matches("-?[0-9]+(?:\\.[0-9]+)?")) return false;
            // Chromium's number control exposes its value and associated label
            // together, e.g. the actual owned API36 field was "48, x".
            return named && observed.equals(expected) || observed.equals(expected + ", " + name);
        }
        return name.equals("Node content") && named && observed.equals(expected);
    }
}
