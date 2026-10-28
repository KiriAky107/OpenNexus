"""Timeout release only: batch fixed read-only boot properties, preserve owner checks."""
from copy import copy
import os
import time

from android_release_observation import admit_identity
from android_runtime_probe import require,require_hosted_emulator
from android_sync_process_host import _owner as original_owner

ENTRY='ReleaseSyncTimeoutRuntimeProbe'
PROPERTIES='getprop ro.kernel.qemu && getprop ro.build.version.sdk && getprop ro.build.type && getprop ro.debuggable && cat /proc/sys/kernel/random/boot_id'


def read_owned_identity(subject):
    require(subject.driver_entry==ENTRY,'SYNC_TIMEOUT_RELEASE_IDENTITY_SCOPE')
    require_hosted_emulator(subject.args.serial)
    start=time.monotonic()
    avd=subject.device('emu','avd','name',check=False,timeout=1)
    properties=subject.shell('sh','-c',PROPERTIES,check=False,timeout=1)
    require(0<=start<=time.monotonic()<=start+1,'SYNC_TIMEOUT_RELEASE_IDENTITY_BOUND')
    for value in (avd,properties):
        require(type(value.returncode) is int and value.returncode==0 and type(value.stdout) is bytes
            and 0<len(value.stdout)<=512 and type(value.stderr) is bytes and value.stderr==b'',
            'SYNC_TIMEOUT_RELEASE_IDENTITY_QUERY')
    names=avd.stdout.decode('ascii','strict').splitlines();fields=properties.stdout.decode('ascii','strict').splitlines()
    require(len(names)==2 and names[1]=='OK' and len(fields)==5,'SYNC_TIMEOUT_RELEASE_IDENTITY_SHAPE')
    identity=dict(avd=names[0],qemu=fields[0],api=fields[1],build_type=fields[2],debuggable=fields[3],boot_id=fields[4])
    return admit_identity(identity,os.environ['GITHUB_RUN_ID'],os.environ['GITHUB_RUN_ATTEMPT'])


def release_owner(subject,process,pid,uid,lifetime):
    # Keep every original root/PID/UID/foreground/start_ticks/driver check.
    # Only this temporary observer's fixed identity reader uses two commands.
    view=copy(subject);view.observer=copy(subject.observer)
    view.observer.observe_identity=lambda:read_owned_identity(subject)
    return original_owner(view,process,pid,uid,lifetime)
