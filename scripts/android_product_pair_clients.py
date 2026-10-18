"""Immutable hosted client choices; a tools commit never relabels an APK."""
from dataclasses import dataclass
import sys


class FixedClientError(ValueError):
    pass


def require(value, code):
    if not value:
        raise FixedClientError(code)


@dataclass(frozen=True)
class AndroidClient:
    source: str
    build_run: str
    apk_sha256: str
    original_outcome: bool = False


CLIENTS = (
    AndroidClient('18b6abbbf8bc8dae249469f7bcd9e5139cb39fee', '39054475515',
                  '5cc30c6a8c89f404fed9d8b0ac33306a734a6a8035379268d9718f6cb7a28755'),
    AndroidClient('0756d1e079ac045255322759d944b7c01408b0bd', '39064416419',
                  'eacb0a241b7b03d474991f8b7dfa649408d5b7f123135d822df16c5fa05e8c00', True),
    AndroidClient('b29b710a09d9a8a374c59b9410864a6a91f5f6ca', '39078195102',
                  '9f96fb6bb88ba3bc7f983008864f3e2d9fcf025d3d95b8280ebcf8afecf14d11', True),
)


def fixed_android_client(source, build_run=None):
    require(type(source) is str and (build_run is None or type(build_run) is str),
            'PAIR_FIXED_ANDROID_CLIENT_REFUSED')
    values = [client for client in CLIENTS if client.source == source
              and (build_run is None or client.build_run == build_run)]
    require(len(values) == 1, 'PAIR_FIXED_ANDROID_CLIENT_REFUSED')
    return values[0]


def original_outcome_client(source, build_run):
    client = fixed_android_client(source, build_run)
    require(client.original_outcome, 'PAIR_ORIGINAL_OUTCOME_CLIENT_REFUSED')
    return client


if __name__ == '__main__':
    require(len(sys.argv) in (3,4), 'PAIR_FIXED_ANDROID_CLIENT_REFUSED')
    if len(sys.argv)==4:
        require(sys.argv[3]=='--original-native-outcome', 'PAIR_FIXED_ANDROID_CLIENT_REFUSED')
        original_outcome_client(*sys.argv[1:3])
    else:fixed_android_client(*sys.argv[1:])
