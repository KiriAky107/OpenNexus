"""Bind a split release APK input to source and compiled ELF, before private signing.

This receipt never substitutes for intended-signer, device, upgrade or release proof.
"""
import argparse,hashlib,json,os,re,subprocess,tempfile,zipfile
from pathlib import Path,PurePosixPath
from android_release_native import same_runtime
from check_android_release_manifest import release_manifest,inspect_network_policy
from release_plan import atomic_json,build_source,digest,journal_lock,require
from verify_android_package import (MACHINES,TLS_COORDINATE,TLS_UPSTREAM,TLS_ARTIFACTS,TLS_CLASSES,
    TLS_SYMBOL,FOREGROUND_SYMBOL,BROKER_SYMBOL,verify_mobile_files,verify_generated_mobile_files)


def archive_inventory(apk,abi):
    require(abi in MACHINES,'ANDROID_RELEASE_ABI_UNSUPPORTED');inventory={}
    with zipfile.ZipFile(apk) as archive:
        entries=archive.infolist();names=[e.filename for e in entries]
        require(0<len(entries)<=4096 and len(set(names))==len(names),'ANDROID_RELEASE_ZIP_ENTRIES')
        require(sum(e.file_size for e in entries)<=768*1024**2,'ANDROID_RELEASE_ZIP_SIZE')
        natives=[]
        for entry in entries:
            name=entry.filename;parts=PurePosixPath(name)
            require(entry.orig_filename==name and not parts.is_absolute() and all(p not in ('.','..') for p in parts.parts) and '\\' not in name and ':' not in name,
                'ANDROID_RELEASE_ZIP_PATH')
            require(((entry.external_attr>>16)&0o170000)!=0o120000,'ANDROID_RELEASE_ZIP_LINK')
            if entry.is_dir():continue
            require(0<=entry.file_size<=512*1024**2,'ANDROID_RELEASE_ZIP_ENTRY_SIZE')
            if name.startswith('lib/'):natives.append(name)
            with archive.open(entry) as stream:value=hashlib.file_digest(stream,'sha256').hexdigest()
            inventory[name]=dict(bytes=entry.file_size,sha256=value)
        require(natives==[f'lib/{abi}/libopennexus_mobile.so'],'ANDROID_RELEASE_SPLIT_ABI_SET')
        require('AndroidManifest.xml' in inventory and any(re.fullmatch(r'classes(?:\d+)?\.dex',n) for n in inventory),
            'ANDROID_RELEASE_MANIFEST_OR_DEX_MISSING')
    return inventory


def tls_origin(path):
    value=json.loads(path.read_text('utf-8'))
    require(value.get('coordinate')==TLS_COORDINATE and value.get('upstream_commit')==TLS_UPSTREAM,'ANDROID_RELEASE_TLS_ORIGIN')
    artifacts=value.get('artifacts',[])
    require(len(artifacts)==2 and {x.get('extension') for x in artifacts}==set(TLS_ARTIFACTS),'ANDROID_RELEASE_TLS_ARTIFACT_SET')
    for item in artifacts:
        expected,size=TLS_ARTIFACTS[item['extension']]
        require(item.get('sha256')==expected and item.get('bytes')==size,'ANDROID_RELEASE_TLS_ARTIFACT_BYTES')
    return value


def compiled_release(apk,native_dir,abi,symbol_tool):
    compiled=native_dir/MACHINES[abi][1]/'release/libopennexus_mobile.so'
    require(compiled.is_file() and not compiled.is_symlink(),'ANDROID_RELEASE_NATIVE_INPUT_MISSING')
    before=digest(compiled);name=f'lib/{abi}/libopennexus_mobile.so'
    with tempfile.TemporaryDirectory(prefix='opennexus-release-code-') as directory:
        packaged=Path(directory)/'packaged.so'
        with zipfile.ZipFile(apk) as archive,archive.open(name) as source,packaged.open('xb') as target:
            size=0
            while block:=source.read(1024*1024):
                size+=len(block);require(size<=512*1024**2,'ANDROID_RELEASE_NATIVE_SIZE');target.write(block)
        equivalence=same_runtime(compiled,packaged,abi)
        table=subprocess.check_output([str(symbol_tool),'--defined-only','--dynamic',str(packaged)],timeout=90).decode('utf-8','strict')
        names=[line.split()[-1] for line in table.splitlines() if line.split()]
        require(names.count(TLS_SYMBOL)==names.count(FOREGROUND_SYMBOL)==1,'ANDROID_RELEASE_PRODUCTION_JNI_LINKAGE')
        require(BROKER_SYMBOL not in names,'ANDROID_RELEASE_DEBUG_CREDENTIAL_HOOK_PRESENT')
        markers=(b'mobile_sync_auth',b'auth_profile_choices',b'SYNC_AUTH_SELECTION_CHANGED',b'mobile_sync_cycle',b'sync_review_receipts')
        found=set();tail=b''
        with packaged.open('rb') as stream:
            while block:=stream.read(1024*1024):
                data=tail+block;found.update(v for v in markers if v in data);tail=data[-max(map(len,markers))+1:]
        require(len(found)==len(markers),'ANDROID_RELEASE_AUTH_OR_SYNC_ROUTES_MISSING')
        require(digest(compiled)==before,'ANDROID_RELEASE_COMPILED_INPUT_CHANGED')
        return dict(compiled_sha256=before,packaged_sha256=digest(packaged),packaged_bytes=packaged.stat().st_size,
            source_target=MACHINES[abi][1],profile='release',equivalence=equivalence,
            production_JNI_symbols=[TLS_SYMBOL,FOREGROUND_SYMBOL],debug_NativeCredentialProbe_roundTrip_export_absent=True,
            actual_TLS_or_credential_or_sync_execution_verified=False)


def inspect(root,commit,source_proof,apk,native_dir,sdk,abi):
    require(apk.is_file() and not apk.is_symlink(),'ANDROID_RELEASE_APK_INPUT')
    original_hash=digest(apk);before=json.loads(source_proof.read_text('utf-8'))
    require(before==build_source(root,commit),'ANDROID_RELEASE_SOURCE_OR_CHECKOUT_CHANGED')
    config=json.loads((root/'frontend/mobile/src-tauri/tauri.conf.json').read_text('utf-8'))
    require(config['identifier']=='cc.kronecker.opennexus','ANDROID_RELEASE_CONFIG_ID')
    tools=sdk/'build-tools/36.0.0';suffix='.exe' if os.name=='nt' else '';aapt=tools/('aapt2'+suffix)
    def run(args):return subprocess.check_output([str(v) for v in args],timeout=90).decode('utf-8','strict')
    badging=run([aapt,'dump','badging',apk]);xml=run([aapt,'dump','xmltree','--file','AndroidManifest.xml',apk])
    network_policy=inspect_network_policy(apk,aapt,xml)
    metadata=release_manifest(badging,xml,version=config['version'],version_code=config['bundle']['android']['versionCode'],network_policy=network_policy)
    inventory=archive_inventory(apk,abi)
    files=verify_mobile_files(apk,run([aapt,'dump','permissions',apk]))
    required=TLS_CLASSES+['Lcc/kronecker/opennexus/MainActivity;','Lcc/kronecker/opennexus/sync/SyncCredentialStore;']
    with zipfile.ZipFile(apk) as archive:
        dex=[archive.read(n) for n in inventory if re.fullmatch(r'classes(?:\d+)?\.dex',n)]
        require(all(any(n.encode() in data for data in dex) for n in required),'ANDROID_RELEASE_R8_REQUIRED_JVM_LINKAGE')
    origin=tls_origin(root/'frontend/mobile/src-tauri/gen/android/app/build/reports/platform-verifier/origin.json')
    prebuilt='windows-x86_64' if os.name=='nt' else 'linux-x86_64'
    symbol=sdk/'ndk/30.0.16248370/toolchains/llvm/prebuilt'/prebuilt/('bin/llvm-nm'+suffix)
    native=compiled_release(apk,native_dir,abi,symbol)
    kotlin=verify_generated_mobile_files(root)
    alignment=run([tools/('zipalign'+suffix),'-c','-P','16','4',apk])
    require(digest(apk)==original_hash and before==build_source(root,commit),'ANDROID_RELEASE_INPUT_CHANGED_DURING_VERIFY')
    return dict(schema=1,passed=True,kind='android-release-input',source_commit=commit,source_clean=True,
        source_proof=source_proof.name,apk=apk.name,apk_sha256=original_hash,apk_bytes=apk.stat().st_size,abi=abi,
        metadata=metadata,inventory=inventory,native_library=native,platform_TLS_origin=origin,network_security_policy=network_policy,
        files_plugin={**files,'source_sha256':kotlin},R8_required_JVM_descriptors=required,
        zip_alignment_page_size_kib=16,raw_badging=badging,raw_manifest_xmltree=xml,raw_alignment=alignment,
        intended_signer_verified=False,actual_native_runtime_verified=False,upgrade_verified=False,
        physical_phone_verified=False,published=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository-dir',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--commit',required=True);parser.add_argument('--source-proof',type=Path,required=True)
    parser.add_argument('--apk',type=Path,required=True);parser.add_argument('--native-dir',type=Path,required=True)
    parser.add_argument('--sdk',type=Path,required=True);parser.add_argument('--abi',choices=tuple(MACHINES),required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    with journal_lock(args.output):
        require(not args.output.exists(),'ANDROID_RELEASE_PACKAGE_ORIGINAL_OUTPUT_EXISTS')
        result=inspect(args.repository_dir.resolve(),args.commit,args.source_proof,args.apk,args.native_dir,args.sdk,args.abi)
        require(not args.output.exists(),'ANDROID_RELEASE_PACKAGE_ORIGINAL_OUTPUT_EXISTS');atomic_json(args.output,result)
    print(json.dumps({k:result[k] for k in ('passed','kind','source_commit','apk_sha256','apk_bytes','abi','intended_signer_verified','actual_native_runtime_verified','published')}))

if __name__=='__main__':main()
