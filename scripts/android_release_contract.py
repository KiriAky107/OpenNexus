"""Typed Android release binding; debug/component receipts cannot become an APK release."""
from release_plan import COMMIT,HEX,require

PRODUCT='OpenNexus for Android'
PACKAGE='cc.kronecker.opennexus'


def asset_names(version):
    return {f'OpenNexus_Android_{version}_arm64-v8a.apk',f'OpenNexus_Android_{version}_source.zip','SHA256SUMS.txt'}


def validate(plan,result,installer):
    require(plan.get('product')==PRODUCT and plan.get('tag')=='android-v'+plan['version'],'ANDROID_RELEASE_PRODUCT_OR_TAG')
    require(plan.get('original') is None,'ANDROID_RELEASE_REPLACEMENT_FORBIDDEN')
    data=plan.get('android')
    require(type(data) is dict and set(data)=={'application_id','abi','version_code','certificate_sha256'},'ANDROID_RELEASE_PLAN_METADATA')
    require(data['application_id']==PACKAGE and data['abi']=='arm64-v8a' and type(data['version_code']) is int
        and data['version_code']>0 and type(data['certificate_sha256']) is str and HEX.fullmatch(data['certificate_sha256']),
        'ANDROID_RELEASE_PLAN_IDENTITY')
    require(result.get('kind')=='android-release' and result.get('passed') is True and result.get('source_commit')==plan['commit']
        and result.get('source_clean') is True and result.get('version')==plan['version'],'ANDROID_RELEASE_VERIFICATION_KIND_OR_SOURCE')
    require(result.get('android')==data and result.get('build_source_verified') is True and result.get('debug_signing') is False
        and result.get('signer_role')=='intended-android-release','ANDROID_RELEASE_INTENDED_SIGNER_REQUIRED')
    require(installer is not None and installer.get('kind')=='installer'
        and installer.get('name')==f'OpenNexus_Android_{plan["version"]}_arm64-v8a.apk'
        and type(installer.get('sha256')) is str and HEX.fullmatch(installer['sha256'])
        and result.get('apk_sha256')==installer['sha256'],'ANDROID_RELEASE_INSTALLER_BINDING')
    binaries=result.get('release_binaries')
    require(type(binaries) is dict and set(binaries)=={'arm64-v8a','x86_64'},'ANDROID_RELEASE_SAME_SOURCE_ABI_PAIR_REQUIRED')
    for abi,binary in binaries.items():
        require(type(binary) is dict and binary.get('abi')==abi and binary.get('source_commit')==plan['commit']
            and type(binary.get('apk_sha256')) is str and HEX.fullmatch(binary['apk_sha256'])
            and binary.get('application_id')==PACKAGE and binary.get('certificate_sha256')==data['certificate_sha256']
            and type(binary.get('version_code')) is int and binary['version_code']==data['version_code']
            and binary.get('manifest_nondebuggable') is True and binary.get('compiled_source_verified') is True
            and binary.get('signing_payload_verified') is True,'ANDROID_RELEASE_ABI_SOURCE_OR_SIGNER_BINDING')
    require(binaries['arm64-v8a']['apk_sha256']==installer['sha256']
        and binaries['arm64-v8a']['apk_sha256']!=binaries['x86_64']['apk_sha256'],'ANDROID_RELEASE_PHONE_ABI_BINDING')
    runtime=result.get('native_runtime',{})
    runtime_abi=runtime.get('abi') if type(runtime) is dict else None
    require(type(runtime) is dict and runtime.get('passed') is True and runtime.get('source_commit')==plan['commit']
        and runtime_abi in binaries and runtime.get('apk_sha256')==binaries[runtime_abi]['apk_sha256'] and runtime.get('application_id')==PACKAGE
        and runtime.get('certificate_sha256')==data['certificate_sha256'] and runtime.get('version_code')==data['version_code']
        and runtime.get('nondebuggable_installed_binary') is True and runtime.get('production_commands_verified') is True
        and runtime.get('cold_originals_preserved') is True,'ANDROID_RELEASE_ACTUAL_RUNTIME_REQUIRED')
    kind=runtime.get('device_kind');physical=runtime.get('physical_phone_verified')
    require((kind,physical) in (('owned-hosted-emulator',False),('owned-physical-device',True)) and type(physical) is bool,
        'ANDROID_RELEASE_DEVICE_SCOPE_REQUIRED')
    upgrade=result.get('upgrade',{})
    require(type(upgrade) is dict and upgrade.get('passed') is True and upgrade.get('application_id')==PACKAGE
        and upgrade.get('abi')==runtime_abi and upgrade.get('after_source_commit')==plan['commit']
        and upgrade.get('after_apk_sha256')==binaries[runtime_abi]['apk_sha256']
        and type(upgrade.get('before_source_commit')) is str and COMMIT.fullmatch(upgrade['before_source_commit'])
        and upgrade['before_source_commit']!=plan['commit'] and type(upgrade.get('before_apk_sha256')) is str and HEX.fullmatch(upgrade['before_apk_sha256'])
        and upgrade['before_apk_sha256']!=binaries[runtime_abi]['apk_sha256'] and upgrade.get('certificate_sha256')==data['certificate_sha256']
        and type(upgrade.get('before_version_code')) is int and 0<upgrade['before_version_code']<data['version_code']
        and upgrade.get('after_version_code')==data['version_code'] and upgrade.get('original_data_and_drafts_preserved') is True
        and upgrade.get('package_manager_update_without_clear_data') is True,'ANDROID_RELEASE_ACTUAL_UPGRADE_REQUIRED')
    return dict(actual_package_and_runtime_and_upgrade_bound=True,device_kind=kind,runtime_abi=runtime_abi,
        phone_asset_executed_in_this_runtime=runtime_abi==data['abi'],physical_phone_verified=physical and runtime_abi==data['abi'],
        repository_latest_must_remain_false=True)
