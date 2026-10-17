"""Run only a new hosted packaged Desktop bootstrap, not an Android pair claim."""
import json
import os
from pathlib import Path
import re
import urllib.request
from urllib.parse import urlsplit

from android_desktop_handoff import canonical, require
from android_desktop_product import DESKTOP_SOURCE, INSTALLER_SHA256, claim_root, digest, extract_installer, start_product

ANDROID_SOURCE = '2a49774d7261b968e25583f8f9030ca3b4f9ad9d'
INSTALLER_URL = 'https://github.com/KiriAky107/OpenNexus/releases/download/v0.6.0-beta6/OpenNexus_0.6.0-beta6_x64-setup.exe'


def main():
    value = os.environ.get('GITHUB_RUN_ID', '')
    require(re.fullmatch(r'[1-9][0-9]{0,15}', value), 'DESKTOP_PAIR_RUN_INVALID')
    require(os.environ.get('ANDROID_COMPILED_SOURCE') == ANDROID_SOURCE
            and os.environ.get('ANDROID_BUILD_RUN_ID') == '39006953890', 'DESKTOP_PAIR_ANDROID_CONTEXT_INVALID')
    context = {'run_id': int(value), 'tools_commit': os.environ.get('GITHUB_SHA', ''),
               'android_commit': ANDROID_SOURCE, 'desktop_commit': DESKTOP_SOURCE}
    root = claim_root(os.environ, context)  # Refuse local/user/self-hosted machines before any download.
    installer, partial = root / 'original-installer.exe', root / 'original-installer.exe.partial'
    with urllib.request.urlopen(INSTALLER_URL, timeout=90) as source:
        actual = urlsplit(source.geturl())
        require(actual.scheme == 'https' and actual.hostname is not None
                and actual.hostname.endswith('.githubusercontent.com'), 'DESKTOP_PAIR_DOWNLOAD_ORIGIN_INVALID')
        with partial.open('xb') as output:
            count = 0
            while chunk := source.read(1024 * 1024):
                count += len(chunk)
                require(count <= 105741178, 'DESKTOP_PAIR_DOWNLOAD_SIZE_INVALID')
                output.write(chunk)
            output.flush(); os.fsync(output.fileno())
    require(count == 105741178 and digest(partial) == INSTALLER_SHA256, 'DESKTOP_PAIR_INSTALLER_INVALID')
    partial.rename(installer)
    extract_installer(context, installer)
    with start_product(context, 1) as (page, vault, attempt):
        page.evaluate("()=>pairRouter.push('/settings')")
        page.locator('.settings-nav').get_by_role('button', name='Sync', exact=True).click()
        panel = page.locator('.sync-settings')
        panel.get_by_role('heading', name='OpenNexus Sync', exact=True).wait_for()
        status = page.evaluate("()=>pairInvoke('sync_status')")
        require(status.get('binding') is None and status.get('pending') == 0, 'DESKTOP_PAIR_INITIAL_STATE_INVALID')
        native = {'actual_Sync_settings_visible': True, 'actual_native_binding_absent': True,
                  'actual_native_pending': 0, 'native_vault_id': status['vault_id']}
        with (attempt / 'unbound-Sync.json').open('xb') as output:
            output.write(canonical(native))
    cleanup = json.loads((root / 'stage-1' / 'cleanup.json').read_text('utf-8'))
    require(cleanup['original_process_exited'] and cleanup['profile_preserved'], 'DESKTOP_PAIR_CLEANUP_UNKNOWN')
    proof = {'passed': True, 'scope': 'New isolated published Desktop bootstrap only', 'context': context,
             'desktop_installer_sha256': INSTALLER_SHA256, 'actual_Android_ran': False,
             'actual_Android_Desktop_pair_verified': False, 'old_smoke_scenarios_replayed': False,
             'native': native, 'cleanup': cleanup}
    with (root / 'desktop-startup-proof.json').open('xb') as output:
        output.write(canonical(proof)); output.flush(); os.fsync(output.fileno())
    print('OWNED_PACKAGED_DESKTOP_STARTUP_OK; actual Android pair remains incomplete')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('OWNED_PACKAGED_DESKTOP_STARTUP_FAILED_' + type(error).__name__)
        raise SystemExit(1)
