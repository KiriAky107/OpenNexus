"""Retain failed release compilation originals without admitting them as verified inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil


def collect(root, native, output, tools_source, caller, product):
    root, native, output = Path(root).resolve(), Path(native).resolve(), Path(output).resolve()
    assert re.fullmatch(r'[0-9a-f]{40}', caller) and re.fullmatch(r'[0-9a-f]{40}', product)
    assert native.is_relative_to(root / '.build') and output.is_relative_to(root / '.build') and not output.exists()
    output.mkdir(parents=True)
    items = {};total = 0
    def copy(path, name):
        nonlocal total
        if not path.is_file(): return
        assert not path.is_symlink()
        size = path.stat().st_size;total += size
        assert size <= 768 * 1024**2 and total <= 2 * 1024**3
        def sha(value):
            with value.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
        before = sha(path);destination = output / name;destination.parent.mkdir(parents=True, exist_ok=True)
        with path.open('rb') as source, destination.open('xb') as target:
            shutil.copyfileobj(source,target,1024*1024)
        items[name] = dict(bytes=destination.stat().st_size,sha256=sha(destination),source_sha256_before=before,
            source_sha256_after=sha(path),source_bytes_stable=False)
        items[name]['source_bytes_stable'] = items[name]['sha256'] == items[name]['source_sha256_before'] == items[name]['source_sha256_after']
    apks = root / 'frontend/mobile/src-tauri/gen/android/app/build/outputs/apk'
    originals = sorted(apks.rglob('*.apk')) if apks.is_dir() else []
    assert len(originals) <= 8
    for path in originals:
        assert path.resolve().is_relative_to(apks.resolve())
        copy(path, 'apks/' + path.relative_to(apks).as_posix())
    for target in ('aarch64-linux-android', 'x86_64-linux-android'):
        path = native / target / 'release/libopennexus_mobile.so'
        assert path.resolve().is_relative_to(native)
        copy(path,'compiled/' + target + '/libopennexus_mobile.so')
    copy(root / '.build/source-provenance' / (product + '.json'), 'source-provenance.json')
    tools_source = Path(tools_source)
    if tools_source.is_file():
        selection = json.loads(tools_source.read_text('utf-8'))
        assert type(selection) is dict and set(selection) == {'schema','tools_commit','compiled_commit','files_sha256'}
        assert type(selection['schema']) is int and selection['schema'] == 1
        assert selection['tools_commit'] == caller and selection['compiled_commit'] == product
        assert type(selection['files_sha256']) is dict and 1 <= len(selection['files_sha256']) <= 32
        assert all(re.fullmatch(r'[A-Za-z0-9_.-]+\.py', name) and type(value) is str
            and re.fullmatch(r'[0-9a-f]{64}', value) for name,value in selection['files_sha256'].items())
        copy(tools_source, 'tools-source.json')
    copy(root / 'frontend/mobile/src-tauri/gen/android/app/build/reports/platform-verifier/origin.json', 'platform-verifier-origin.json')
    record = dict(schema=1,kind='android-release-attempt-not-verified',passed=False,caller_commit=caller,
        declared_product_commit=product,files=items,original_APK_files=len(originals),
        no_signing_or_runtime_or_upgrade_or_publication_claim=True,private_signing_material_collected=False)
    with (output / 'attempt-not-verified.json').open('x',encoding='utf-8') as stream:
        json.dump(record,stream,sort_keys=True,indent=2);stream.write('\n')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','native-dir','output','tools-source'):parser.add_argument('--'+name,type=Path,required=True)
    for name in ('caller-commit','product-commit'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();value=collect(args.root,args.native_dir,args.output,args.tools_source,args.caller_commit,args.product_commit)
    print(json.dumps(dict(kind=value['kind'],passed=False,files=len(value['files']),original_APKs=value['original_APK_files'])))


if __name__=='__main__':main()
