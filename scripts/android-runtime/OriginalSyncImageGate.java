package cc.kronecker.opennexus.testdriver;

import java.util.Arrays;
import java.util.function.LongConsumer;
import java.util.function.LongSupplier;
import java.util.function.Supplier;

/** One immutable GUI observation, retained on the same instrumentation connection. */
final class OriginalSyncImageGate {
    static boolean imageIdentity(String app,String role,boolean exactAlt,int window,int expectedWindow,
                                 boolean visible,boolean editable,boolean password,boolean withinCanvas) {
        return "cc.kronecker.opennexus".equals(app) && "android.widget.Image".equals(role) && exactAlt
            && window>=0 && window==expectedWindow && visible && !editable && !password && withinCanvas;
    }
    static boolean occluded(boolean visible,boolean intersectsImage,boolean sameImageOrAncestor,boolean emptyCanvasConnectionImage) {
        return visible && intersectsImage && !sameImageOrAncestor && !emptyCanvasConnectionImage;
    }
    static final class Frame {
        final int uid,window,display;
        private final int[] screen,canvas,image;
        Frame(int uid,int window,int display,int[] screen,int[] canvas,int[] image) {
            OriginalSyncImageRegion.geometry(uid,window,display,screen,canvas,image);
            this.uid=uid;this.window=window;this.display=display;
            this.screen=screen.clone();this.canvas=canvas.clone();this.image=image.clone();
        }
        int[] screen() { return screen.clone(); }
        int[] canvas() { return canvas.clone(); }
        int[] image() { return image.clone(); }
        boolean same(Frame other) {
            return other!=null && uid==other.uid && window==other.window && display==other.display
                && Arrays.equals(screen,other.screen) && Arrays.equals(canvas,other.canvas) && Arrays.equals(image,other.image);
        }
    }
    interface Completion { boolean complete() throws Exception; }
    private Frame candidate,published;
    private long since=-1,last=-1,stable;
    boolean observe(Frame frame,long now) {
        need(published==null && now>=0);
        if(frame==null){candidate=null;since=-1;last=now;stable=0;return false;}
        if(candidate==null || now<last || !candidate.same(frame)) {candidate=frame;since=now;stable=0;}
        last=now;stable=now-since;return stable>=600;
    }
    Frame publish(long now) {
        need(published==null && candidate!=null && last==now && stable>=600 && stable<=10000);
        published=candidate;return published;
    }
    long stableMillis() { return stable; }
    void guard(Frame observed,long now) {
        need(published!=null && now>=last && published.same(observed));last=now;
    }
    void hold(Supplier<Frame> observations,LongSupplier clock,LongConsumer pause,Completion completion) throws Exception {
        long start=clock.getAsLong();need(start>=0 && start<=Long.MAX_VALUE-90000);long until=start+90000;
        while(clock.getAsLong()<until) {
            guard(observations.get(),clock.getAsLong());
            if(completion.complete()) { guard(observations.get(),clock.getAsLong());return; }
            pause.accept(50);
        }
        throw new IllegalStateException("OWNED_IMAGE_GUI_FINISH_TIMEOUT");
    }
    private static void need(boolean value) { if(!value)throw new IllegalStateException("OWNED_IMAGE_GUI_CHANGED"); }
}
