package cc.kronecker.opennexus.testdriver;

/** Read-only visible hit region; never retries an injected input. */
final class TouchViewport {
    private TouchViewport() {}
    /** Read-only exposure can be re-observed before input, never after a tap. */
    static final class RevealBudget {
        private long last = -1;
        private int attempts;
        boolean scrollBeforeInput(boolean hasVisibleHit, long now) {
            if (hasVisibleHit || now < 0 || attempts >= 2) return false;
            if (last < 0 || now < last) { last = now; return false; }
            if (now - last < 500) return false;
            last = now; attempts++; return true;
        }
    }
    static int verticalExposure(int[] target, int[] viewport) {
        if (target == null || viewport == null || target[2] <= target[0] || target[3] <= target[1]
            || viewport[2] <= viewport[0] || viewport[3] <= viewport[1]) return 0;
        if (target[1] < viewport[1]) return -1;
        if (target[3] > viewport[3]) return 1;
        return 0;
    }
    static int[] intersect(int[] a,int[] b) {
        if(a==null || b==null)return null;
        int l=Math.max(a[0],b[0]),t=Math.max(a[1],b[1]),r=Math.min(a[2],b[2]),bottom=Math.min(a[3],b[3]);
        return r>l && bottom>t ? new int[]{l,t,r,bottom} : null;
    }
    static int[] avoid(int[] original,int[] covered) {
        int[] overlap=intersect(original,covered);
        if(overlap==null)return original;
        int[][] candidates={
            {original[0],original[1],overlap[0],original[3]},
            {overlap[2],original[1],original[2],original[3]},
            {original[0],original[1],original[2],overlap[1]},
            {original[0],overlap[3],original[2],original[3]}
        };
        int[] result=null;long largest=0;
        for(int[] candidate:candidates) {
            long width=(long)candidate[2]-candidate[0],height=(long)candidate[3]-candidate[1];
            if(width>0 && height>0 && width*height>largest){largest=width*height;result=candidate;}
        }
        return result;
    }
}
