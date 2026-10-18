"""Own reserved-host routing; typed Android state, never shell display assumptions."""
from android_auth_fixture import atomic_json
from android_runtime_probe import DRIVER,require,require_hosted_emulator
from android_external_contract import HOST,MD,decode

STAGES=('before','after','cleanup-before','cleanup-after')
DOMAIN_NONE=0
DOMAIN_SELECTED=1
DOMAIN_VERIFIED=2


def selected(value,stage):
    require(stage in STAGES and type(value) is dict and set(value)=={'schema','scope','stage','package','user_id',
        'link_handling_allowed','host_states','internet_verification_claimed'},'EXTERNAL_DOMAIN_OBSERVATION_SCHEMA')
    require(type(value['schema']) is int and value['schema']==1 and value['scope']=='external-links' and value['stage']==stage,
        'EXTERNAL_DOMAIN_OBSERVATION_SCOPE')
    require(value['package']==DRIVER and type(value['user_id']) is int and value['user_id']==0,'EXTERNAL_DOMAIN_OWN_PACKAGE_OR_USER')
    require(value['link_handling_allowed'] is True and value['internet_verification_claimed'] is False,'EXTERNAL_DOMAIN_HANDLING_OR_CLAIM')
    states=value['host_states'];require(type(states) is dict and set(states)=={HOST} and type(states[HOST]) is int
        and states[HOST] in (DOMAIN_NONE,DOMAIN_SELECTED),'EXTERNAL_DOMAIN_OWN_SINGLE_UNVERIFIED_HOST')
    return states[HOST]==DOMAIN_SELECTED


def observe(probe,stage):
    require_hosted_emulator(probe.args.serial);require(probe.args.scope=='external-links' and stage in STAGES,'EXTERNAL_DOMAIN_OBSERVATION_SCOPE')
    probe.wait_owned_connection();phase='external-domain-'+stage
    result=probe.shell('am','instrument','-w','-e','phase',phase,DRIVER+'/.RuntimeProbe',timeout=60)
    (probe.output/(phase+'.log')).write_bytes(result.stdout)
    require(('OPENNEXUS_RUNTIME_PHASE_OK '+phase).encode() in result.stdout and b'OPENNEXUS_RUNTIME_FAILED' not in result.stdout,
        'EXTERNAL_DOMAIN_STRUCTURED_API_FAILED')
    raw=probe.shell('run-as',DRIVER,'cat','files/owned-'+phase+'.json').stdout
    require(len(raw)<=8192,'EXTERNAL_DOMAIN_RAW_BOUND')
    with (probe.output/(phase+'-raw.json')).open('xb') as output:
        import os
        output.write(raw);output.flush();os.fsync(output.fileno())
    value=decode(raw);selected(value,stage);atomic_json(probe.output/(phase+'.json'),value,exclusive=True);return value


def setup(probe):
    require_hosted_emulator(probe.args.serial);require(probe.args.scope=='external-links','EXTERNAL_ROUTING_SCOPE');probe.wait_owned_connection()
    help_text=probe.shell('cmd','package','help').stdout.decode('utf-8','strict')
    require(len(help_text)<=131072 and 'get-app-links' in help_text and 'set-app-links-user-selection' in help_text,'EXTERNAL_SUPPORTED_ROUTING_COMMANDS_REQUIRED')
    (probe.output/'external-package-manager-help.txt').write_text(help_text,'utf-8')
    raw=probe.shell('cmd','package','get-app-links','--user','0',DRIVER).stdout
    require(len(raw)<=32768,'EXTERNAL_ROUTING_RAW_OUTPUT_BOUND');(probe.output/'external-domain-before-display.txt').write_bytes(raw)
    before=observe(probe,'before');require(not selected(before,'before'),'EXTERNAL_FRESH_TOOL_DOMAIN_ALREADY_SELECTED')
    intent=dict(schema=1,scope='external-links',package=DRIVER,user=0,domain=HOST,before=before,
        command=['cmd','package','set-app-links-user-selection','--user','0','--package',DRIVER,'true',HOST],
        fixture_only_not_internet_domain_verification=True,global_default_browser_change=False,automatic_retry=False)
    atomic_json(probe.output/'external-routing-intent.json',intent,exclusive=True);probe.shell(*intent['command'])
    after=observe(probe,'after');require(selected(after,'after'),'EXTERNAL_OWN_DOMAIN_SELECTION_NOT_OBSERVED')
    resolution=probe.shell('cmd','package','resolve-activity','--brief','-a','android.intent.action.VIEW','-c','android.intent.category.BROWSABLE','-d',MD).stdout.decode('utf-8','strict').strip().splitlines()
    require(bool(resolution),'EXTERNAL_OS_RESOLVER_MISSING');component=resolution[-1]
    require(component in (DRIVER+'/.ExternalIntentReceiver',DRIVER+'/'+DRIVER+'.ExternalIntentReceiver'),'EXTERNAL_OS_RESOLVER_NOT_OWN_RECEIVER')
    atomic_json(probe.output/'external-routing-after.json',dict(after=after,component=component,fixture_only=True),exclusive=True);return intent


def cleanup(probe):
    if not (probe.output/'external-routing-intent.json').exists():return dict(owned_mutation_attempted=False)
    require_hosted_emulator(probe.args.serial);require(probe.args.scope=='external-links','EXTERNAL_ROUTING_SCOPE')
    current=observe(probe,'cleanup-before');changed=selected(current,'cleanup-before')
    if changed:probe.shell('cmd','package','set-app-links-user-selection','--user','0','--package',DRIVER,'false',HOST)
    after=observe(probe,'cleanup-after');require(not selected(after,'cleanup-after'),'EXTERNAL_OWN_DOMAIN_SELECTION_NOT_CLEARED')
    result=dict(owned_mutation_attempted=True,own_selection_cleared=True,setter_sent=changed,after=after,no_global_browser_phone_or_TLS_setting_changed=True)
    atomic_json(probe.output/'external-routing-cleanup.json',result,exclusive=True);return result
