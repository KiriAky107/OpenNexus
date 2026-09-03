"""Provider 凭据解析及本地加密存储。"""

import json
import os
import re
import threading
from pathlib import Path
from typing import ClassVar, Protocol

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_CREDENTIAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PLUGIN_CREDENTIAL_PREFIX = "plugin."
_MCP_CREDENTIAL_PREFIX = "mcp."


class CredentialStoreError(RuntimeError):
    pass


class CredentialResolver(Protocol):
    def resolve(self, credential_id: str | None) -> str | None: ...


def validate_provider_credential_id(credential_id: str | None) -> None:
    """阻止 Provider 和通用凭据 API 跨入 Plugin 私有命名空间。"""

    if credential_id and credential_id.casefold().startswith(_PLUGIN_CREDENTIAL_PREFIX):
        raise CredentialStoreError(
            "Credential namespace is reserved for Plugin settings."
        )
    if credential_id and credential_id.casefold().startswith(_MCP_CREDENTIAL_PREFIX):
        raise CredentialStoreError("Credential namespace is reserved for MCP settings.")


class EnvironmentCredentialResolver:
    """解析由桌面 Host 注入 Sidecar 进程的临时凭证上下文。"""

    _development_aliases: ClassVar[dict[str, str]] = {
        "openai": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }

    def resolve(self, credential_id: str | None) -> str | None:
        if not credential_id:
            return None
        normalized = re.sub(r"[^A-Za-z0-9]", "_", credential_id).upper()
        injected = os.getenv(f"AINOTE_CREDENTIAL_{normalized}")
        if injected:
            return injected
        alias = self._development_aliases.get(credential_id.lower())
        return os.getenv(alias) if alias else None


class EncryptedCredentialStore:
    """将本地开发凭据作为 Fernet 密文存储，Provider 使用时按 ID 解密。"""

    # TODO(security): 桌面 Host 接入后将主密钥迁移到系统钥匙串/凭据保险库。

    def __init__(self) -> None:
        self._lock = threading.RLock()

    @staticmethod
    def _validate_id(credential_id: str) -> None:
        if not _CREDENTIAL_ID.fullmatch(credential_id):
            raise CredentialStoreError("Credential ID contains unsupported characters.")

    @staticmethod
    def _paths() -> tuple[Path, Path]:
        directory = get_settings().data_dir / "credentials"
        return directory / "master.key", directory / "credentials.json"

    @staticmethod
    def _restrict(path: Path, mode: int) -> None:
        try:
            path.chmod(mode)
        except OSError:
            pass

    def _fernet(self) -> Fernet:
        key_path, _ = self._paths()
        environment_key = os.getenv("APP_CREDENTIAL_MASTER_KEY")
        if environment_key:
            try:
                return Fernet(environment_key.encode("ascii"))
            except (ValueError, UnicodeEncodeError) as exc:
                raise CredentialStoreError(
                    "APP_CREDENTIAL_MASTER_KEY is invalid."
                ) from exc

        key_path.parent.mkdir(parents=True, exist_ok=True)
        self._restrict(key_path.parent, 0o700)
        if not key_path.exists():
            # 先写临时文件再原子替换，避免异常退出留下半截主密钥。
            temporary = key_path.with_suffix(".tmp")
            temporary.write_bytes(Fernet.generate_key())
            self._restrict(temporary, 0o600)
            try:
                temporary.replace(key_path)
            except FileExistsError:
                temporary.unlink(missing_ok=True)
        self._restrict(key_path, 0o600)
        try:
            return Fernet(key_path.read_bytes().strip())
        except (OSError, ValueError) as exc:
            raise CredentialStoreError(
                "Credential master key cannot be loaded."
            ) from exc

    def _read_tokens(self) -> dict[str, str]:
        _, store_path = self._paths()
        if not store_path.exists():
            return {}
        try:
            data = json.loads(store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialStoreError(
                "Encrypted credential store cannot be loaded."
            ) from exc
        if not isinstance(data, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in data.items()
        ):
            raise CredentialStoreError(
                "Encrypted credential store has an invalid format."
            )
        return data

    def _write_tokens(self, tokens: dict[str, str]) -> None:
        _, store_path = self._paths()
        temporary = store_path.with_suffix(".tmp")
        try:
            store_path.parent.mkdir(parents=True, exist_ok=True)
            self._restrict(store_path.parent, 0o700)
            temporary.write_text(
                json.dumps(tokens, ensure_ascii=True, sort_keys=True),
                encoding="utf-8",
            )
            self._restrict(temporary, 0o600)
            # 凭据表同样使用原子替换，确保并发读取只会看到完整 JSON。
            temporary.replace(store_path)
            self._restrict(store_path, 0o600)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise CredentialStoreError(
                "Encrypted credential store cannot be written."
            ) from exc

    def put(self, credential_id: str, secret: str) -> None:
        self._validate_id(credential_id)
        if not secret:
            raise CredentialStoreError("Credential secret cannot be empty.")
        with self._lock:
            tokens = self._read_tokens()
            token = self._fernet().encrypt(secret.encode("utf-8")).decode("ascii")
            tokens[credential_id] = token
            self._write_tokens(tokens)

    def resolve(self, credential_id: str | None) -> str | None:
        if not credential_id:
            return None
        self._validate_id(credential_id)
        with self._lock:
            token = self._read_tokens().get(credential_id)
            if token is None:
                return None
            try:
                return self._fernet().decrypt(token.encode("ascii")).decode("utf-8")
            except (InvalidToken, UnicodeDecodeError) as exc:
                raise CredentialStoreError("Credential cannot be decrypted.") from exc

    def has(self, credential_id: str) -> bool:
        self._validate_id(credential_id)
        with self._lock:
            return credential_id in self._read_tokens()

    def delete(self, credential_id: str) -> bool:
        self._validate_id(credential_id)
        with self._lock:
            tokens = self._read_tokens()
            removed = tokens.pop(credential_id, None) is not None
            if removed:
                self._write_tokens(tokens)
            return removed

    def delete_many(self, credential_ids: list[str]) -> set[str]:
        """用一次原子替换删除多个凭据，避免插件卸载只删除部分 Secret。"""

        for credential_id in credential_ids:
            self._validate_id(credential_id)
        with self._lock:
            tokens = self._read_tokens()
            removed = {
                credential_id
                for credential_id in credential_ids
                if credential_id in tokens
            }
            if removed:
                for credential_id in removed:
                    del tokens[credential_id]
                self._write_tokens(tokens)
            return removed

    def move_many(self, replacements: dict[str, str]) -> None:
        """原子迁移凭据 ID，直接移动密文且不覆盖已经写入的新凭据。"""

        for old_id, new_id in replacements.items():
            self._validate_id(old_id)
            self._validate_id(new_id)
        with self._lock:
            tokens = self._read_tokens()
            changed = False
            for old_id, new_id in replacements.items():
                if old_id != new_id and old_id in tokens:
                    tokens.setdefault(new_id, tokens.pop(old_id))
                    changed = True
            if changed:
                self._write_tokens(tokens)


class ChainedCredentialResolver:
    def __init__(self, *resolvers: CredentialResolver) -> None:
        self._resolvers = resolvers

    def resolve(self, credential_id: str | None) -> str | None:
        # 顺序即优先级：调用方可让 Host 注入值覆盖本地开发凭据。
        for resolver in self._resolvers:
            value = resolver.resolve(credential_id)
            if value:
                return value
        return None


class ProviderCredentialResolver:
    """Provider 专用防御层，避免配置绕过 HTTP 校验读取 Plugin Secret。"""

    def __init__(self, delegate: CredentialResolver) -> None:
        self._delegate = delegate

    def resolve(self, credential_id: str | None) -> str | None:
        validate_provider_credential_id(credential_id)
        return self._delegate.resolve(credential_id)
