package cc.kronecker.opennexus.sdkinternal;

import com.android.repository.Revision;
import com.android.repository.impl.manager.LocalRepoLoaderImpl;
import java.nio.file.Files;
import java.nio.file.Path;

/** Real vendor local reader against a metadata-only task-owned fixture. */
public final class OwnedEmulatorRegistryCheck {
    public static void main(String[] args) throws Exception {
        OwnedEmulatorRegistry.require(args.length == 1);
        Path root = Path.of(args[0]).toAbsolutePath().normalize();
        OwnedEmulatorRegistry.require(Files.isDirectory(root) && !Files.exists(root.resolve("emulator")));
        Path directory = Files.createDirectory(root.resolve("emulator"));
        Files.write(directory.resolve("source.properties"), OwnedEmulatorRegistry.fixedProperties());
        var before = new LocalRepoLoaderImpl(root, OwnedEmulatorRegistry.modules(), null)
            .getPackages(OwnedEmulatorRegistry.progress());
        OwnedEmulatorRegistry.require(before.isEmpty());
        byte[] xml = OwnedEmulatorRegistry.derive(OwnedEmulatorRegistry.fixedProperties());
        Files.write(directory.resolve("package.xml"), xml);
        var after = new LocalRepoLoaderImpl(root, OwnedEmulatorRegistry.modules(), null)
            .getPackages(OwnedEmulatorRegistry.progress());
        var local = after.get("emulator");
        OwnedEmulatorRegistry.require(after.size() == 1 && local != null &&
            local.getPath().equals("emulator") && local.getVersion().equals(new Revision(37, 2, 12)) &&
            local.getDisplayName().equals("Android Emulator") && local.getLocation().equals(directory));
        OwnedEmulatorRegistry.validate(Files.readAllBytes(directory.resolve("package.xml")));
        System.out.println("SDK_REGISTRY_VENDOR_READER_OK metadata_only=1 SDK_install_or_runtime=0");
    }
}
