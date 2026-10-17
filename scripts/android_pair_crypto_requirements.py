"""Export three existing frozen backend wheels for an isolated CI tool overlay.

The production Sync environment is not changed. Workflow installation must use
--target, --no-deps, --require-hashes and --only-binary :all: for this overlay.
No dependency is downloaded or installed by this exporter.
"""
import json
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit


EXPECTED={
    'cffi':('2.1.1','cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.whl','a931079504ecc49efed7744c476a5c343a92fabf66dec2db95edb1b2fdc770e2'),
    'cryptography':('50.0.1','cp311-abi3-manylinux_2_34_x86_64.whl','51afcfceb15597cf2635068e4ac9a56b2abde622edde17f37d85fd7b5306497a'),
    'pycparser':('3.0','py3-none-any.whl','b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992'),
}


def requirement_lines(lock):
    result=[]
    for name,(version,tag,digest) in EXPECTED.items():
        packages=[p for p in lock['package'] if p['name']==name]
        if len(packages)!=1 or packages[0]['version']!=version:raise ValueError('PAIR_TOOL_LOCK_CHANGED')
        wheels=[w for w in packages[0]['wheels'] if w['url'].endswith('/'+name+'-'+version+'-'+tag)]
        if len(wheels)!=1:raise ValueError('PAIR_TOOL_WHEEL_AMBIGUOUS')
        wheel=wheels[0];url=urlsplit(wheel['url'])
        if url.scheme!='https' or url.hostname!='files.pythonhosted.org' or url.query or url.fragment or url.username or url.password \
                or not re.fullmatch(r'/packages/[A-Za-z0-9/_.-]+',url.path) or wheel['hash']!='sha256:'+digest:
            raise ValueError('PAIR_TOOL_WHEEL_ORIGINAL_CHANGED')
        result.append(wheel['url']+' --hash=sha256:'+digest)
    return '\n'.join(result)+'\n'


def main():
    from android_auth_fixture import require_hosted
    require_hosted()  # Before local lock/file access; this is hosted tooling only.
    root=Path(__file__).resolve().parents[1]
    print(requirement_lines(tomllib.loads((root/'backend/uv.lock').read_text(encoding='utf-8'))),end='')


if __name__=='__main__':main()
