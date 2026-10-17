package cc.kronecker.opennexus.testdriver;

/** Read-only visible hit region; never retries an injected input. */
final class TouchViewport {
    private TouchViewport() {}
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
