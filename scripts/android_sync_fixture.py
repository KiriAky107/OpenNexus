"""Bounded delivery controls for a NEW owned Sync run, not product evidence.

Compose the fixed real service with this proxy only in an exclusive hosted owner.
It forwards each original request once and changes only response delivery after
the service completes. It never manufactures a revision, receipt or client ACK.
Actual PG/S3 provisioning, UI phases and provider observation are separate steps.
"""
from pathlib import Path
import re
from android_auth_fixture import (
    DeliveryProxy as OriginalDeliveryProxy,
    arm_control as original_arm_control,
    initialize_control as original_initialize_control,
    require,
)

MODES=frozenset(('normal','drop_revision','hold_revision','hold_changes','release'))


def routes(remote_vault):
    require(isinstance(remote_vault,str) and re.fullmatch(r'[A-Za-z0-9-]{1,80}',remote_vault),
        'OWNED_SYNC_TARGET_INVALID')
    prefix='/sync/v1/vaults/'+remote_vault
    return {('POST',prefix+'/revisions'):'revision',('GET',prefix+'/changes'):'changes'}


def initialize_control(owner):
    # A new exclusive namespace; never reuse or alter passed auth controls.
    root=owner/'sync-control'
    require(not root.exists() and not root.is_symlink(),'OWNED_SYNC_ALREADY_PREPARED')
    root.mkdir(mode=0o700)
    original_initialize_control(root)
    return root


def control_root(owner):
    root=owner/'sync-control'
    require(root.is_dir() and not root.is_symlink(),'OWNED_SYNC_NOT_PREPARED')
    return root


def arm_control(owner, operation, mode, expected_revision, *, retry=False):
    return original_arm_control(control_root(owner),operation,mode,expected_revision,
        retry=retry,allowed_modes=MODES)


class DeliveryProxy(OriginalDeliveryProxy):
    def __init__(self, app, owner: Path, remote_vault):
        super().__init__(app,control_root(owner),routes=routes(remote_vault),response_limit=2*1024*1024)
