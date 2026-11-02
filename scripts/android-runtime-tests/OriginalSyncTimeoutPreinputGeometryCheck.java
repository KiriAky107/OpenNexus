package cc.kronecker.opennexus.testdriver;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.Arrays;

public final class OriginalSyncTimeoutPreinputGeometryCheck {
    private static int assertions;
    private static final int UID=12001,WINDOW=7;
    private static final OriginalSyncTimeoutPreinputGeometry.Scope OWNED=scope(true,true,false,false,UID,WINDOW);
    private static final OriginalSyncTimeoutPreinputGeometry.Decision WAIT=OriginalSyncTimeoutPreinputGeometry.Decision.WAIT_WITHIN_PREINPUT_BUDGET;
    private static final OriginalSyncTimeoutPreinputGeometry.Decision REVEAL=OriginalSyncTimeoutPreinputGeometry.Decision.REQUEST_ONE_SECTION_REVEAL;
    private static final OriginalSyncTimeoutPreinputGeometry.Decision STABLE=OriginalSyncTimeoutPreinputGeometry.Decision.CURRENT_PAIR_STABLE;
    private static final OriginalSyncTimeoutPreinputGeometry.Decision REFUSE=OriginalSyncTimeoutPreinputGeometry.Decision.REFUSE;
    private static void check(boolean value) {assertions++;if(!value)throw new AssertionError("Pre-input geometry contract");}
    private static OriginalSyncTimeoutPreinputGeometry.Scope scope(Boolean timeout,Boolean owned,Boolean ready,Boolean input,int uid,int window) {
        return new OriginalSyncTimeoutPreinputGeometry.Scope(timeout,owned,ready,input,uid,window);
    }
    private static OriginalSyncTimeoutPreinputGeometry planner() {return new OriginalSyncTimeoutPreinputGeometry(0,UID,WINDOW);}
    // Synthetic coordinates have no relationship to a device or future UI height.
    private static int[][] fit() {return new int[][]{{0,0,1000,1000},{10,10,20,20},{0,0,1000,1000},{10,30,90,80},{0,0,1000,1000}};}
    private static int[][] clipped() {int[][] value=fit();value[3][3]=1001;return value;}
    private static OriginalSyncTimeoutPreinputGeometry.Decision observe(OriginalSyncTimeoutPreinputGeometry p,long now,int[][] r) {
        return observe(p,OWNED,now,r);
    }
    private static OriginalSyncTimeoutPreinputGeometry.Decision observe(OriginalSyncTimeoutPreinputGeometry p,OriginalSyncTimeoutPreinputGeometry.Scope scope,long now,int[][] r) {
        return p.observe(scope,now,r[0],r[1],r[2],r[3],r[4]);
    }
    private static void stabilityAndCurrentScope() {
        OriginalSyncTimeoutPreinputGeometry p=planner();int[][] r=fit();
        check(observe(p,0,r)==WAIT);check(observe(p,300,r)==WAIT);check(observe(p,599,r)==WAIT);check(observe(p,600,r)==STABLE);
        check(observe(p,700,r)==STABLE);
        // The very same current fit is compatible with a later section overflow.
        r[3][3]=1001;check(observe(p,701,r)==REVEAL);
        p=planner();r=fit();check(observe(p,0,r)==WAIT);check(observe(p,600,r)==WAIT);check(observe(p,600,r)==STABLE);
        p=planner();r=fit();check(observe(p,0,r)==WAIT);r[3][3]++;check(observe(p,500,r)==WAIT);
        check(observe(p,800,r)==WAIT);check(observe(p,1099,r)==WAIT);check(observe(p,1100,r)==STABLE);
    }
    private static void arrayOwnershipAndClipChanges() {
        for(int i=0;i<5;i++) {
            OriginalSyncTimeoutPreinputGeometry p=planner();int[][] r=fit();
            int[][] original=new int[5][];for(int j=0;j<5;j++)original[j]=r[j].clone();
            check(observe(p,0,r)==WAIT);
            for(int j=0;j<5;j++)check(Arrays.equals(original[j],r[j]));
            if(i==0)r[i][2]++;else if(i==1)r[i][0]++;else if(i==3)r[i][3]++;else r[i][0]++;
            // Mutation must be a new geometry sample, not mutate retained state.
            check(observe(p,600,r)==WAIT);check(observe(p,900,r)==WAIT);check(observe(p,1200,r)==STABLE);
        }
    }
    private static void revealProtocol() {
        for(Boolean result:new Boolean[]{false,null}) {
            OriginalSyncTimeoutPreinputGeometry p=planner();check(observe(p,0,clipped())==REVEAL);
            check(p.revealReturned(OWNED,result,1)==REFUSE);check(observe(p,1000,fit())==REFUSE);
            check(p.revealReturned(OWNED,true,1001)==REFUSE);
        }
        OriginalSyncTimeoutPreinputGeometry p=planner();check(observe(p,0,clipped())==REVEAL);
        check(observe(p,1,fit())==REFUSE);check(p.revealReturned(OWNED,true,2)==REFUSE);
        p=planner();check(p.revealReturned(OWNED,true,0)==REFUSE);
        p=planner();check(observe(p,0,clipped())==REVEAL);check(p.revealReturned(OWNED,true,100)==WAIT);
        check(observe(p,200,fit())==WAIT);check(observe(p,500,fit())==WAIT);check(observe(p,800,fit())==STABLE);
        check(p.revealReturned(OWNED,true,801)==REFUSE);
        p=planner();check(observe(p,0,clipped())==REVEAL);check(p.revealReturned(OWNED,true,100)==WAIT);
        check(observe(p,101,clipped())==REFUSE);check(observe(p,1000,fit())==REFUSE);
        p=planner();check(observe(p,0,clipped())==REVEAL);
        check(p.revealReturned(scope(true,false,false,false,UID,WINDOW),true,1)==REFUSE);
    }
    private static void invalidRectangles() {
        int[][] bad={null,new int[0],new int[]{0,0,1},new int[]{0,0,1,1,2},
            new int[]{-1,0,1,1},new int[]{0,-1,1,1},new int[]{1,0,1,1},new int[]{0,1,1,1},
            new int[]{2,0,1,1},new int[]{0,2,1,1},new int[]{Integer.MIN_VALUE,0,Integer.MAX_VALUE,1}};
        for(int slot=0;slot<5;slot++)for(int[] value:bad) {
            OriginalSyncTimeoutPreinputGeometry p=planner();int[][] r=fit();r[slot]=value;
            check(observe(p,0,r)==REFUSE);check(observe(p,1000,fit())==REFUSE);
        }
        for(int clip:new int[]{2,4}) {
            int[][] r=fit();r[clip][2]=1001;check(observe(planner(),0,r)==REFUSE);
        }
        int[][] r=fit();r[1][2]=1001;check(observe(planner(),0,r)==REFUSE);
        r=fit();r[1][2]=1001;r[3][3]=1001;check(observe(planner(),0,r)==REFUSE);
        OriginalSyncTimeoutPreinputGeometry p=planner();
        r=new int[][]{{0,0,Integer.MAX_VALUE,Integer.MAX_VALUE},{1,1,2,2},{0,0,Integer.MAX_VALUE,Integer.MAX_VALUE},
            {Integer.MAX_VALUE-1,Integer.MAX_VALUE-1,Integer.MAX_VALUE,Integer.MAX_VALUE},{0,0,Integer.MAX_VALUE,Integer.MAX_VALUE}};
        check(observe(p,0,r)==WAIT);check(observe(p,300,r)==WAIT);check(observe(p,600,r)==STABLE);
    }
    private static void unknownOrChangedScope() {
        Boolean[] options={true,false,null};
        for(Boolean timeout:options)for(Boolean owned:options)for(Boolean ready:options)for(Boolean input:options) {
            if(Boolean.TRUE.equals(timeout) && Boolean.TRUE.equals(owned) && Boolean.FALSE.equals(ready) && Boolean.FALSE.equals(input))continue;
            OriginalSyncTimeoutPreinputGeometry p=planner();
            check(observe(p,scope(timeout,owned,ready,input,UID,WINDOW),0,fit())==REFUSE);
            check(observe(p,1000,fit())==REFUSE);
        }
        check(observe(planner(),null,0,fit())==REFUSE);
        check(observe(planner(),scope(true,true,false,false,UID+1,WINDOW),0,fit())==REFUSE);
        check(observe(planner(),scope(true,true,false,false,UID,WINDOW+1),0,fit())==REFUSE);
        for(int uid:new int[]{-1,0,9999,100000,Integer.MAX_VALUE}) {
            OriginalSyncTimeoutPreinputGeometry p=new OriginalSyncTimeoutPreinputGeometry(0,uid,WINDOW);
            check(observe(p,scope(true,true,false,false,uid,WINDOW),0,fit())==REFUSE);
        }
        check(observe(new OriginalSyncTimeoutPreinputGeometry(0,UID,-1),scope(true,true,false,false,UID,-1),0,fit())==REFUSE);
        OriginalSyncTimeoutPreinputGeometry p=planner();check(observe(p,0,fit())==WAIT);check(observe(p,300,fit())==WAIT);check(observe(p,600,fit())==STABLE);
        check(observe(p,scope(true,true,true,false,UID,WINDOW),601,fit())==REFUSE);check(observe(p,602,fit())==REFUSE);
    }
    private static void clockBoundaries() {
        for(long start:new long[]{-1,Long.MIN_VALUE,Long.MAX_VALUE,Long.MAX_VALUE-4999})
            check(observe(new OriginalSyncTimeoutPreinputGeometry(start,UID,WINDOW),start,fit())==REFUSE);
        OriginalSyncTimeoutPreinputGeometry p=planner();check(observe(p,-1,fit())==REFUSE);
        p=planner();check(observe(p,100,fit())==WAIT);check(observe(p,99,fit())==REFUSE);
        p=planner();check(observe(p,0,fit())==WAIT);check(observe(p,5001,fit())==REFUSE);
        p=planner();check(observe(p,4400,fit())==WAIT);check(observe(p,4700,fit())==WAIT);check(observe(p,5000,fit())==STABLE);
        check(observe(p,5001,fit())==REFUSE);
        check(observe(planner(),5000,fit())==REFUSE);check(observe(planner(),5000,clipped())==REFUSE);
        p=planner();check(observe(p,4999,clipped())==REVEAL);check(p.revealReturned(OWNED,true,5000)==REFUSE);
        p=planner();check(observe(p,100,clipped())==REVEAL);check(p.revealReturned(OWNED,true,99)==REFUSE);
        long start=Long.MAX_VALUE-5000;p=new OriginalSyncTimeoutPreinputGeometry(start,UID,WINDOW);
        check(observe(p,start,fit())==WAIT);check(observe(p,start+300,fit())==WAIT);check(observe(p,start+600,fit())==STABLE);
        check(observe(p,Long.MAX_VALUE,fit())==STABLE);
        p=new OriginalSyncTimeoutPreinputGeometry(100,UID,WINDOW);check(observe(p,99,fit())==REFUSE);
    }
    private static void closedOutputAndPureInputs() {
        check(OriginalSyncTimeoutPreinputGeometry.BUDGET_MS==5000);check(OriginalSyncTimeoutPreinputGeometry.STABLE_MS==600);
        check(OriginalSyncTimeoutPreinputGeometry.Decision.values().length==4);
        for(Field field:OriginalSyncTimeoutPreinputGeometry.Scope.class.getDeclaredFields()) {
            check(Modifier.isFinal(field.getModifiers()));check(field.getType()==int.class || field.getType()==Boolean.class);
        }
        for(Method method:OriginalSyncTimeoutPreinputGeometry.class.getDeclaredMethods())if(!Modifier.isPrivate(method.getModifiers())) {
            check(method.getReturnType()==OriginalSyncTimeoutPreinputGeometry.Decision.class);
            for(Class<?> type:method.getParameterTypes())check(type==OriginalSyncTimeoutPreinputGeometry.Scope.class
                || type==long.class || type==int[].class || type==Boolean.class);
        }
    }
    public static void main(String[] args) {
        stabilityAndCurrentScope();arrayOwnershipAndClipChanges();revealProtocol();invalidRectangles();
        unknownOrChangedScope();clockBoundaries();closedOutputAndPureInputs();
        System.out.println("{\"passed\":true,\"assertions\":"+assertions+",\"actual_UI_or_Native_verified\":false}");
    }
}
