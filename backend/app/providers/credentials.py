import os
import re
from typing import Protocol


class CredentialResolver(Protocol):
    def resolve(self, credential_id: str | None) -> str | None: ...


class EnvironmentCredentialResolver:
    """解析由桌面 Host 注入 Sidecar 进程的临时凭证上下文。"""

    def resolve(self, credential_id: str | None) -> str | None:
        if not credential_id:
            return None
        normalized = re.sub(r"[^A-Za-z0-9]", "_", credential_id).upper()
        return os.getenv(f"AINOTE_CREDENTIAL_{normalized}")
