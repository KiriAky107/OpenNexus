"""单写入者门禁使用受控目录，拒绝 Web 绕过已迁移的桌面 Vault。"""

import pytest
from app.config import get_settings
from app.errors import ApiError
from app.services.coordination import web_vault_ownership


def test_web_refuses_desktop_owned_vault():
    root = get_settings().vault_path
    (root / '.ainote').mkdir(parents=True)
    (root / '.ainote' / 'host.sqlite3').write_bytes(b'fixture-marker')
    with pytest.raises(ApiError) as error:
        with web_vault_ownership():
            pytest.fail('不应取得桌面写入权')
    assert error.value.code == 'WORKSPACE_OWNER_DESKTOP'


def test_web_lock_is_exclusive_and_released():
    with web_vault_ownership():
        with pytest.raises(ApiError):
            with web_vault_ownership():
                pytest.fail('不应同时持有锁')
    with web_vault_ownership():
        pass
