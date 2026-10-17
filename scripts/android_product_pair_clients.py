"""Immutable hosted client choices; a tools commit never relabels an APK."""
from dataclasses import dataclass
import sys

from android_desktop_handoff import require


@dataclass(frozen=True)
class AndroidClient:
    source: str
    build_run: str
    apk_sha256: str


CLIENTS = (
    AndroidClient('18b6abbbf8bc8dae249469f7bcd9e5139cb39fee', '39054475515',
                  '5cc30c6a8c89f404fed9d8b0ac33306a734a6a8035379268d9718f6cb7a28755'),
    AndroidClient('0756d1e079ac045255322759d944b7c01408b0bd', '39064416419',
                  'eacb0a241b7b03d474991f8b7dfa649408d5b7f123135d822df16c5fa05e8c00'),
)


def fixed_android_client(source, build_run=None):
    require(type(source) is str and (build_run is None or type(build_run) is str),
            'PAIR_FIXED_ANDROID_CLIENT_REFUSED')
    values = [client for client in CLIENTS if client.source == source
              and (build_run is None or client.build_run == build_run)]
    require(len(values) == 1, 'PAIR_FIXED_ANDROID_CLIENT_REFUSED')
    return values[0]


if __name__ == '__main__':
    require(len(sys.argv) == 3, 'PAIR_FIXED_ANDROID_CLIENT_REFUSED')
    fixed_android_client(*sys.argv[1:])
