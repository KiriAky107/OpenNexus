"""Standalone copying mechanism for new, owned, stopped quota originals.

No live completed-driver/explicit-stop verifier or CLI is supplied here.
OriginalQuotaAdmission is a required abstract dependency, not a success flag.
Its future implementation must validate the original record set before copying;
controlled test implementations do not attest runtime admission.

The caller owns tools and must clean its private task directories in finally.
Only the returned snapshot can later be admitted for narrow evidence export.
"""
from abc import ABC, abstractmethod
import hashlib
import json
from pathlib import Path
import re
import shlex

from android_release_observation import APP, StoppedReleaseObserver, admit_root, exclusive
import android_release_sync_quota_snapshot as snapshot
import android_release_sync_quota_validation as quota


class OriginalQuotaAdmission(ABC):
    @abstractmethod
    def verify(self, *, scope, stage, identity):
        """Validate original completed-driver/once-owned-stop records or raise.

        Return exactly None after validation. There is no default verifier.
        This interface alone supplies no live original-record schema or proof.
        """


class OriginalQuotaAssociationAdmission(ABC):
    @abstractmethod
    def verify(self, *, association_scope, identity, collection_id):
        """Verify own associated driver/once-stop/two samples, return typed digest.

        No future quota cycle ID is available here. There is no concrete
        verifier or runtime admission supplied by this interface.
        """


class OriginalQuotaBootstrapAdmission(ABC):
    @abstractmethod
    def verify(self, *, capture_scope, identity, collection_id):
        """Verify original associated completion/once-stop/two samples, or raise.

        Binding/upload IDs are not available before capture. No concrete
        verifier, default success or live admission is supplied here.
        """


def need(value):
    if not value:
        raise RuntimeError('SIGNED_QUOTA_COLLECTION_UNAVAILABLE')


def directory(value):
    need(type(value) in (str, type(Path())))
    path = Path(value)
    need(path.is_absolute() and path.resolve() == path and path.is_dir())
    for item in (path, *path.parents):
        stat = item.lstat()
        need(not item.is_symlink() and not getattr(stat, 'st_file_attributes', 0) & 0x400)
    return path


def collect_stopped(device, serial, tools, artifact, *, scope, stage, identity, admission):
    """Copy only the approved quota files; never stop, start or root a product.

    Device commands are read-only and bounded. The future lifecycle must first
    supply its original-record verifier; no caller bool/dict substitutes it.
    A partial/existing private directory blocks another attempt at that stage.
    """
    scope = quota._scope(scope)
    need(type(stage) is str and stage in ('associated', 'quota-failed'))
    identity = owned_identity(identity, scope)
    need(isinstance(admission, OriginalQuotaAdmission))
    need(admission.verify(scope=dict(scope), stage=stage, identity=dict(identity)) is None)
    return _copy_stopped(device, serial, tools, artifact, scope=scope, stage=stage, identity=identity)


def owned_identity(identity, scope):
    need(quota._closed(identity, ('avd', 'qemu', 'api', 'build_type', 'debuggable', 'boot_id'))
         and type(identity['build_type']) is str and identity['build_type'] in ('eng', 'userdebug')
         and quota._same(identity, dict(avd=scope['avd'], qemu='1', api='36',
                         build_type=identity['build_type'], debuggable='1', boot_id=scope['boot_id'])))
    return dict(identity)


def collect_associated_before_cycle(device, serial, tools, artifact, *, association_scope,
                                   identity, collection_id, admission):
    """New operation-only capture; no placeholder parent/input/window or action."""
    scope = quota.association_scope(association_scope)
    need(quota._uuid(collection_id) and collection_id not in scope.values())
    identity = owned_identity(identity, scope)
    need(isinstance(admission, OriginalQuotaAssociationAdmission))
    digest = admission.verify(association_scope=dict(scope), identity=dict(identity), collection_id=collection_id)
    quota.association_capture(scope, collection_id, digest)
    return _copy_stopped(device, serial, tools, artifact, scope=scope, stage=quota.ASSOCIATED_BEFORE_CYCLE,
                         identity=identity, collection_id=collection_id, admission_digest=digest)


def collect_associated_bootstrap(device, serial, tools, artifact, *, capture_scope,
                                 identity, collection_id, admission):
    """Capture known original identities only, before deriving DB-owned IDs."""
    scope = quota.bootstrap_scope(capture_scope)
    need(quota._uuid(collection_id) and collection_id not in scope.values())
    identity = owned_identity(identity, scope)
    need(isinstance(admission, OriginalQuotaBootstrapAdmission))
    digest = admission.verify(capture_scope=dict(scope), identity=dict(identity), collection_id=collection_id)
    quota.bootstrap_capture(scope, collection_id, digest)
    return _copy_stopped(device, serial, tools, artifact, scope=scope, stage=quota.ASSOCIATED_BOOTSTRAP,
                         identity=identity, collection_id=collection_id, admission_digest=digest)


def _copy_stopped(device, serial, tools, artifact, *, scope, stage, identity,
                  collection_id=None, admission_digest=None):
    tools, artifact = directory(tools), directory(artifact)
    need(not tools.is_relative_to(artifact) and not artifact.is_relative_to(tools))

    # There is no path/command input from UI, records, the admission callback or
    # a CLI. The wrapper independently rejects lifecycle/unknown commands.
    def owned_path(name):
        if type(name) is not str:
            return False
        prefix = '/data/user/0/' + APP
        if name == prefix:
            return True
        return (name.startswith(prefix + '/') and
                re.fullmatch(r'[A-Za-z0-9_./-]{1,512}', name) is not None
                and all(part not in ('', '.', '..') for part in name[len(prefix) + 1:].split('/')))

    def readonly(*args, **kwargs):
        need(set(kwargs) <= {'check'} and ('check' not in kwargs or type(kwargs['check']) is bool))
        bound = 1024
        if args == ('emu', 'avd', 'name'):
            pass
        else:
            need(len(args) == 3 and args[:2] == ('shell', '-T') and type(args[2]) is str)
            command = tuple(shlex.split(args[2]))
            fixed = {('id', '-u'), ('getprop', 'ro.kernel.qemu'), ('getprop', 'ro.build.version.sdk'),
                     ('getprop', 'ro.build.type'), ('getprop', 'ro.debuggable'),
                     ('cat', '/proc/sys/kernel/random/boot_id'), ('ps', '-A', '-o', 'NAME'),
                     ('dumpsys', 'package', APP),
                     ('find', '/data/user/0/' + APP, '-name', 'mobile-drafts.sqlite3', '-type', 'f')}
            path_command = (len(command) == 2 and command[0] in ('realpath', 'sha256sum') and owned_path(command[1])
                or len(command) == 3 and command[:2] in (('test', '-e'), ('test', '-d'), ('test', '-f'))
                   and owned_path(command[2])
                or len(command) == 4 and command[:3] == ('test', '!', '-L') and owned_path(command[3])
                or len(command) == 4 and command[:3] == ('stat', '-c', '%s:%h:%i') and owned_path(command[3])
                or len(command) == 4 and command[:2] == ('head', '-c')
                   and command[2] in ('57', str(snapshot.MAX_FILE + 1)) and owned_path(command[3]))
            need(command in fixed or path_command)
            if command[:1] in (('ps',), ('dumpsys',)):
                bound = 1024 * 1024
            elif command[:1] == ('find',):
                bound = 8192
            elif command[:2] == ('head', '-c'):
                bound = int(command[2])
        result = device(*args, **kwargs)
        need(type(result.returncode) is int and type(result.stdout) is bytes and type(result.stderr) is bytes
             and len(result.stdout) <= bound and result.stderr == b'')
        # test -e may report an absent nullable companion; no other failed
        # command is treated as content, a successful empty read or absence.
        absent_test = args[:2] == ('shell', '-T') and tuple(shlex.split(args[2]))[:2] == ('test', '-e')
        need(result.returncode == 0 or absent_test and result.returncode == 1)
        return result

    private = tools / ('quota-original-' + stage)
    observer = StoppedReleaseObserver(readonly, serial, private, [quota.NOTE])
    observer.allowed.add(snapshot.SPOOL_SOURCE)  # this one private instance only
    observer.identity = identity
    need(observer.text('id', '-u') == '0')
    root = observer.discover_data_root()  # its prior check is not a bracketing sample
    admit_root(root)

    def path(relative):
        need(relative in observer.allowed)
        name = root + relative
        # Reject symlinked ancestors, while retaining Android's known root alias.
        components = name.split('/')
        for end in range(5, len(components)):
            parent = '/'.join(components[:end])
            need(observer.shell('test', '-d', parent).returncode == 0
                 and observer.shell('test', '!', '-L', parent).returncode == 0)
            need(observer.text('realpath', parent) in (parent, parent.replace('/data/user/0/', '/data/data/', 1)))
        need(observer.shell('test', '!', '-L', name).returncode == 0)
        return name

    def metadata(name, limit):
        need(observer.text('realpath', name) in (name, name.replace('/data/user/0/', '/data/data/', 1)))
        need(observer.shell('test', '-f', name).returncode == 0)
        value = observer.text('stat', '-c', '%s:%h:%i', name)
        need(re.fullmatch(r'[0-9]{1,20}:1:[0-9]{1,20}', value) is not None)
        size, links, inode = map(int, value.split(':'))
        need(0 <= size <= limit and links == 1 and 0 < inode < 2**64)
        digest = observer.text('sha256sum', name).split()
        need(len(digest) == 2 and digest[1] == name and re.fullmatch('[0-9a-f]{64}', digest[0]) is not None)
        return size, inode, digest[0]

    observer.require_stopped()
    need(observer.text('id', '-u') == '0')
    output = private / 'snapshot'
    output.mkdir()
    files, before = {}, {}
    for relative in sorted(observer.allowed):
        name = path(relative)
        present = observer.shell('test', '-e', name, check=False).returncode == 0
        target_name = snapshot.SPOOL if relative == snapshot.SPOOL_SOURCE else relative
        if not present:
            need(relative.endswith(('-wal', '-shm')))
            before[relative] = None
            files[target_name] = None
            continue
        limit = 56 if relative in (snapshot.NOTE, snapshot.SPOOL_SOURCE) else snapshot.MAX_FILE
        info = metadata(name, limit)
        if limit == 56:
            need(info[0] == 56 and info[2] == quota.HASH)
        raw = observer.shell('head', '-c', str(limit + 1), name).stdout
        need(len(raw) == info[0] and hashlib.sha256(raw).hexdigest() == info[2])
        if limit == 56:
            need(raw == quota.BODY)
        target = output / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        need(exclusive(target, raw) is None)
        before[relative] = info
        files[target_name] = dict(bytes=len(raw), sha256=info[2])
    observer.require_stopped()
    need(observer.text('id', '-u') == '0')
    for relative, info in before.items():
        name = path(relative)
        present = observer.shell('test', '-e', name, check=False).returncode == 0
        if info is None:
            need(not present)
        else:
            need(present and metadata(name, snapshot.MAX_FILE) == info)
    manifest = dict(schema=1, package=APP, identity=identity, quota_scope=scope,
        quota_stage=stage, spool_source=snapshot.SPOOL_SOURCE, files=files,
        app_stopped_before_and_after=True, credential_store_copied=False,
        SQL_executed_on_device=False, normal_phone_permissions_verified=False)
    if stage == quota.ASSOCIATED_BEFORE_CYCLE:
        del manifest['quota_scope']
        manifest.update(schema=2, quota_association_scope=scope, collection_id=collection_id,
                        admission_metadata_canonical_sha256=admission_digest)
    elif stage == quota.ASSOCIATED_BOOTSTRAP:
        del manifest['quota_scope']
        manifest.update(schema=3, quota_association_capture_scope=scope, collection_id=collection_id,
                        admission_metadata_canonical_sha256=admission_digest,
                        binding_and_upload_ids_present_at_capture=False)
    need(set(files) == snapshot.FILES)
    raw_manifest = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    need(len(raw_manifest) <= snapshot.MAX_MANIFEST)
    need(exclusive(output / 'snapshot.json', raw_manifest) is None)
    return output
