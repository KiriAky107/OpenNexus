# Windows package verification

The manual [GitHub Windows package workflow](https://github.com/KiriAky107/OpenNexus/actions/workflows/windows-rc.yml)
builds `x86_64-pc-windows-msvc` with the shared `tauri.bundle.conf.json`.
Unsigned builds need no signing secrets. Signed builds add only signing fields
and require the controlled Windows certificate and Core signing key.

From a clean checkout, install locked backend packaging dependencies and frontend
dependencies, build Core, then build the installer:

```powershell
uv sync --frozen --group packaging --directory backend
pnpm --dir frontend install --frozen-lockfile
uv run --frozen --directory backend --group packaging python ../scripts/build-core.py
pnpm --dir frontend exec tauri build --target x86_64-pc-windows-msvc --features desktop --bundles nsis --config src-tauri/tauri.bundle.conf.json
uv run --frozen --directory backend --group packaging python ../scripts/verify-windows-package.py --target x86_64-pc-windows-msvc --native-smoke
```

Verification requires 7-Zip, Windows and an installed WebView2 Runtime; native
startup additionally requires an interactive Windows session. The ordinary
payload check can be run without `--native-smoke`. `--installer PATH` selects an
explicit final installer instead of discovering the target's single NSIS output.
Use the actual build target: GNU imports the SDK Loader dynamically, while MSVC
links its Loader statically. Both packages carry the prepared matching SDK DLL
beside Host, outside Core's strict manifest.

The verifier extracts the actual final installer, checks the exact Host-embedded
manifest, every Core filename/hash and dependency lock, matching Loader
architecture/hash, and Microsoft signatures of the SDK DLL and embedded Runtime
bootstrapper. Native startup launches this extracted Host/Core with only Windows
System32 on PATH and no developer Python environment variables. It verifies the
Core version, private storage, Canvas, inspector toggles, backlinks dialog and
normal exit. For a dynamically importing Host it also removes/restores the DLL
and requires the missing-dependency exit `0xC0000135`; this control is explicitly
inapplicable to a static Loader.

Native verification refuses to run while another OpenNexus process is open.
It temporarily points the product storage configuration at synthetic test data,
restores the exact prior bytes, and refuses to overwrite a concurrent change.
It never opens a real vault, and closes only its own process tree. Output under
`.build/windows` is ignored; `package-verification.json` records installer hash,
versions, OS build, loaded WebView2 version and the precise test scope. The
workflow uploads that report and the synthetic screenshot, not private logs or
WebView profiles. Failure blocks the workflow.

This validates extracted payload startup. Windows Server runner results are
recorded separately from Windows 10/11 installation acceptance. First installation,
alpha2 upgrade, uninstall, and connected/disconnected installation without Runtime
still require isolated Windows 10/11 machines or virtual machines. An unsigned
build must be described as unsigned; Microsoft component signatures do not sign
the OpenNexus Host or installer.
