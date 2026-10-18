package cc.kronecker.opennexus.testdriver;

/** Only a new owned nested-note search scene, never a user device or IPC shim. */
final class OriginalNoteSearch {
    static final String DIRECTORY="检索";
    static final String NAME="资料😀.md", CONTROL_NAME="对照.md";
    static final String PATH=DIRECTORY+"/"+NAME, CONTROL_PATH=DIRECTORY+"/"+CONTROL_NAME;
    static final String SEED="# 离线检索原文 😀\n专属查找文本 %_.*\nOriginal source remains literal.\n";
    static final String ORIGINAL=SEED.replace("\n","\r\n");
    static final String CONTROL="# 对照笔记\nControl note without the query tokens.\n";
    static final String NEGATIVE="owned-search-no-match-6fdd9eb7";
    static final String[] QUERIES={"专属查找文本","%_",".*","资料😀","😀","对照"};
    static int queryIndex(String phase) {
        for(int i=0;i<QUERIES.length;i++)if(phase.equals("search-query-"+i))return i;
        throw new IllegalArgumentException("OWNED_NOTE_SEARCH_PHASE_REFUSED");
    }
    static String expected(int index) {
        if(index<0||index>=QUERIES.length)throw new IllegalArgumentException("OWNED_NOTE_SEARCH_QUERY_REFUSED");
        return index==5?CONTROL_NAME:NAME;
    }
    static boolean searchButton(boolean owned,String role,String text,String description,boolean enabled,boolean clickable) {
        return owned&&enabled&&clickable&&"android.widget.Button".equals(role)
            &&("Search".equals(text)||"Search".equals(description));
    }
    static int landingField(boolean owned,String role,String text,String description,boolean editable,boolean enabled,boolean clickable) {
        if(!owned||editable||!enabled)return 0;
        boolean readable="android.widget.TextView".equals(role)||"android.view.View".equals(role);
        if(readable&&("Start writing".equals(text)||"Start writing".equals(description)))return 1;
        if(readable&&("Select a note from the file tree or create a new one".equals(text)
            ||"Select a note from the file tree or create a new one".equals(description)))return 2;
        if("android.widget.Button".equals(role)&&clickable) {
            if("New note".equals(text)||"New note".equals(description))return 4;
            if("New canvas".equals(text)||"New canvas".equals(description))return 8;
        }
        return 0;
    }
    static boolean landingReady(int fields){return fields==15;}
}
