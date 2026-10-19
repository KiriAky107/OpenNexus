"""Check a signed stable Android APK's manifest and certificate, not its runtime."""
import argparse,hashlib,json,os,re,subprocess,zipfile
from pathlib import Path
from release_plan import VERSION,atomic_json,digest,journal_lock,require
from verify_android_package import package_identity,TLS_ARTIFACTS,TLS_UPSTREAM

PACKAGE='cc.kronecker.opennexus'
POLICY_IDS={'debuggable':0x0101000f,'testOnly':0x01010272,'allowBackup':0x01010280,
    'usesCleartextTraffic':0x010104ec,'networkSecurityConfig':0x01010527}
ANDROID_NAMESPACE='http://schemas.android.com/apk/res/android:'
# Reviewed resource inside the already SHA-pinned rustls-platform-verifier AAR.
# Its default is deny-cleartext with an exact HTTP CRL host list, not custom roots.
TLS_POLICY_SOURCE_SHA256='a81434f9139f3780412c358d1cc782fe25339182d3dc141b35413f23d2b29f6a'
TLS_POLICY_CANONICAL_SHA256='7029e2f22ca3c014f9167d286a1ce75732debba435c92e1b1f37a252ba76986c'
TLS_POLICY_DOMAINS_SHA256='7c0a8fc0bcfa441e2e922e1d5b61831c1439ba27c3423475b42e352cc30297b2'


def policy_attributes(xml):
    blocks=re.split(r'(?m)^\s*E: ',xml)
    require(not any(block.startswith('instrumentation ') for block in blocks),'ANDROID_RELEASE_INSTRUMENTATION_PRESENT')
    applications=[block for block in blocks if block.startswith('application ')]
    require(len(applications)==1,'ANDROID_RELEASE_APPLICATION_COUNT')
    application=applications[0]
    # Current aapt2 prints the full namespace URI and literal booleans. Older
    # versions use android: and (type0x12) integers. Bind either spelling to the
    # real framework resource ID; unknown namespaces/IDs cannot hide a true flag.
    attributes={name:[] for name in POLICY_IDS}
    names={prefix+name:name for prefix in ('android:',ANDROID_NAMESPACE) for name in POLICY_IDS}
    ids={value:name for name,value in POLICY_IDS.items()}
    for line in application.splitlines():
        match=re.fullmatch(r'\s*A: ([^\r\n(]+)\(0x([0-9a-fA-F]+)\)=(.*)',line)
        if not match:
            require(not any(label in line for label in names),'ANDROID_RELEASE_POLICY_ATTRIBUTE_FORMAT')
            continue
        label,number,raw=match.groups();number=int(number,16)
        by_name=names.get(label);by_id=ids.get(number)
        if by_name is None and by_id is None:continue
        require(by_name is not None and by_name==by_id,'ANDROID_RELEASE_POLICY_ATTRIBUTE_NAMESPACE_NAME_OR_ID')
        attributes[by_name].append(raw)
    return attributes


def network_resource(resources,reference):
    require(type(reference) is str and re.fullmatch(r'@0x7f[0-9a-f]{6}',reference),'ANDROID_RELEASE_NETWORK_REFERENCE')
    require(type(resources) is str and len(resources)<=4*1024**2,'ANDROID_RELEASE_RESOURCE_TABLE_BOUND')
    blocks=re.split(r'(?m)^\s*resource ',resources)
    found=[v for v in blocks[1:] if v.startswith(reference[1:]+' ')]
    require(len(found)==1,'ANDROID_RELEASE_NETWORK_RESOURCE_ID')
    lines=found[0].strip().splitlines()
    require(lines[0]==reference[1:]+' xml/network_security_config' and len(lines)==2,
        'ANDROID_RELEASE_NETWORK_RESOURCE_VARIANTS')
    match=re.fullmatch(r'\s*\(\) \(file\) (res/(?:[A-Za-z0-9_-]+/){0,2}[A-Za-z0-9_.-]+\.xml) type=XML',lines[1])
    require(match is not None and '..' not in match[1].split('/'),'ANDROID_RELEASE_NETWORK_RESOURCE_PATH')
    return match[1]


def network_tree(text):
    """Parse only the unqualified boolean/domain tree used by the pinned AAR."""
    require(type(text) is str and 0<len(text)<=128*1024,'ANDROID_RELEASE_NETWORK_TREE_BOUND')
    root=None;stack=[]
    for line in text.splitlines():
        depth=len(line)-len(line.lstrip(' '));value=line[depth:]
        element=re.fullmatch(r'E: ([a-z][a-z-]*)(?: \(line=[0-9]+\))?',value)
        if element:
            while stack and depth<=stack[-1][0]:stack.pop()
            node=[element[1],{},'',[]]
            if stack:stack[-1][1][3].append(node)
            else:
                require(root is None and depth==0,'ANDROID_RELEASE_NETWORK_TREE_ROOT')
                root=node
            stack.append((depth,node));continue
        require(stack and depth>stack[-1][0],'ANDROID_RELEASE_NETWORK_TREE_STRUCTURE')
        node=stack[-1][1]
        attribute=re.fullmatch(r'A: ([A-Za-z]+)=(true|false)',value)
        content=re.fullmatch(r"T: '([a-z0-9.-]+)'",value)
        if attribute:
            require(attribute[1] not in node[1] and not node[2] and not node[3],
                'ANDROID_RELEASE_NETWORK_DUPLICATE_OR_LATE_ATTRIBUTE')
            node[1][attribute[1]]=attribute[2]
        elif content:
            require(not node[2] and not node[3],'ANDROID_RELEASE_NETWORK_DUPLICATE_CONTENT')
            node[2]=content[1]
        else:require(False,'ANDROID_RELEASE_NETWORK_UNREVIEWED_TREE_CONTENT')
    require(root is not None,'ANDROID_RELEASE_NETWORK_TREE_ROOT')
    def canonical(node):return [node[0],sorted(node[1].items()),node[2],[canonical(c) for c in node[3]]]
    return canonical(root)


def reviewed_network_tree(text):
    value=network_tree(text)
    canonical=json.dumps(value,ensure_ascii=True,separators=(',',':')).encode()
    require(hashlib.sha256(canonical).hexdigest()==TLS_POLICY_CANONICAL_SHA256,
        'ANDROID_RELEASE_NETWORK_POLICY_DIFFERS_FROM_PINNED_TLS_AAR')
    domains=[n[2] for n in value[3][1][3]]
    require(len(domains)==376 and hashlib.sha256(('\n'.join(domains)+'\n').encode()).hexdigest()==TLS_POLICY_DOMAINS_SHA256,
        'ANDROID_RELEASE_NETWORK_REVOCATION_ALLOWLIST_CHANGED')
    return dict(base_cleartext_permitted=False,certificate_revocation_http_domains=376,
        certificate_revocation_domains_sha256=TLS_POLICY_DOMAINS_SHA256,
        custom_trust_anchors_or_debug_overrides=False,inherited_system_trust_unmodified=True)


def inspect_network_policy(apk,aapt,xml):
    values=policy_attributes(xml)['networkSecurityConfig']
    if not values:return None
    require(len(values)==1,'ANDROID_RELEASE_NETWORK_CONFIG_UNREVIEWED')
    resources=subprocess.check_output([str(aapt),'dump','resources',str(apk)],timeout=90).decode('utf-8','strict')
    path=network_resource(resources,values[0])
    with zipfile.ZipFile(apk) as bundle:
        entries=[v for v in bundle.infolist() if v.filename==path]
        require(len(entries)==1 and entries[0].file_size<=128*1024
            and ((entries[0].external_attr>>16)&0o170000)!=0o120000,'ANDROID_RELEASE_NETWORK_COMPILED_RESOURCE')
        original=bundle.read(entries[0])
    tree=subprocess.check_output([str(aapt),'dump','xmltree','--file',path,str(apk)],timeout=90).decode('utf-8','strict')
    summary=reviewed_network_tree(tree)
    return dict(kind='pinned-rustls-certificate-revocation-policy',reference=values[0],resource_path=path,
        compiled_resource_sha256=hashlib.sha256(original).hexdigest(),source_AAR_sha256=TLS_ARTIFACTS['aar'][0],
        source_AAR_upstream_commit=TLS_UPSTREAM,source_XML_sha256=TLS_POLICY_SOURCE_SHA256,
        canonical_policy_sha256=TLS_POLICY_CANONICAL_SHA256,summary=summary,raw_policy_xmltree=tree)


def release_manifest(badging,xml,*,version,version_code,network_policy=None):
    require(isinstance(version,str) and VERSION.fullmatch(version),'ANDROID_RELEASE_VERSION_INVALID')
    require(type(version_code) is int and version_code>0,'ANDROID_RELEASE_CODE_INVALID')
    package_identity(badging,package=PACKAGE,version=version,version_code=version_code)
    attributes=policy_attributes(xml)
    def values(name):return attributes[name]
    def disabled(name,required):
        found=values(name)
        require(len(found)<=1 and (bool(found) or not required),'ANDROID_RELEASE_FLAG_'+name.upper())
        if not found:return False
        value=re.fullmatch(r'\(type 0x12\)0x([0-9a-fA-F]{1,8})',found[0].strip())
        require(found[0].strip()=='false' or value is not None and int(value[1],16)==0,'ANDROID_RELEASE_FLAG_'+name.upper())
        return False
    configured=values('networkSecurityConfig')
    if configured:
        require(len(configured)==1 and type(network_policy) is dict
            and network_policy.get('kind')=='pinned-rustls-certificate-revocation-policy'
            and network_policy.get('reference')==configured[0]
            and network_policy.get('source_AAR_sha256')==TLS_ARTIFACTS['aar'][0]
            and network_policy.get('source_AAR_upstream_commit')==TLS_UPSTREAM
            and network_policy.get('source_XML_sha256')==TLS_POLICY_SOURCE_SHA256
            and network_policy.get('canonical_policy_sha256')==TLS_POLICY_CANONICAL_SHA256,
            'ANDROID_RELEASE_NETWORK_CONFIG_UNREVIEWED')
        require(reviewed_network_tree(network_policy.get('raw_policy_xmltree'))==network_policy.get('summary'),
            'ANDROID_RELEASE_NETWORK_POLICY_SUMMARY_CHANGED')
    else:require(network_policy is None,'ANDROID_RELEASE_NETWORK_POLICY_WITHOUT_REFERENCE')
    result=dict(application_id=PACKAGE,version=version,version_code=version_code,min_sdk=24,target_sdk=36,
        debuggable=disabled('debuggable',False),test_only=disabled('testOnly',False),
        uses_cleartext_traffic=disabled('usesCleartextTraffic',True),allow_backup=disabled('allowBackup',True))
    if configured:result['network_security_policy']=network_policy['summary']
    return result

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
    network_policy=inspect_network_policy(apk,aapt,xml)
    metadata=release_manifest(badging,xml,version=version,version_code=version_code,network_policy=network_policy)
    certificates=run([java,'-Xmx256m','-jar',jar,'verify','--verbose','--print-certs','--min-sdk-version','24',apk])
    signer=release_certificate(certificates,certificate)
    require(digest(apk)==expected_sha256,'ANDROID_RELEASE_APK_HASH_CHANGED')
    return dict(passed=True,scope='Signed stable APK identity, manifest flags and certificate only',
        apk_sha256=expected_sha256,apk_bytes=apk.stat().st_size,signer_certificate_sha256=signer,metadata=metadata,
        raw_badging=badging,raw_manifest_xmltree=xml,raw_apksigner=certificates,network_security_policy=network_policy,
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
