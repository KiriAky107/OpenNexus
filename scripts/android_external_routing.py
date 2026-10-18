"""Only the test package's reserved host selection on an owned hosted emulator."""
from android_auth_fixture import atomic_json
from android_runtime_probe import DRIVER,require,require_hosted_emulator
from android_external_contract import HOST,MD


def selected(text):
    require(type(text) is str and len(text)<=32768,'EXTERNAL_ROUTING_OUTPUT_BOUND')
    require(text.lstrip().startswith(DRIVER+':') and text.count('User 0:')==1,'EXTERNAL_ROUTING_OWN_PACKAGE')
    section=text.split('User 0:',1)[1]
    require('Verification link handling allowed: true' in section,'EXTERNAL_LINK_HANDLING_NOT_ENABLED')
    require('User ' not in section,'EXTERNAL_UNREVIEWED_USER')
    lines=section.splitlines();enabled=[];inside=False
    for line in lines:
        value=line.strip()
        if value=='Enabled:':
            require(not inside,'EXTERNAL_DUPLICATE_ENABLED_SECTION');inside=True;continue
        if value=='Disabled:':inside=False;continue
        if inside and value:enabled.append(value)
    require(all(domain==HOST for domain in enabled) and len(enabled)<=1,'EXTERNAL_UNREVIEWED_OWN_DOMAIN_SELECTION')
    return enabled==[HOST]


def setup(probe):
    require_hosted_emulator(probe.args.serial);require(probe.args.scope=='external-links','EXTERNAL_ROUTING_SCOPE')
    probe.wait_owned_connection()
    help_text=probe.shell('cmd','package','help').stdout.decode('utf-8','strict')
    require(len(help_text)<=131072 and 'get-app-links' in help_text and 'set-app-links-user-selection' in help_text,
        'EXTERNAL_SUPPORTED_ROUTING_COMMANDS_REQUIRED')
    (probe.output/'external-package-manager-help.txt').write_text(help_text,'utf-8')
    before=probe.shell('cmd','package','get-app-links','--user','0',DRIVER).stdout.decode('utf-8','strict')
    require(not selected(before),'EXTERNAL_FRESH_TOOL_DOMAIN_ALREADY_SELECTED')
    intent=dict(schema=1,scope='external-links',package=DRIVER,user=0,domain=HOST,before=before,
        command=['cmd','package','set-app-links-user-selection','--user','0','--package',DRIVER,'true',HOST],
        fixture_only_not_internet_domain_verification=True,global_default_browser_change=False,automatic_retry=False)
    atomic_json(probe.output/'external-routing-intent.json',intent,exclusive=True)
    probe.shell(*intent['command'])
    after=probe.shell('cmd','package','get-app-links','--user','0',DRIVER).stdout.decode('utf-8','strict')
    require(selected(after),'EXTERNAL_OWN_DOMAIN_SELECTION_NOT_OBSERVED')
    resolution=probe.shell('cmd','package','resolve-activity','--brief','-a','android.intent.action.VIEW',
        '-c','android.intent.category.BROWSABLE','-d',MD).stdout.decode('utf-8','strict').strip().splitlines()
    require(bool(resolution),'EXTERNAL_OS_RESOLVER_MISSING');component=resolution[-1]
    require(component in (DRIVER+'/.ExternalIntentReceiver',DRIVER+'/'+DRIVER+'.ExternalIntentReceiver'),
        'EXTERNAL_OS_RESOLVER_NOT_OWN_RECEIVER')
    atomic_json(probe.output/'external-routing-after.json',dict(after=after,component=component,fixture_only=True),exclusive=True)
    return intent


def cleanup(probe):
    if not (probe.output/'external-routing-intent.json').exists():return dict(owned_mutation_attempted=False)
    require_hosted_emulator(probe.args.serial);require(probe.args.scope=='external-links','EXTERNAL_ROUTING_SCOPE');probe.wait_owned_connection()
    # Read first even after an unknown original setter. Never send Open again.
    current=probe.shell('cmd','package','get-app-links','--user','0',DRIVER).stdout.decode('utf-8','strict')
    changed=selected(current)
    if changed:probe.shell('cmd','package','set-app-links-user-selection','--user','0','--package',DRIVER,'false',HOST)
    after=probe.shell('cmd','package','get-app-links','--user','0',DRIVER).stdout.decode('utf-8','strict')
    require(not selected(after),'EXTERNAL_OWN_DOMAIN_SELECTION_NOT_CLEARED')
    result=dict(owned_mutation_attempted=True,own_selection_cleared=True,setter_sent=changed,after=after,
        no_global_browser_phone_or_TLS_setting_changed=True)
    atomic_json(probe.output/'external-routing-cleanup.json',result,exclusive=True);return result
