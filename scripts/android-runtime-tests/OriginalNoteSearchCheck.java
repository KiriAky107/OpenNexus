package cc.kronecker.opennexus.testdriver;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
public final class OriginalNoteSearchCheck {
    private static int count;
    private static void need(boolean value){if(!value)throw new AssertionError("OWNED_NOTE_SEARCH_DATA_FAILED");count++;}
    private static void refuses(Runnable value){try{value.run();}catch(IllegalArgumentException e){count++;return;}throw new AssertionError("NOTE_SEARCH_NOT_REFUSED");}
    private static String hash(String value)throws Exception{StringBuilder s=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)))s.append(String.format("%02x",b&255));return s.toString();}
    public static void main(String[] args)throws Exception{
        for(int i=0;i<6;i++){need(OriginalNoteSearch.queryIndex("search-query-"+i)==i);need(OriginalNoteSearch.expected(i).equals(i==5?"对照.md":"资料😀.md"));}
        for(String value:new String[]{"search-query-6","search-query--1","search-query-00","search-query-0-extra","other"})refuses(()->OriginalNoteSearch.queryIndex(value));
        refuses(()->OriginalNoteSearch.expected(-1));refuses(()->OriginalNoteSearch.expected(6));
        need(OriginalNoteSearch.QUERIES[1].equals("%_"));need(OriginalNoteSearch.QUERIES[2].equals(".*"));
        need(!OriginalNoteSearch.ORIGINAL.replace("\r\n","").contains("\n"));need(OriginalNoteSearch.ORIGINAL.replace("\r\n","\n").equals(OriginalNoteSearch.SEED));
        for(String query:OriginalNoteSearch.QUERIES)need(OriginalNoteSearch.SEED.contains(query)||OriginalNoteSearch.NAME.contains(query)||OriginalNoteSearch.CONTROL_NAME.contains(query));
        need(!OriginalNoteSearch.SEED.contains(OriginalNoteSearch.NEGATIVE));need(!OriginalNoteSearch.CONTROL.contains(OriginalNoteSearch.NEGATIVE));
        need(OriginalNoteSearch.searchButton(true,"android.widget.Button","Search","",true,true));
        need(OriginalNoteSearch.searchButton(true,"android.widget.Button","","Search",true,true));
        need(!OriginalNoteSearch.searchButton(false,"android.widget.Button","Search","",true,true));
        need(!OriginalNoteSearch.searchButton(true,"android.view.View","Search","",true,true));
        need(!OriginalNoteSearch.searchButton(true,"android.widget.Button","Search","",false,true));
        need(!OriginalNoteSearch.searchButton(true,"android.widget.Button","Search","",true,false));
        need(!OriginalNoteSearch.searchButton(true,"android.widget.Button","Search names and text","",true,true));
        need(!OriginalNoteSearch.searchButton(true,"android.widget.Button",null,null,true,true));
        int fields=OriginalNoteSearch.landingField(true,"android.widget.TextView","Start writing","",false,true,false)
            |OriginalNoteSearch.landingField(true,"android.widget.TextView","Select a note from the file tree or create a new one","",false,true,false)
            |OriginalNoteSearch.landingField(true,"android.widget.Button","New note","",false,true,true)
            |OriginalNoteSearch.landingField(true,"android.widget.Button","New canvas","",false,true,true);
        need(OriginalNoteSearch.landingReady(fields));
        for(int missing:new int[]{1,2,4,8})need(!OriginalNoteSearch.landingReady(fields&~missing));
        need(OriginalNoteSearch.landingField(true,"android.widget.TextView","Notes in this vault","",false,true,false)==0);
        need(OriginalNoteSearch.landingField(false,"android.widget.TextView","Start writing","",false,true,false)==0);
        need(OriginalNoteSearch.landingField(true,"android.widget.EditText","Start writing","",true,true,false)==0);
        need(OriginalNoteSearch.landingField(true,"android.widget.Button","New note","",false,true,false)==0);
        need(OriginalNoteSearch.landingField(true,"android.widget.Button","New canvas","",false,false,true)==0);
        need(OriginalNoteSearch.landingField(true,"android.view.View","New note","",false,true,true)==0);
        need(OriginalNoteSearch.landingField(true,"android.widget.TextView","Start writing draft","",false,true,false)==0);
        need(OriginalNoteSearch.landingField(true,"android.widget.TextView",null,null,false,true,false)==0);
        System.out.println("ORIGINAL_NOTE_SEARCH_DATA_OK "+count);
        System.out.println(hash(OriginalNoteSearch.SEED)+" "+hash(OriginalNoteSearch.ORIGINAL)+" "+hash(OriginalNoteSearch.CONTROL));
    }
}
