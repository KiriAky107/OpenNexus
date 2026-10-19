package cc.kronecker.opennexus.testcontract;

/** Closed targets for the separate CI IME; not compiled into either product. */
public final class ImeTargetPolicy {
    private ImeTargetPolicy() {}

    public static boolean allowed(String target) {
        return "cc.kronecker.opennexus.dev".equals(target)
            || "cc.kronecker.opennexus".equals(target);
    }

    public static boolean matches(String requested, String focused) {
        return allowed(requested) && requested.equals(focused);
    }
}
