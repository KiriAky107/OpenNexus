package cc.kronecker.opennexus.sdkinternal;

import java.io.InputStream;
import java.io.OutputStream;
import java.util.Collection;
import javax.xml.bind.JAXBElement;
import org.w3c.dom.ls.LSResourceResolver;
import com.android.repository.api.ProgressIndicator;
import com.android.repository.api.SchemaModule;
import com.android.repository.api.Repository;
import com.android.repository.impl.meta.SchemaModuleUtil;

/** Two fixed API adapters call the actual installed vendor schema exactly once. */
public final class OwnedEmulatorSchemaContractCheck {
    public static final class Legacy {
        public static int reads, writes;
        public static Object unmarshal(InputStream input, Collection<SchemaModule<?>> modules,
                boolean strict, ProgressIndicator progress) throws Exception {
            OwnedEmulatorRegistry.require(strict); reads++;
            return SchemaModuleUtil.unmarshal(input, modules, strict, progress, "owned-emulator-registry");
        }
        public static void marshal(JAXBElement value, Collection<SchemaModule<?>> modules, OutputStream output,
                LSResourceResolver resolver, ProgressIndicator progress) throws Exception {
            writes++;
            SchemaModuleUtil.marshal(value, modules, output, resolver, progress, true);
        }
    }
    public static final class Current {
        public static int reads, writes;
        public static Object unmarshal(InputStream input, Collection<SchemaModule<?>> modules,
                boolean strict, ProgressIndicator progress, String description) throws Exception {
            OwnedEmulatorRegistry.require(strict && description.equals("owned-emulator-registry")); reads++;
            return SchemaModuleUtil.unmarshal(input, modules, strict, progress, description);
        }
        public static void marshal(JAXBElement value, Collection<SchemaModule<?>> modules, OutputStream output,
                LSResourceResolver resolver, ProgressIndicator progress, boolean strict) throws Exception {
            OwnedEmulatorRegistry.require(strict); writes++;
            SchemaModuleUtil.marshal(value, modules, output, resolver, progress, strict);
        }
    }
    public static final class FailedCurrent {
        public static int calls;
        public static Object unmarshal(InputStream input, Collection<SchemaModule<?>> modules,
                boolean strict, ProgressIndicator progress, String description) {
            calls++; throw new OwnedEmulatorRegistry.Guard();
        }
        public static Object unmarshal(InputStream input, Collection<SchemaModule<?>> modules,
                boolean strict, ProgressIndicator progress) {
            throw new AssertionError("Cannot retry a failed current reader as legacy");
        }
    }
    public static final class Unknown {}
    public static final class InstanceReader {
        public Object unmarshal(InputStream input, Collection<SchemaModule<?>> modules,
                boolean strict, ProgressIndicator progress) { return null; }
    }
    private static void require(boolean value) { OwnedEmulatorRegistry.require(value); }
    public static void main(String[] args) throws Exception {
        require(args.length == 0);
        byte[] xml = OwnedEmulatorRegistry.derive(OwnedEmulatorRegistry.fixedProperties());
        for (Class<?> api : new Class<?>[] {Legacy.class, Current.class}) {
            Repository parsed = OwnedEmulatorRegistry.readSchema(api, xml);
            require(parsed != null);
            byte[] roundtrip = OwnedEmulatorRegistry.writeSchema(api, parsed);
            OwnedEmulatorRegistry.validate(roundtrip);
            try {
                OwnedEmulatorRegistry.readSchema(api, new byte[] {'x'});
                throw new AssertionError("Invalid metadata accepted");
            } catch (java.lang.reflect.InvocationTargetException expected) {}
        }
        require(Legacy.reads == 2 && Legacy.writes == 1 && Current.reads == 2 && Current.writes == 1);
        try { OwnedEmulatorRegistry.readSchema(FailedCurrent.class, xml); throw new AssertionError(); }
        catch (java.lang.reflect.InvocationTargetException expected) { require(FailedCurrent.calls == 1); }
        for (Class<?> api : new Class<?>[] {Unknown.class, InstanceReader.class}) {
            try { OwnedEmulatorRegistry.readSchema(api, xml); throw new AssertionError(); }
            catch (OwnedEmulatorRegistry.Guard expected) {}
        }
        System.out.println("SDK_SCHEMA_CONTRACTS_OK legacy_and_current_vendor_backed=1 old_SDK_runtime=0");
    }
}
