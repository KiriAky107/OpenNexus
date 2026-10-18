"""Bounded public protocol metadata for one new owned offline scene, no CDP command."""
import hashlib
import http.client
import json
import os
import re
import uuid
from urllib.parse import urlsplit
from android_auth_fixture import atomic_json
from android_runtime_probe import APP,require,require_hosted_emulator


def protocol_summary(value):
    domains=value.get('domains') if type(value) is dict else None
    result={'domains_array':type(domains) is list,'domain_count':0,'Debugger_domains':0,
        'enable_commands':0,'getScriptSource_commands':0,'scriptId_required':False,'scriptParsed_events':0}
    if type(domains) is not list:return result
    result['domain_count']=min(len(domains),257)
    if len(domains)>256:return result
    own=[x for x in domains if type(x) is dict and x.get('domain')=='Debugger'];result['Debugger_domains']=min(len(own),2)
    if len(own)!=1:return result
    commands,events=own[0].get('commands'),own[0].get('events')
    if type(commands) is list and len(commands)<=512:
        enable=[x for x in commands if type(x) is dict and x.get('name')=='enable']
        source=[x for x in commands if type(x) is dict and x.get('name')=='getScriptSource']
        result['enable_commands']=min(len(enable),2);result['getScriptSource_commands']=min(len(source),2)
        if len(source)==1:
            params=source[0].get('parameters')
            result['scriptId_required']=type(params) is list and len(params)<=64 and any(type(x) is dict and x.get('name')=='scriptId' and x.get('optional') is not True for x in params)
    if type(events) is list and len(events)<=512:result['scriptParsed_events']=min(sum(type(x) is dict and x.get('name')=='scriptParsed' for x in events),2)
    return result


def observe_owned_protocol(probe):
    require_hosted_emulator(probe.args.serial)
    require(probe.args.scope=='note-search','PROTOCOL_OBSERVATION_OFFLINE_SCOPE')
    nonce=str(uuid.uuid4());port=None
    report={'run_id':os.environ['GITHUB_RUN_ID'],'tools_source':probe.args.tools_commit,'compiled_source':probe.args.commit,
        'scope':'public HTTP metadata of this new owned note-search app, not the prior Native outcome fixture',
        'nonce':nonce,'Debugger_enabled':False,'CDP_command_or_native_IPC_sent':False,
        'raw_protocol_or_error_or_user_content_exported':False,'automatic_retry':False}
    atomic_json(probe.output/'owned-protocol-observation-intent.json',report,exclusive=True)
    try:
        pid=probe.shell('pidof',APP).stdout.strip();require(re.fullmatch(rb'[1-9][0-9]{0,9}',pid),'PROTOCOL_OBSERVATION_PID')
        report['original_pid']=int(pid)
        value=probe.device('forward','tcp:0','localabstract:webview_devtools_remote_'+pid.decode('ascii'))
        port=value.stdout.decode('ascii').strip();require(re.fullmatch(r'[0-9]{4,5}',port) and 1024<=int(port)<=65535,'PROTOCOL_OBSERVATION_FORWARD')
        connection=http.client.HTTPConnection('127.0.0.1',int(port),timeout=5)
        try:
            connection.request('GET','/json/list');response=connection.getresponse();pages=response.read(1024**2+1)
            require(response.status==200 and 0<len(pages)<=1024**2,'PROTOCOL_OBSERVATION_PAGE_LIST')
            listing=json.loads(pages.decode('utf-8','strict'));require(type(listing) is list and len(listing)<=16,'PROTOCOL_OBSERVATION_PAGE_LIST')
            own=[x for x in listing if type(x) is dict and x.get('type')=='page']
            require(len(own)==1 and own[0].get('title')=='OpenNexus','PROTOCOL_OBSERVATION_ORIGIN')
            url=urlsplit(own[0].get('url',''));require(url.scheme=='http' and url.hostname=='tauri.localhost' and url.port is None and not url.username and not url.password,'PROTOCOL_OBSERVATION_ORIGIN')
            report['owned_original_page_admitted']=True
            # Each endpoint gets its own original request. A server closing its
            # page-list socket must not masquerade as a protocol response.
            # There is still no retry of either request.
            connection.close()
            connection=http.client.HTTPConnection('127.0.0.1',int(port),timeout=5)
            report['public_HTTP_connections_independent']=True
            connection.request('GET','/json/protocol');response=connection.getresponse();body=response.read(4*1024**2+1)
            report.update(protocol_HTTP_status=response.status,protocol_bytes_bounded=len(body),protocol_exceeds_4MiB=len(body)>4*1024**2)
            if len(body)<=4*1024**2:
                report['protocol_body_sha256']=hashlib.sha256(body).hexdigest()
                if response.status==200:
                    try:report['protocol_summary']=protocol_summary(json.loads(body.decode('utf-8','strict')))
                    except (ValueError,UnicodeError):report['protocol_JSON_unconfirmed']=True
        finally:connection.close()
        report['same_original_pid_after_HTTP']=probe.shell('pidof',APP).stdout.strip()==pid
    except Exception as error:
        report['observation_error_class']=type(error).__name__
    finally:
        if port is not None and re.fullmatch(r'[0-9]{4,5}',port) and 1024<=int(port)<=65535:
            report['owned_forward_cleanup_attempted']=True
            try:
                cleanup=probe.device('forward','--remove','tcp:'+port,check=False)
                if type(getattr(cleanup,'returncode',None)) is int:
                    report['owned_forward_cleanup_exit']=cleanup.returncode
            except Exception as error:
                report['owned_forward_cleanup_error_class']=type(error).__name__
        atomic_json(probe.output/'owned-protocol-observation.json',report,exclusive=True)
    return report
