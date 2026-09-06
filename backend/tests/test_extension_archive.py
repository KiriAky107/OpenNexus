import asyncio
import io
import stat
import zipfile

import pytest
from starlette.requests import Request

from app.errors import ApiError
from app.extensions import ExtensionError
from app.extensions.archive import install_zip
from app.extensions import archive as module


def zipped(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in files:
            if isinstance(name, str) and '\\' in name:
                entry = zipfile.ZipInfo()
                entry.filename = name  # Keep malicious separators on Windows too.
                name = entry
            archive.writestr(name, value)
    return output.getvalue()


@pytest.mark.parametrize('kind', ['skill', 'plugin'])
@pytest.mark.parametrize('prefix', ['', 'package/'])
def test_install_keeps_package_resources(tmp_path, kind, prefix):
    data = zipped([(prefix + kind + '.yaml', 'name: test'), (prefix + 'assets/说明.txt', 'hello')])
    root = install_zip(data, kind, tmp_path, lambda root: root)
    assert (root / 'assets/说明.txt').read_text() == 'hello'


@pytest.mark.parametrize('path', ['../outside', '/outside', 'C:/outside', 'a\\b', 'NUL.txt', 'a/../b', 'a./x'])
def test_unsafe_paths_rejected_and_cleaned(tmp_path, path):
    with pytest.raises(ApiError):
        install_zip(zipped([('skill.yaml', 'name: x'), (path, 'x')]), 'skill', tmp_path, lambda _: pytest.fail('must not install'))
    assert list(tmp_path.iterdir()) == []


def test_links_duplicates_and_size_limits(tmp_path, monkeypatch):
    link = zipfile.ZipInfo('link')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    cases = [zipped([(link, '../outside')]), zipped([('skill.yaml', 'x'), ('SKILL.yaml', 'x')]), b'not a zip']
    for data in cases:
        with pytest.raises(ApiError):
            install_zip(data, 'skill', tmp_path, lambda _: pytest.fail('must not install'))
        assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(module, 'MAX_EXPANDED_BYTES', 3)
    with pytest.raises(ApiError, match='50 MiB'):
        install_zip(zipped([('skill.yaml', 'xxxxx')]), 'skill', tmp_path, lambda _: None)
    assert list(tmp_path.iterdir()) == []


def test_manifest_validation_failure_preserved_and_cleaned(tmp_path):
    def reject(_):
        raise ExtensionError('BAD_MANIFEST', 'invalid manifest')
    with pytest.raises(ExtensionError, match='invalid manifest'):
        install_zip(zipped([('plugin.yaml', 'x')]), 'plugin', tmp_path, reject)
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ApiError, match='plugin.yaml'):
        install_zip(zipped([('skill.yaml', 'x')]), 'plugin', tmp_path, reject)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('kind', ['skill', 'plugin'])
def test_upload_route_uses_real_manifest_validation(tmp_path, monkeypatch, kind):
    from app import routes
    from app.container import build_container
    runtime = build_container()
    monkeypatch.setattr(routes, 'container', runtime)
    data = zipped([(kind + '.yaml', f'id: zip-example\nname: ZIP example\nversion: 1.0.0\ndescription: test\n')])
    sent = False
    async def receive():
        nonlocal sent
        assert not sent
        sent = True
        return {'type': 'http.request', 'body': data, 'more_body': False}
    request = Request({'type': 'http', 'method': 'POST', 'headers': []}, receive)
    try:
        result = asyncio.run(getattr(routes, f'install_{kind}_zip')(request))
        assert getattr(result.manifest, kind + '_id') == 'zip-example'
        assert not result.enabled
    finally:
        runtime.plugins.shutdown()
