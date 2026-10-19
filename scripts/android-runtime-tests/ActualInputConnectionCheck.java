package cc.kronecker.opennexus.testcontract;
import android.text.InputType;
public final class ActualInputConnectionCheck {
  static ActualInputConnectionCheck active;
  boolean started=true;
  EditorInfo info=new EditorInfo();
  InputConnection connection=new InputConnection();
  static final class EditorInfo { String packageName; int inputType=InputType.TYPE_CLASS_TEXT; }
  static final class InputConnection {
    int calls;
    boolean setSelection(int a,int b){calls++;return true;}
    boolean commitText(String s,int n){calls++;return true;}
    boolean setComposingText(String s,int n){calls++;return true;}
    boolean finishComposingText(){calls++;return true;}
  }
  EditorInfo getCurrentInputEditorInfo(){return info;}
  InputConnection getCurrentInputConnection(){return connection;}
// ACTUAL_METHODS
  static void need(boolean b){if(!b)throw new AssertionError("Actual InputConnection admission changed");}
  public static void main(String[] args){
    int cases=0;
    String debug="cc.kronecker.opennexus.dev",release="cc.kronecker.opennexus";
    for(String target:new String[]{debug,release}){
      ActualInputConnectionCheck t=new ActualInputConnectionCheck();active=t;t.info.packageName=target;
      need(t.execute(target,"check",null,-1));need(t.connection.calls==0);cases++;
      for(String op:new String[]{"select","commit","compose","finish"}){
        int before=t.connection.calls;need(t.execute(target,op,"owned",1));need(t.connection.calls==before+1);cases++;
      }
      for(String focused:new String[]{null,"com.android.settings",target.equals(debug)?release:debug,target+".other"}){
        t.info.packageName=focused;int before=t.connection.calls;
        need(!t.execute(target,"commit","must not input",-1));need(t.connection.calls==before);cases++;
      }
      t.info.packageName=target;
      for(int type:new int[]{InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD,
          InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD,
          InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD,
          InputType.TYPE_CLASS_NUMBER|InputType.TYPE_NUMBER_VARIATION_PASSWORD}){
        t.info.inputType=type;int before=t.connection.calls;
        need(!t.execute(target,"commit","must not input",-1));need(t.connection.calls==before);cases++;
      }
      t.info.inputType=InputType.TYPE_CLASS_TEXT;t.started=false;need(!t.execute(target,"check",null,-1));cases++;
      t.started=true;active=null;need(!t.execute(target,"check",null,-1));cases++;
      active=t;t.connection=null;need(!t.execute(target,"check",null,-1));cases++;
      t.connection=new InputConnection();t.info=null;need(!t.execute(target,"check",null,-1));cases++;
    }
    System.out.println("ACTUAL_IME_CONNECTION_OK "+cases+" no_device_or_text_input=1");
  }
}
