package cc.kronecker.opennexus.testdriver;

import java.util.ArrayList;
import java.util.List;

/** Owned long-note input, below the product limit and each existing IME message limit. */
final class OriginalLongNoteScenario {
    static final String PATH = "runtime-long.md";
    static final String MARKER = "\n末尾输入已确认 😀 / original end input\n";
    static String content() {
        StringBuilder body = new StringBuilder("# 长笔记测量 😀\n\n");
        for (int i=0;i<768;i++) {
            body.append("## 段落 ").append(i).append("\n")
                .append("保留中文和 emoji 😀。这是拥有的离线输入，记录真实耗时，不设虚构性能门槛。\n")
                .append("[原链接](https://example.invalid/long-note) `original Markdown`\n\n");
        }
        return body.toString();
    }
    static List<String> chunks(String body) {
        List<String> result=new ArrayList<>();
        for(int start=0;start<body.length();) {
            int end=Math.min(start+16384,body.length());
            if(end<body.length() && Character.isHighSurrogate(body.charAt(end-1)))end--;
            result.add(body.substring(start,end));start=end;
        }
        return result;
    }
}
