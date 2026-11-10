import com.sun.source.util.JavacTask;
import com.sun.source.util.TaskEvent;
import com.sun.source.util.TaskListener;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import javax.tools.Diagnostic;
import javax.tools.DiagnosticCollector;
import javax.tools.FileObject;
import javax.tools.ForwardingJavaFileManager;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileManager;
import javax.tools.JavaFileObject;
import javax.tools.SimpleJavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.StandardLocation;
import javax.tools.ToolProvider;

/** Source-file launch and compiler output stay in RAM; no build directory exists. */
public final class ReleaseQuotaPrivateGuiMemoryCheck {
    private static void need(boolean value) {if(!value)throw new IllegalStateException("Memory compiler contract failed");}
    private static final class Source extends SimpleJavaFileObject {
        private final String source;
        Source(String name,String source) {super(URI.create("mem:///"+name),Kind.SOURCE);this.source=source;}
        @Override public CharSequence getCharContent(boolean ignore) {return source;}
    }
    private static final class Bytes extends SimpleJavaFileObject {
        final String name;
        final ByteArrayOutputStream buffer=new ByteArrayOutputStream();
        boolean opened;
        byte[] immutable;
        Bytes(String name) {super(URI.create("mem:///"+name.replace('.','/')+".class"),Kind.CLASS);this.name=name;}
        @Override public OutputStream openOutputStream() throws IOException {
            if(opened)throw new IOException("Duplicate memory class output");opened=true;return buffer;
        }
        byte[] raw() {if(immutable==null)immutable=buffer.toByteArray();return immutable;}
        @Override public InputStream openInputStream() {return new ByteArrayInputStream(raw());}
    }
    private static final class MemoryManager extends ForwardingJavaFileManager<StandardJavaFileManager> {
        final Map<String,Bytes> inputs,outputs=new LinkedHashMap<>();
        MemoryManager(StandardJavaFileManager base,Map<String,Bytes> inputs) {super(base);this.inputs=inputs;}
        @Override public JavaFileObject getJavaFileForOutput(Location location,String name,JavaFileObject.Kind kind,FileObject sibling) throws IOException {
            if(location!=StandardLocation.CLASS_OUTPUT || kind!=JavaFileObject.Kind.CLASS || outputs.containsKey(name)
                || outputs.size()>=1024)throw new IOException("No filesystem output fallback");
            Bytes value=new Bytes(name);outputs.put(name,value);return value;
        }
        @Override public JavaFileObject getJavaFileForOutputForOriginatingFiles(Location location,String name,JavaFileObject.Kind kind,FileObject... files) throws IOException {
            return getJavaFileForOutput(location,name,kind,null);
        }
        @Override public FileObject getFileForOutput(Location location,String pkg,String relative,FileObject sibling) throws IOException {
            throw new IOException("No resource filesystem output");
        }
        @Override public FileObject getFileForOutputForOriginatingFiles(Location location,String pkg,String relative,FileObject... files) throws IOException {
            throw new IOException("No resource filesystem output");
        }
        @Override public JavaFileObject getJavaFileForInput(Location location,String name,JavaFileObject.Kind kind) throws IOException {
            if(location==StandardLocation.CLASS_PATH && kind==JavaFileObject.Kind.CLASS && inputs.containsKey(name))return inputs.get(name);
            return super.getJavaFileForInput(location,name,kind);
        }
        @Override public Iterable<JavaFileObject> list(Location location,String pkg,Set<JavaFileObject.Kind> kinds,boolean recurse) throws IOException {
            List<JavaFileObject> values=new ArrayList<>();super.list(location,pkg,kinds,recurse).forEach(values::add);
            if(location==StandardLocation.CLASS_PATH && kinds.contains(JavaFileObject.Kind.CLASS))
                for(Bytes value:inputs.values()) {
                    String owner=value.name.substring(0,value.name.lastIndexOf('.'));
                    if(owner.equals(pkg) || recurse && owner.startsWith(pkg+'.'))values.add(value);
                }
            return values;
        }
        @Override public String inferBinaryName(Location location,JavaFileObject value) {
            return value instanceof Bytes?((Bytes)value).name:super.inferBinaryName(location,value);
        }
        @Override public boolean contains(Location location,FileObject value) throws IOException {
            return value instanceof Bytes || value instanceof Source || super.contains(location,value);
        }
    }
    private static Map<String,Bytes> compile(JavaCompiler compiler,String sdk,
            Iterable<? extends JavaFileObject> units,StandardJavaFileManager standard,
            Map<String,Bytes> inputs,Set<URI> parsed) throws IOException {
        DiagnosticCollector<JavaFileObject> diagnostics=new DiagnosticCollector<>();
        MemoryManager manager=new MemoryManager(standard,inputs);
        JavacTask task=(JavacTask)compiler.getTask(null,manager,diagnostics,
            List.of("-proc:none","-implicit:none","-encoding","UTF-8","-classpath",sdk,"-sourcepath",""),null,units);
        task.addTaskListener(new TaskListener(){
            @Override public void started(TaskEvent event) {
                if(event.getKind()==TaskEvent.Kind.PARSE)parsed.add(event.getSourceFile().toUri());
            }
        });
        if(!task.call()) {
            StringBuilder error=new StringBuilder("Memory compilation failed");
            for(Diagnostic<? extends JavaFileObject> d:diagnostics.getDiagnostics())if(d.getKind()==Diagnostic.Kind.ERROR)
                error.append(" ").append(d.getCode()).append(":").append(d.getLineNumber());
            throw new IllegalStateException(error.toString());
        }
        long count=0;for(Bytes value:manager.outputs.values())count+=value.raw().length;
        need(count>0 && count<=8388608);return manager.outputs;
    }
    private static String text(DataInputStream input,int max) throws IOException {
        int size=input.readInt();need(size>0 && size<=max);byte[] value=input.readNBytes(size);need(value.length==size);
        return new String(value,StandardCharsets.UTF_8);
    }
    public static void main(String[] args) throws Exception {
        need(args.length>=3);String sdk=args[0];int number=Integer.parseInt(args[1]);
        need(number>0 && number<=64 && args.length==number+2);
        List<Path> sources=new ArrayList<>();Set<URI> expected=new HashSet<>();
        for(int index=2;index<args.length;index++){Path p=Path.of(args[index]).toAbsolutePath().normalize();sources.add(p);expected.add(p.toUri());}
        JavaCompiler compiler=ToolProvider.getSystemJavaCompiler();need(compiler!=null);
        Map<String,Bytes> actual;Set<URI> parsed=new HashSet<>();
        try(StandardJavaFileManager manager=compiler.getStandardFileManager(null,null,StandardCharsets.UTF_8)) {
            actual=compile(compiler,sdk,manager.getJavaFileObjectsFromPaths(sources),manager,Map.of(),parsed);
        }
        need(parsed.equals(expected));
        String prefix="cc.kronecker.opennexus.testdriver.";
        Map<String,Bytes> entries=new HashMap<>();
        for(String name:List.of("ReleaseQuotaSeedRuntimeProbe","ReleaseQuotaLoginRuntimeProbe")) {
            need(actual.containsKey(prefix+name));entries.put(prefix+name,actual.get(prefix+name));
        }
        DataInputStream input=new DataInputStream(System.in);need(input.readInt()==0x4f4e5847);
        int count=input.readInt();need(count==8 || count==9);List<JavaFileObject> controlled=new ArrayList<>();Set<String> names=new HashSet<>();
        for(int index=0;index<count;index++) {
            String name=text(input,256);need(name.matches("[A-Za-z0-9_/]+\\.java") && names.add(name));
            controlled.add(new Source(name,text(input,65536)));
        }
        need(input.read()==-1);Map<String,Bytes> model;
        try(StandardJavaFileManager manager=compiler.getStandardFileManager(null,null,StandardCharsets.UTF_8)) {
            model=compile(compiler,sdk,controlled,manager,entries,new HashSet<>());
        }
        for(String name:entries.keySet()){need(!model.containsKey(name));model.put(name,entries.get(name));}
        ClassLoader loader=new ClassLoader(ClassLoader.getPlatformClassLoader()) {
            @Override protected Class<?> findClass(String name) throws ClassNotFoundException {
                Bytes value=model.get(name);if(value==null)throw new ClassNotFoundException(name);
                byte[] raw=value.raw();return defineClass(name,raw,0,raw.length);
            }
        };
        String check=System.getProperty("onx.private_gui_check","ReleaseQuotaPrivateGuiCheck");
        need(check.equals("ReleaseQuotaPrivateGuiCheck") || check.equals("ReleaseQuotaPrivateCreateCheck"));
        loader.loadClass(prefix+check).getMethod("main",String[].class).invoke(null,(Object)new String[0]);
        System.out.println("{\"API36_source_count\":"+parsed.size()+",\"API36_class_count\":"+actual.size()
            +",\"controlled_class_count\":"+model.size()+",\"API36_new_entry_bytecode_reused\":true,\"filesystem_outputs\":0}");
    }
}
