package com.android.sdklib.tool;

import com.android.utils.ILogger;
import com.android.utils.NullLogger;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.file.Path;

/** Actual vendor init/root lookup only: no run(), createAvd(), installer or emulator. */
public final class OwnedAvdRootCheck {
    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new AssertionError();
        Path global = Path.of(args[0]).toRealPath(), descriptor = Path.of(args[1]).toRealPath();
        Path owned = descriptor.getParent().getParent();
        if (!owned.toString().equals(System.getenv("ANDROID_HOME"))) throw new AssertionError();
        Constructor<AvdManagerCli> constructor = AvdManagerCli.class.getDeclaredConstructor(ILogger.class);
        Method init = AvdManagerCli.class.getDeclaredMethod("init");
        Field sdk = AvdManagerCli.class.getDeclaredField("mOsSdkFolder");
        constructor.setAccessible(true); init.setAccessible(true); sdk.setAccessible(true);
        Path[] references = {global.resolve("cmdline-tools/latest"), descriptor};
        Path[] expected = {global, owned};
        for (int i = 0; i < references.length; i++) {
            System.setProperty("com.android.sdkmanager.toolsdir", references[i].toString());
            AvdManagerCli cli = constructor.newInstance(NullLogger.getLogger());
            init.invoke(cli);
            if (!Path.of((String)sdk.get(cli)).toRealPath().equals(expected[i])) throw new AssertionError();
        }
        System.out.println("SDK_AVD_ROOT_CONTRACT_OK metadata_only=1 AVD_created=0");
    }
}
