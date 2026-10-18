"""Check a signed stable Android APK's manifest and certificate, not its runtime."""
import argparse,json,os,re,subprocess
from pathlib import Path
from release_plan import VERSION,atomic_json,digest,journal_lock,require
from verify_android_package import package_identity

PACKAGE='cc.kronecker.opennexus'

def release_manifest(badging,xml,*,version,version_code):
    require(isinstance(version,str) and VERSION.fullmatch(version),'ANDROID_RELEASE_VERSION_INVALID')
    require(type(version_code) is int and version_code>0,'ANDROID_RELEASE_CODE_INVALID')
    package_identity(badging,package=PACKAGE,version=version,version_code=version_code)
    blocks=re.split(r'(?m)^\s*E: ',xml)
    require(not any(block.startswith('instrumentation ') for block in blocks),'ANDROID_RELEASE_INSTRUMENTATION_PRESENT')
    applications=[block for block in blocks if block.startswith('application ')]
    require(len(applications)==1,'ANDROID_RELEASE_APPLICATION_COUNT')
    application=applications[0]
    def values(name):return re.findall(r'(?m)^\s*A: android:'+re.escape(name)+r'\(0x[0-9a-fA-F]+\)=(.*)$',application)
    def disabled(name,required):
        found=values(name)
        require(len(found)<=1 and (bool(found) or not required),'ANDROID_RELEASE_FLAG_'+name.upper())
        if not found:return False
        value=re.fullmatch(r'\(type 0x12\)0x([0-9a-fA-F]{1,8})',found[0].strip())
        require(value is not None and int(value[1],16)==0,'ANDROID_RELEASE_FLAG_'+name.upper())
        return False
    require(not values('networkSecurityConfig'),'ANDROID_RELEASE_NETWORK_CONFIG_UNREVIEWED')
    return dict(application_id=PACKAGE,version=version,version_code=version_code,min_sdk=24,target_sdk=36,
        debuggable=disabled('debuggable',False),test_only=disabled('testOnly',False),
        uses_cleartext_traffic=disabled('usesCleartextTraffic',True),allow_backup=disabled('allowBackup',True))

def release_certificate(text,expected):
    require(isinstance(expected,str) and re.fullmatch(r'[0-9a-fA-F]{64}',expected),'ANDROID_RELEASE_CERTIFICATE_INVALID')
    fingerprints=re.findall(r'^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]{64})$',text,re.M)
    require([v.lower() for v in fingerprints]==[expected.lower()],'ANDROID_RELEASE_CERTIFICATE_MISMATCH')
    require(re.search(r'^Verified using v2 scheme \(APK Signature Scheme v2\): true$',text,re.M),
        'ANDROID_RELEASE_API24_SIGNATURE_REQUIRED')
    return expected.lower()

def inspect(apk,sdk,java,*,version,version_code,expected_sha256,certificate):
    require(apk.is_file() and not apk.is_symlink(),'ANDROID_RELEASE_APK_MISSING')
    require(re.fullmatch(r'[0-9a-f]{64}',expected_sha256 or ''),'ANDROID_RELEASE_EXPECTED_HASH_INVALID')
    require(digest(apk)==expected_sha256,'ANDROID_RELEASE_APK_HASH_CHANGED')
    tools=sdk/'build-tools/36.0.0';aapt=tools/('aapt2.exe' if os.name=='nt' else 'aapt2')
    jar=tools/'lib/apksigner.jar';require(aapt.is_file() and jar.is_file(),'ANDROID_RELEASE_TOOLS_MISSING')
    def run(args):return subprocess.check_output([str(v) for v in args],timeout=90).decode('utf-8','strict')
    badging=run([aapt,'dump','badging',apk]);xml=run([aapt,'dump','xmltree','--file','AndroidManifest.xml',apk])
    metadata=release_manifest(badging,xml,version=version,version_code=version_code)
    certificates=run([java,'-Xmx256m','-jar',jar,'verify','--verbose','--print-certs','--min-sdk-version','24',apk])
    signer=release_certificate(certificates,certificate)
    require(digest(apk)==expected_sha256,'ANDROID_RELEASE_APK_HASH_CHANGED')
    return dict(passed=True,scope='Signed stable APK identity, manifest flags and certificate only',
        apk_sha256=expected_sha256,apk_bytes=apk.stat().st_size,signer_certificate_sha256=signer,metadata=metadata,
        raw_badging=badging,raw_manifest_xmltree=xml,raw_apksigner=certificates,
        native_runtime_verified=False,source_bytes_verified=False,upgrade_verified=False,physical_phone_verified=False)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk',type=Path,required=True);parser.add_argument('--sdk',type=Path,required=True)
    parser.add_argument('--java',type=Path,required=True);parser.add_argument('--version',required=True)
    parser.add_argument('--version-code',type=int,required=True);parser.add_argument('--expected-sha256',required=True)
    parser.add_argument('--certificate-sha256',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with journal_lock(args.output):
        require(not args.output.exists(),'ANDROID_RELEASE_ORIGINAL_OUTPUT_EXISTS')
        result=inspect(args.apk,args.sdk,args.java,version=args.version,version_code=args.version_code,
            expected_sha256=args.expected_sha256,certificate=args.certificate_sha256)
        require(not args.output.exists(),'ANDROID_RELEASE_ORIGINAL_OUTPUT_EXISTS')
        atomic_json(args.output,result)
    print(json.dumps({k:v for k,v in result.items() if not k.startswith('raw_')}))

if __name__=='__main__':main()
