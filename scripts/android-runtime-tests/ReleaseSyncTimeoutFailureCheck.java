package cc.kronecker.opennexus.testdriver;

/** Bounded in-memory failure facts; no UI or signal/action performed. */
public final class ReleaseSyncTimeoutFailureCheck {
    private static int checks;
    interface Task {void run();}
    private static void need(boolean value) {if(!value)throw new AssertionError("Closed failure facts changed");checks++;}
    private static void reject(Task task) {
        try {task.run();}catch(IllegalStateException expected) {checks++;return;}
        throw new AssertionError("Invalid failure facts accepted");
    }
    public static void main(String[] ignored) {
        String parent="40000000-0000-4000-8000-000000000001",input="40000000-0000-4000-8000-000000000002";
        OriginalSyncTimeoutActions value=new OriginalSyncTimeoutActions(OriginalSyncTimeoutActions.TIMEOUT,parent,input);
        need(value.lastScene()==null && value.lastSceneAt()==-1 && value.originalReadyAt()==-1 && !value.originalInputFinished());
        int[] scene={1,1,1,0,0,0,0,0};reject(()->value.recordScene(scene,100));
        value.begin("cycle-start");value.ready(100);reject(()->value.recordScene(scene,100));value.finished("cycle-start");
        value.recordScene(scene,30100);need(value.originalInputFinished() && value.originalReadyAt()==100 && value.lastSceneAt()==30100);
        scene[0]=200;need(value.lastScene()[0]==1);int[] copy=value.lastScene();copy[1]=200;need(value.lastScene()[1]==1);
        value.recordScene(scene,30101);need(value.lastScene()[0]==2 && value.lastScene()[3]==0);
        reject(()->value.recordScene(new int[]{-1,1,1,1,0,0,0,0},30102));
        reject(()->value.recordScene(scene,30100));reject(()->value.recordScene(scene,99));
        reject(()->value.recordScene(null,30102));reject(()->value.recordScene(new int[9],30102));
        need(value.lastSceneAt()==30101 && value.lastScene()[0]==2);
        reject(value::complete);need(value.summary().contains("failed_cycle_UI_observed=false") && value.summary().contains("native_timeout_verified=false"));
        value.close();reject(()->value.recordScene(scene,30102));need(value.lastScene()[0]==2);
        OriginalSyncTimeoutActions resume=new OriginalSyncTimeoutActions(OriginalSyncTimeoutActions.RESUME,parent,input);
        resume.begin("cycle-resume");resume.ready(100);resume.finished("cycle-resume");reject(()->resume.recordScene(scene,30100));
        need(OriginalSyncTimeoutActions.failedScene(1,1,1,1,0,0,0,0));need(!OriginalSyncTimeoutActions.failedScene(1,1,1,0,0,0,0,0));
        System.out.println("RELEASE_SYNC_TIMEOUT_FAILURE_FACTS_OK checks="+checks+" native_or_UI_ACK=0");
    }
}
