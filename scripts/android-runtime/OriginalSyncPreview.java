package cc.kronecker.opennexus.testdriver;

import java.util.function.Consumer;
import java.util.function.LongConsumer;
import java.util.function.LongSupplier;
import java.util.function.Supplier;

/** Read-only semantic admission after the one preview tap, before any confirm search/scroll. */
public final class OriginalSyncPreview {
    public static final class State {
        public final int sections, confirmations, enabled, errors;
        public final boolean owned;
        public State(int sections,int confirmations,int enabled,int errors,boolean owned) {
            this.sections=sections;this.confirmations=confirmations;this.enabled=enabled;this.errors=errors;this.owned=owned;
        }
        public String summary() {
            return "sections="+sections+" confirmations="+confirmations+" enabled="+enabled+" public_errors="+errors+" owned="+owned;
        }
    }
    private OriginalSyncPreview() {}
    public static void await(Supplier<State> read,LongSupplier clock,LongConsumer sleep,Consumer<State> observe) {
        long until=clock.getAsLong()+30000;String previous=null;
        do {
            State value=read.get();
            if(value==null || value.sections<0 || value.confirmations<0 || value.enabled<0 || value.errors<0
                || value.sections>2500 || value.confirmations>2500 || value.enabled>value.confirmations || value.errors>2500)
                throw new IllegalStateException("Original preview observation invalid; no confirm input sent");
            String summary=value.summary();
            if(!summary.equals(previous)) { observe.accept(value);previous=summary; }
            if(!value.owned || value.errors>0)
                throw new IllegalStateException("Original preview not admitted; no confirm input sent");
            if(value.sections>0 && value.enabled>0)return;
            sleep.accept(150);
        } while(clock.getAsLong()<until);
        throw new IllegalStateException("Original preview unconfirmed; no confirm search or input sent");
    }
}
