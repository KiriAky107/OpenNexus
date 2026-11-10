"""Focused private result chronology; no device, GUI, DB or filesystem output."""
import test_android_quota_private_create_api as create


CASES = ('status-size-reserves-before-reentry', 'status-stream-reentry-stops-next-read',
         'framework-status-return-precedes-uiSent', 'finish-size-reentry-stops-getters',
         'finish-stream-reentry-stops-getters', 'finish-phase-reentry-stops-getters',
         'private-result-getter-error-static', 'private-fields-read-once-positive')
LEGACY = ('reentrant-result-getter-cannot-complete', 'duplicate-login-ui-and-status-refused',
          'framework-finish-or-status-unknown-never-retried')


class PrivateResultAPI(create.PrivateCreateAPI):
    case_names = allowed_cases = CASES
    legacy_names = allowed_legacy = LEGACY
    check_class = 'ReleaseQuotaPrivateResultCheck'

    def model_sources(self):
        values = create.controlled_sources()
        values.pop('cc/kronecker/opennexus/testdriver/ReleaseQuotaPrivateCreateCheck.java')
        values['cc/kronecker/opennexus/testdriver/ReleaseQuotaPrivateResultCheck.java'] = (
            create.ROOT/'android-runtime-tests/ReleaseQuotaPrivateResultCheck.java').read_text('utf-8')
        name = 'android/os/Bundle.java'
        values[name] = create.replace_once(values[name], 'public class Bundle {', '''public class Bundle {
 public Runnable phaseHook;
 public Bundle(){if(cc.kronecker.opennexus.testdriver.Control.markStatus){
  cc.kronecker.opennexus.testdriver.Control.markStatus=false;
  cc.kronecker.opennexus.testdriver.Control.lastStatus=this;
  sizeHook=cc.kronecker.opennexus.testdriver.Control.nextSizeHook;
  hook=cc.kronecker.opennexus.testdriver.Control.nextGetHook;
  cc.kronecker.opennexus.testdriver.Control.nextSizeHook=null;
  cc.kronecker.opennexus.testdriver.Control.nextGetHook=null;}}
''')
        values[name] = create.replace_once(values[name], 'public Object get(String k){gets++;',
            'public Object get(String k){gets++;if(k.equals("phase") && phaseHook!=null){'
            'Runnable once=phaseHook;phaseHook=null;once.run();}')
        name = 'cc/kronecker/opennexus/testdriver/Control.java'
        values[name] = create.replace_once(values[name], 'public static void reset(){',
            'public static boolean markStatus;public static Bundle lastStatus,lastResult;'
            'public static Runnable nextSizeHook,nextGetHook,statusAction;'
            'public static void reset(){markStatus=false;lastStatus=lastResult=null;'
            'nextSizeHook=nextGetHook=statusAction=null;')
        values[name] = create.replace_once(values[name], '  return b;', '''
  lastResult=b;
  if(mode.equals("close-finish-size"))b.sizeHook=resultAction;
  if(mode.equals("close-finish-stream"))b.hook=resultAction;
  if(mode.equals("close-finish-phase"))b.phaseHook=resultAction;
  if(mode.equals("private-result-error"))b.hook=()->{throw new AssertionError("SENSITIVE");};
  return b;''')
        name = 'android/app/Instrumentation.java'
        values[name] = create.replace_once(values[name], 'Control.statusCalls++;',
            'Control.statusCalls++;if(Control.statusAction!=null){Runnable once=Control.statusAction;'
            'Control.statusAction=null;once.run();}')
        return values
