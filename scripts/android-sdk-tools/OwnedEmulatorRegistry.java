package cc.kronecker.opennexus.sdkinternal;

import com.android.repository.Revision;
import com.android.repository.api.ProgressIndicatorAdapter;
import com.android.repository.api.ProgressIndicator;
import com.android.repository.api.RepoManager;
import com.android.repository.api.Repository;
import com.android.repository.api.SchemaModule;
import com.android.repository.impl.meta.CommonFactory;
import com.android.repository.impl.meta.LocalPackageImpl;
import com.android.repository.impl.meta.SchemaModuleUtil;
import com.android.repository.impl.meta.TypeDetails;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.OutputStream;
import java.io.InputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import javax.xml.bind.JAXBElement;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.ls.LSResourceResolver;

/** SDK vendor metadata only. No filesystem, network, installer or device actions. */
public final class OwnedEmulatorRegistry {
    private static final byte[] PROPERTIES = (
        "Pkg.UserSrc=false\nPkg.Revision=37.2.12\nPkg.Path=emulator\n" +
        "Pkg.Desc=Android Emulator\nPkg.BuildId=16428233").getBytes(StandardCharsets.US_ASCII);
    public static final String MATCH = "SDK_REGISTRY_SCHEMA_MATCH\n";

    public static final class Guard extends RuntimeException {}

    public static void require(boolean value) { if (!value) throw new Guard(); }

    public static byte[] fixedProperties() { return PROPERTIES.clone(); }

    public static Set<SchemaModule<?>> modules() {
        return new LinkedHashSet<>(Arrays.asList(RepoManager.getCommonModule(), RepoManager.getGenericModule()));
    }

    public static ProgressIndicatorAdapter progress() {
        return new ProgressIndicatorAdapter() {
            @Override public void logWarning(String text) { throw new Guard(); }
            @Override public void logWarning(String text, Throwable error) { throw new Guard(); }
            @Override public void logError(String text) { throw new Guard(); }
            @Override public void logError(String text, Throwable error) { throw new Guard(); }
        };
    }

    private static List<Element> children(Element parent) {
        List<Element> result = new ArrayList<>();
        for (Node node = parent.getFirstChild(); node != null; node = node.getNextSibling()) {
            if (node.getNodeType() == Node.ELEMENT_NODE) result.add((Element)node);
            else require(node.getNodeType() == Node.TEXT_NODE && node.getTextContent().trim().isEmpty());
        }
        return result;
    }

    private static void attributes(Element element, Set<String> allowed) {
        for (int i = 0; i < element.getAttributes().getLength(); i++) {
            Node value = element.getAttributes().item(i);
            if (XMLConstants.XMLNS_ATTRIBUTE_NS_URI.equals(value.getNamespaceURI())) continue;
            require(allowed.contains(value.getNodeName()));
        }
    }

    private static void closedDocument(byte[] bytes) throws Exception {
        require(bytes.length > 0 && bytes.length <= 65536);
        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
        factory.setNamespaceAware(true);
        factory.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
        factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
        factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
        factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "");
        factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
        factory.setXIncludeAware(false); factory.setExpandEntityReferences(false);
        Element root = factory.newDocumentBuilder().parse(new ByteArrayInputStream(bytes)).getDocumentElement();
        require(root.getLocalName().equals("repository") &&
            RepoManager.getCommonModule().getLatestNamespace().equals(root.getNamespaceURI()));
        attributes(root, Set.of());
        List<Element> roots = children(root); require(roots.size() == 1);
        Element local = roots.get(0);
        require(local.getNodeName().equals("localPackage") && local.getAttribute("path").equals("emulator") &&
            local.getAttribute("obsolete").equals("false"));
        attributes(local, Set.of("path", "obsolete"));
        List<Element> fields = children(local); require(fields.size() == 3);
        Element details = fields.get(0), revision = fields.get(1), display = fields.get(2);
        require(details.getNodeName().equals("type-details") && children(details).isEmpty());
        attributes(details, Set.of("xsi:type"));
        String type = details.getAttributeNS(XMLConstants.W3C_XML_SCHEMA_INSTANCE_NS_URI, "type");
        int colon = type.indexOf(':'); require(colon > 0 && type.substring(colon + 1).equals("genericDetailsType"));
        require(RepoManager.getGenericModule().getLatestNamespace().equals(details.lookupNamespaceURI(type.substring(0, colon))));
        require(revision.getNodeName().equals("revision")); attributes(revision, Set.of());
        List<Element> version = children(revision); require(version.size() == 3);
        String[] names = {"major", "minor", "micro"}, values = {"37", "2", "12"};
        for (int i = 0; i < names.length; i++) {
            Element value = version.get(i); attributes(value, Set.of());
            require(value.getNodeName().equals(names[i]) && value.getTextContent().equals(values[i]) &&
                value.getChildNodes().getLength() == 1 && value.getFirstChild().getNodeType() == Node.TEXT_NODE);
        }
        attributes(display, Set.of());
        require(display.getNodeName().equals("display-name") && display.getTextContent().equals("Android Emulator") &&
            display.getChildNodes().getLength() == 1 && display.getFirstChild().getNodeType() == Node.TEXT_NODE);
    }

    private static Method schemaMethod(Class<?> api, boolean reading) {
        try {
            Method method;
            if (reading) {
                try {
                    method = api.getMethod("unmarshal", InputStream.class, Collection.class,
                        boolean.class, ProgressIndicator.class, String.class);
                } catch (NoSuchMethodException olderContract) {
                    method = api.getMethod("unmarshal", InputStream.class, Collection.class,
                        boolean.class, ProgressIndicator.class);
                }
            } else {
                try {
                    method = api.getMethod("marshal", JAXBElement.class, Collection.class,
                        OutputStream.class, LSResourceResolver.class, ProgressIndicator.class, boolean.class);
                } catch (NoSuchMethodException olderContract) {
                    method = api.getMethod("marshal", JAXBElement.class, Collection.class,
                        OutputStream.class, LSResourceResolver.class, ProgressIndicator.class);
                }
            }
            require(Modifier.isStatic(method.getModifiers()) &&
                method.getReturnType() == (reading ? Object.class : void.class));
            return method;
        } catch (NoSuchMethodException unknownContract) { throw new Guard(); }
    }

    // The CLI always supplies SchemaModuleUtil.class. Package visibility lets the
    // contract check exercise both fixed vendor signatures without installing SDKs.
    static Repository readSchema(Class<?> api, byte[] bytes) throws Exception {
        Method method = schemaMethod(api, true);
        Object[] arguments = method.getParameterCount() == 5
            ? new Object[] {new ByteArrayInputStream(bytes), modules(), true, progress(), "owned-emulator-registry"}
            : new Object[] {new ByteArrayInputStream(bytes), modules(), true, progress()};
        return (Repository)method.invoke(null, arguments);
    }

    static byte[] writeSchema(Class<?> api, Repository repository) throws Exception {
        Method method = schemaMethod(api, false);
        CommonFactory factory = RepoManager.getCommonModule().createLatestFactory();
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        Object[] arguments = method.getParameterCount() == 6
            ? new Object[] {factory.generateRepository(repository), modules(), output,
                SchemaModuleUtil.createResourceResolver(modules(), progress()), progress(), true}
            : new Object[] {factory.generateRepository(repository), modules(), output,
                SchemaModuleUtil.createResourceResolver(modules(), progress()), progress()};
        method.invoke(null, arguments);
        return output.toByteArray();
    }

    public static void validate(byte[] bytes) throws Exception {
        closedDocument(bytes);
        System.setProperty("javax.xml.accessExternalDTD", "");
        System.setProperty("javax.xml.accessExternalSchema", "");
        Repository repository = readSchema(SchemaModuleUtil.class, bytes);
        require(repository != null && repository.getRemotePackage().isEmpty() && repository.getLicense().isEmpty() &&
            repository.getChannel().isEmpty());
        var local = repository.getLocalPackage();
        require(local != null && local.getPath().equals("emulator") && local.getVersion().equals(new Revision(37, 2, 12)) &&
            local.getDisplayName().equals("Android Emulator") && !local.obsolete() && local.getLicense() == null &&
            local.getAllDependencies().isEmpty() && local.getTypeDetails() instanceof TypeDetails.GenericType);
    }

    public static byte[] derive(byte[] properties) throws Exception {
        require(Arrays.equals(properties, PROPERTIES));
        CommonFactory factory = RepoManager.getCommonModule().createLatestFactory();
        LocalPackageImpl local = factory.createLocalPackage();
        local.setPath("emulator"); local.setVersion(new Revision(37, 2, 12));
        local.setDisplayName("Android Emulator"); local.setObsolete(false);
        local.setTypeDetails((TypeDetails)RepoManager.getGenericModule().createLatestFactory().createGenericDetailsType());
        Repository repository = factory.createRepositoryType(); repository.setLocalPackage(local);
        byte[] bytes = writeSchema(SchemaModuleUtil.class, repository); validate(bytes); return bytes;
    }

    public static void main(String[] args) {
        PrintStream output = System.out, error = System.err;
        System.setOut(new PrintStream(OutputStream.nullOutputStream()));
        System.setErr(new PrintStream(OutputStream.nullOutputStream()));
        try {
            require(args.length == 1 && (args[0].equals("derive") || args[0].equals("validate")));
            byte[] input = System.in.readNBytes(65537); require(input.length <= 65536);
            byte[] result;
            if (args[0].equals("derive")) result = derive(input);
            else { validate(input); result = MATCH.getBytes(StandardCharsets.US_ASCII); }
            output.write(result); output.flush();
        } catch (Throwable ignored) {
            error.print("SDK_REGISTRY_VENDOR_GUARD\n"); System.exit(1);
        }
    }
}
