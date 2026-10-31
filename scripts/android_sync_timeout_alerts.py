"""Finite fixed-product UI alert diagnostics only; never a Native cause or admission."""
CATALOG_SHA256 = 'de56387b9166bc8a8fdd0e952397f1c6aee9acc2b225d366e7204aaafa231ec4'
CODES = (
    'SYNC_CONNECTION_FAILED',
    'SYNC_RESPONSE_INVALID',
    'SYNC_NETWORK_ERROR',
    'SYNC_TLS_UNAVAILABLE',
    'SYNC_REDIRECT_BLOCKED',
    'SYNC_FOREGROUND_CHANGED',
    'SYNC_FOREGROUND_REQUIRED',
    'SYNC_CANCELLED',
    'SYNC_PAUSED',
    'SYNC_BINDING_CHANGED',
    'SYNC_NOT_BOUND',
    'SYNC_TARGET_INVALID',
    'SYNC_PAGE_INVALID',
    'SYNC_AUTH_SELECTION_CHANGED',
    'SYNC_AUTH_SELECTION_INVALID',
    'SYNC_AUTH_IDENTITY_CHANGED',
    'SYNC_AUTH_RECONCILIATION_REQUIRED',
    'SYNC_LOGIN_REQUIRED',
    'SYNC_SESSION_EXPIRED',
    'SESSION_EXPIRED',
    'SYNC_CREDENTIALS_UNAVAILABLE',
    'SYNC_CREDENTIALS_INVALID',
    'SYNC_ACTIVITY_BUSY',
    'SYNC_ACTIVITY_LIMIT',
    'SYNC_ACTIVITY_UNAVAILABLE',
    'SYNC_ACTIVITY_RECONCILIATION_REQUIRED',
    'SYNC_ACTIVITY_REQUEST_INVALID',
    'SYNC_ACTIVITY_ID_INVALID',
    'SYNC_ACTIVITY_OWNER_CHANGED',
    'SYNC_WORKER_INTERRUPTED',
    'SYNC_RESOLUTION_INVALID',
    'SYNC_RESOLUTION_CHANGED',
    'SYNC_CONFLICT_CHANGED',
    'SYNC_REVIEW_CHANGED',
    'SYNC_REVIEW_UNSAFE_FILE',
    'SYNC_RESPONSE_TOO_LARGE',
    'SYNC_TIME_INVALID',
    'SYNC_ENDPOINT_INVALID',
    'SYNC_LOGIN_INVALID',
    'MOBILE_HOST_REQUIRED',
    'SYNC_PLATFORM_UNSUPPORTED',
    'MOBILE_STORAGE_UNAVAILABLE',
    'HOST_BUSY',
    'VAULT_CHANGED',
    'QUOTA_EXCEEDED',
    'FILESYSTEM_ERROR',
    'DATABASE_ERROR',
    'SYNC_REMOTE_ERROR',
    'REVISION_CONFLICT',
    'RATE_LIMITED',
)
NETWORK_INDEX = 2
FRAGMENT_CATALOG_SHA256 = '48e3aa6bf5b911398eb73fe661284644d0e65e82e99f297225feef1a9dee1022'


def validate(value, scene, elapsed):
    if not (type(elapsed) is int and elapsed >= 0 and type(scene) is list and len(scene) == 8 and
            all(type(n) is int and 0 <= n <= 2 for n in scene)):
        return False
    keys = {'schema', 'catalog_sha256', 'scan_elapsed_ms', 'text_counts', 'description_counts',
            'strict_counts', 'known_channel_conflicts', 'raw_UI_recorded', 'native_timeout_verified',
            'fragment_catalog_sha256', 'fragment_body_text_counts', 'fragment_body_description_counts',
            'fragment_header_counts', 'fragment_detail_counts', 'unassociated_source_fragments_only', 'complete_alert_inferred'}
    if not (type(value) is dict and set(value) == keys and type(value['schema']) is int and value['schema'] == 2 and
            value['catalog_sha256'] == CATALOG_SHA256 and type(value['scan_elapsed_ms']) is int and
            value['scan_elapsed_ms'] == elapsed and value['raw_UI_recorded'] is False and
            value['native_timeout_verified'] is False and value['fragment_catalog_sha256'] == FRAGMENT_CATALOG_SHA256 and
            value['unassociated_source_fragments_only'] is True and value['complete_alert_inferred'] is False):
        return False
    for key in ('text_counts', 'description_counts', 'strict_counts', 'fragment_body_text_counts', 'fragment_body_description_counts'):
        counts = value[key]
        if not (type(counts) is list and len(counts) == len(CODES) and
                all(type(n) is int and 0 <= n <= 2 for n in counts)):
            return False
    for key in ('fragment_header_counts', 'fragment_detail_counts'):
        if not (type(value[key]) is list and len(value[key]) == 2 and all(type(n) is int and 0 <= n <= 2 for n in value[key])):
            return False
    if not (type(value['known_channel_conflicts']) is int and 0 <= value['known_channel_conflicts'] <= 2 and
            type(scene) is list and len(scene) == 8 and value['strict_counts'][NETWORK_INDEX] == scene[3]):
        return False
    # Strict full-label pairs are a subset of exact per-channel matches.
    return all(s <= min(2, a + b) for s, a, b in zip(value['strict_counts'],
               value['text_counts'], value['description_counts']))
