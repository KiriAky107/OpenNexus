package cc.kronecker.opennexus.testcontract;

/** Executes the actual target policy; never contacts a device or InputConnection. */
public final class ImeTargetPolicyCheck {
    public static void main(String[] args) {
        String debug = "cc.kronecker.opennexus.dev", release = "cc.kronecker.opennexus";
        int cases = 0;
        for (String target : new String[]{debug, release}) {
            if (!ImeTargetPolicy.allowed(target) || !ImeTargetPolicy.matches(target, target))
                throw new AssertionError("Exact owned editor rejected");
            cases++;
            for (String other : new String[]{null, "", "com.android.settings", "cc.kronecker.opennexus.other",
                    target + ".suffix", " " + target, target + " ", target + "\n"}) {
                if (ImeTargetPolicy.matches(target, other) || ImeTargetPolicy.matches(other, target))
                    throw new AssertionError("Unrelated or ambiguous target accepted");
                cases++;
            }
        }
        if (ImeTargetPolicy.matches(debug, release) || ImeTargetPolicy.matches(release, debug))
            throw new AssertionError("Requested and focused owned packages were interchangeable");
        cases += 2;
        System.out.println("IME_TARGET_POLICY_OK " + cases + " no_device_or_text_input=1");
    }
}
