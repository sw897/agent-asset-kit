#!/usr/bin/env python3
"""MCP CLI E2E in synthetic Git repositories, homes and mcplink state only."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

from fixture_cleanup import cleanup_workdir

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / 'src/agent_asset_kit/resources/library'
FAKE = r'''
import json,os,sys,time
from pathlib import Path
p=Path(os.environ['AA_MCP_FIXTURE']);s=json.loads(p.read_text(encoding='utf-8'));args=sys.argv[1:]
with p.with_suffix('.calls').open('a') as f:f.write(json.dumps(args)+'\n')
def emit(value,ok=True):
 print(json.dumps({'ok' if ok else 'result':value}));sys.exit(0 if ok else 1)
if args==['schema']:emit({'schemaVersion':1,'command':{'commands':[{'name':n} for n in ['status','connections','changes']]}})
if args==['status']:emit({k:s[k] for k in ['agents','sources','connections','bindings','gateway']})
if args[:2]==['connections','tools']:
 if s.get('auth_fail'):
  s['connections'][0]['status']=s.get('probe_status','needs_auth');p.write_text(json.dumps(s))
  emit({'connection':s['connections'][0],'tools':[]},False)
 emit({'tools':[{'name':n} for n in s['tools']]})
if args[:2]==['changes','preview'] or args==['rpc','--stdin']:
 selector=None
 if args==['rpc','--stdin']:
  payload=json.loads(sys.stdin.read());assert payload['method']=='changes.preview'
  params=payload['params'];targets=params['targetSourceIds'];connection=params['connectionIds'][0];selector=None if s.get('ignore_binding_target') else params['bindingTarget']['name']
 else:
  targets=[args[i+1] for i,a in enumerate(args) if a=='--target'];connection=args[args.index('--connection')+1]
 conn=next(r for r in s['connections'] if r['id']==connection)
 changes=[]
 for v in targets:
  found=[b for b in s['bindings'] if b['sourceId']==v and b['connectionId']==connection and (selector is None or b['name']==selector)]
  name=found[0]['name'] if found else conn['name']
  changes.append({'sourceId':v,'agent':v.split(':')[0],'path':'synthetic-user-config','name':name,'mode':'shared','action':'upsert'})
 s['plan']={'planId':'provider-plan','changes':changes,'expiresAt':time.time()+(-1 if s.get('expired') else 600),'connection':conn['id']};p.write_text(json.dumps(s));emit(s['plan'])
if args[:2]==['changes','apply']:
 results=[]
 for i,c in enumerate(s['plan']['changes']):
  good=not s.get('partial') or i==0
  if good:
   s['bindings']=[b for b in s['bindings'] if not (b['sourceId']==c['sourceId'] and b['name']==c['name'])]
   s['bindings'].append({'sourceId':c['sourceId'],'name':c['name'],'connectionId':s['plan']['connection'],'enabled':True,'changed':False,'needsUpdate':False})
  results.append({'sourceId':c['sourceId'],'status':'applied' if good else 'failed','operationId':'synthetic-operation','message':'SYNTHETIC_PRIVATE_ENDPOINT'})
 p.write_text(json.dumps(s))
 if s.get('uncertain'):print('invalid SYNTHETIC_PRIVATE_ENDPOINT');sys.exit(1)
 emit({'results':results},not s.get('partial'))
raise RuntimeError('unexpected operation; fixture never allows import, login or business calls')
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scanner', required=True)
    args = parser.parse_args()
    out = ROOT / 'artifacts/e2e/skill-mcp' / time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='aa-mcp-'))
    repo, home = work / 'repo', work / 'home'
    report = {'status': 'FAIL', 'platform': platform.platform(), 'python': sys.version,
              'scope': 'synthetic mcplink protocol; real assets CLI and Git, no accounts/native clients',
              'assertions': [], 'commands': [], 'workdir': str(work)}
    env = dict(os.environ)

    def run(argv, expected=0, cwd=repo):
        p = subprocess.run([str(a) for a in argv], cwd=cwd, env=env, capture_output=True,
                           text=True, encoding='utf-8', timeout=90)
        report['commands'].append({'argv': [str(a) for a in argv], 'exit': p.returncode,
                                   'stdout': p.stdout, 'stderr': p.stderr})
        assert p.returncode == expected, (argv, p.returncode, p.stdout[:1500], p.stderr[:500])
        return p.stdout

    def cli(*argv, expected=0):
        return json.loads(run(['aak', '--root', str(repo), *argv], expected))

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='')

    def read(path):
        return json.loads(path.read_text(encoding='utf-8'))

    def check(name, value):
        assert value, name
        report['assertions'].append(name)

    def calls():
        return [json.loads(v) for v in state.with_suffix('.calls').read_text().splitlines()] if state.with_suffix('.calls').exists() else []

    def connect(*extra, expected=0):
        return cli('mcp', 'connect', '--skill', 'demo', '--service', 'notes',
                   '--instance', 'codex', '--instance', 'claude', '--instance', 'hermes', *extra, expected=expected)

    try:
        repo.mkdir()
        (repo / '.agent-asset-kit.json').write_text('{"tool":"AgentAssetKit","format_version":1}\n', encoding='utf-8')
        shutil.copy2(ROOT / '.gitignore', repo / '.gitignore')
        shutil.copy2(SEED / '.gitattributes', repo / '.gitattributes')
        run(['git', 'init', '-b', 'main'])
        run(['git', 'config', 'user.name', 'Synthetic MCP E2E'])
        run(['git', 'config', 'user.email', 'fixture@example.invalid'])
        cli('configure', '--home', home, '--agents', 'codex,claude,hermes', '--gitleaks', Path(args.scanner).resolve())
        skill = repo / 'skills/custom/demo'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: demo\ndescription: Synthetic MCP fixture.\n---\n\nRead a synthetic item.\n', encoding='utf-8', newline='')
        ident = cli('register', '--path', 'skills/custom/demo', '--targets', 'codex,claude,hermes', '--agent', 'codex', '--reviewed')['skill_id']
        manifest_path = repo / 'skills/manifest.json'
        manifest = read(manifest_path)
        native = dict(service='notes', mode='native-read', via='native', tools=['read_item'], optional=False, reference='SKILL.md')
        optional = dict(service='cli-notes', mode='cli-read', via='cli', tools=['read_item'], optional=True, reference='SKILL.md')
        manifest['packages'][ident]['requirements'] = {'mcp': [native, optional]}
        write(manifest_path, manifest)
        run(['git', 'add', '.'])
        run(['git', 'commit', '-m', 'Synthetic declared skill'])
        state = work / 'state.json'
        base = {'agents': [{'id': k, 'installed': True, 'configurable': True, 'profilePath': str(home / ('.'+k))} for k in ['codex', 'claude', 'hermes']],
                'sources': [{'id': k + ':user', 'agent': k, 'scope': 'user' if k=='claude' else None, 'project': None, 'readOnly': False, 'exists': True,
                             'path':str(home / '.claude.json' if k=='claude' else home / ('.'+k) / ('config.toml' if k=='codex' else 'config.yaml'))} for k in ['codex', 'claude', 'hermes']],
                'connections': [{'id': 'conn-a', 'name': 'notes', 'status': 'ready', 'active': True}],
                'bindings': [], 'tools': ['read_item'], 'gateway': {'paused': False}}
        write(state, base)
        fake = work / 'fake.py'
        fake.write_text(FAKE, encoding='utf-8', newline='')
        wrapper = work / ('mcplink.cmd' if os.name == 'nt' else 'mcplink')
        wrapper.write_text(f'@"{sys.executable}" "{fake}" %*\n' if os.name == 'nt' else f'#!{sys.executable}\nexec(compile(open({str(fake)!r}).read(),{str(fake)!r},"exec"))\n', encoding='utf-8', newline='')
        wrapper.chmod(0o755)
        env.update(MCPLINK_BIN=str(wrapper), AA_MCP_FIXTURE=str(state))
        check('plain audit validates declarations without invoking mcplink', cli('audit')['mcp_checked'] is False and not calls())
        plan = cli('deploy')
        cli('deploy', '--apply', '--plan', plan['plan_id'])
        check('ordinary deployment copies skills and never configures MCP', not calls() and all((home / f'.{k}/skills/demo/SKILL.md').exists() for k in ['codex', 'claude', 'hermes']))
        check('unbound required dependency blocks with no service probing', cli('mcp', 'check', expected=2)['status'] == 'BLOCKED' and not calls())
        malformed = read(manifest_path); malformed['packages'][ident]['requirements']['mcp'][0]['connection_id'] = 'forbidden-device-id'
        write(manifest_path, malformed)
        invalid = cli('audit', expected=2)
        check('machine-specific/unknown declaration fields are rejected offline', any(i['code'] == 'INVALID_MCP_DECLARATION' for i in invalid['issues']) and not calls())
        write(manifest_path, manifest)
        cli('mcp', 'bind', '--service', 'notes', '--connection', 'conn-a', expected=2)
        check('binding needs explicit review before any mcplink invocation', not calls())
        bound = cli('mcp', 'bind', '--service', 'notes', '--connection', 'conn-a', '--source', 'codex=codex:user', '--source', 'claude=claude:user', '--source', 'hermes=hermes:user', '--reviewed')
        cli('mcp', 'bind', '--service', 'cli-notes', '--connection', 'conn-a', '--reviewed')
        check('local binding creates no native entries', bound['native_configuration_changed'] is False and not read(state)['bindings'])
        local_path = repo / '.local/mcp-bindings.json'; local = read(local_path)
        project_source = dict(base['sources'][0], id='codex:source:project', path=str(work / 'project/.codex/config.toml'))
        altered = read(state);altered['sources'].append(project_source);write(state,altered)
        cli('mcp','bind','--service','notes','--connection','conn-a','--source','codex=codex:source:project','--reviewed',expected=2)
        check('manual project sources with null scope/project are refused',read(local_path)==local)
        altered=read(state);altered['sources'][-1]['scope']='user';write(state,altered)
        cli('mcp','bind','--service','notes','--connection','conn-a','--source','codex=codex:source:project','--reviewed',expected=2)
        check('declaring user scope does not authorize an arbitrary project path',read(local_path)==local)
        altered=read(state);altered['sources'][0]['path']=project_source['path'];write(state,altered)
        cli('mcp','bind','--service','notes','--connection','conn-a','--source','codex=codex:user','--reviewed',expected=2)
        check('default source identity cannot substitute for native profile path verification',read(local_path)==local)
        write(state,base)
        offline = cli('mcp', 'check', expected=2)
        check('offline mapping is not live readiness', not offline['ready'] and all(r['status'] == 'BOUND_UNVERIFIED' for r in offline['checks']))
        live = cli('mcp', 'check', '--live', expected=2)
        check('CLI dependencies work without native entries and native dependencies stay blocked', all(r['ready'] == (r['via'] == 'cli') for r in live['checks']))
        connect('--apply', '--reviewed', expected=2)
        check('apply without preview performs no native write', not read(state)['bindings'])
        expired = read(state); expired['expired'] = True; write(state, expired)
        preview = connect()
        refused = connect('--apply', '--reviewed', '--plan', preview['plan_id'], expected=2)
        check('expired provider preview stops before any apply', 'expired' in refused['reason'] and not read(state)['bindings'])
        expired = read(state); expired['expired'] = False; write(state, expired)
        preview = connect()
        check('explicit preview is scoped to three shared targets without native writes', len(preview['changes']) == 3 and not read(state)['bindings'])
        plan_path = repo / '.local/mcp-plans' / (preview['plan_id'] + '.json')
        original_plan = read(plan_path); tampered = dict(original_plan, mcplink_plan='other-plan'); write(plan_path, tampered)
        refused = connect('--apply', '--reviewed', '--plan', preview['plan_id'], expected=2)
        check('tampered provider plan receipt cannot be applied', 'receipt changed' in refused['reason'])
        write(plan_path, original_plan)
        local_path = repo / '.local/mcp-bindings.json'; local = read(local_path)
        changed = json.loads(json.dumps(local)); changed['services']['notes']['connection_id'] = 'other-connection'; write(local_path, changed)
        refused = connect('--apply', '--reviewed', '--plan', preview['plan_id'], expected=2)
        check('mapping changes invalidate frozen preview', 'unchanged explicit preview' in refused['reason'])
        write(local_path, local)
        applied = connect('--apply', '--reviewed', '--plan', preview['plan_id'])
        ready = cli('mcp', 'check', '--live')
        audit = cli('audit', '--mcp')
        check('all three native dependencies and CLI dependencies validate after explicit apply', applied['applied'] and ready['ready'] and audit['mcp']['ready'])
        connect('--apply', '--reviewed', '--plan', preview['plan_id'], expected=2)
        check('completed plans are not replayed', sum(c[:2] == ['changes', 'apply'] for c in calls()) == 1)
        healthy=read(state);original_local=read(local_path)
        aliases=read(state)
        for b in aliases['bindings']:b['name']='external_'+b['sourceId'].split(':')[0]
        aliases['bindings'][0]['name']='external_codex_中文'
        write(state,aliases)
        alias_ready=cli('mcp','check','--live')
        check('native names may differ from the managed connection name',alias_ready['ready'] and all(r['native_name'].startswith('external_') for r in alias_ready['checks'] if r['via']=='native'))
        alias_preview=connect()
        check('alias-preserving native previews are accepted',all(c['name'].startswith('external_') for c in alias_preview['changes']))
        aliases=read(state);aliases['bindings'].append(dict(aliases['bindings'][0],name='second_alias'));write(state,aliases)
        ambiguous=cli('mcp','check','--live',expected=2)
        check('multiple aliases require explicit local disambiguation',any(r['status']=='NATIVE_AMBIGUOUS' for r in ambiguous['checks']))
        connect(expected=2)
        cli('mcp','bind','--service','notes','--connection','conn-a','--native-name','codex=second_alias','--reviewed')
        check('an exact existing alias can be selected locally',cli('mcp','check','--live')['ready'])
        single=['mcp','connect','--skill','demo','--service','notes','--instance','codex']
        unsupported=read(state);unsupported['ignore_binding_target']=True;write(state,unsupported)
        refused=cli(*single,expected=2)
        check('backends that ignore alias selection are refused without native writes','did not honor' in refused['reason'] and read(state)['bindings']==unsupported['bindings'])
        unsupported=read(state);unsupported['ignore_binding_target']=False;write(state,unsupported)
        one=cli(*single)
        check('targeted preview addresses the selected alias through mcplink',len(one['changes'])==1 and one['changes'][0]['name']=='second_alias')
        cli(*single,'--apply','--plan',one['plan_id'],'--reviewed')
        kept=read(state)['bindings']
        check('applying one alias preserves other native Unicode aliases',{'second_alias','external_codex_中文'} <= {b['name'] for b in kept if b['sourceId']=='codex:user'})
        write(state,healthy);write(local_path,original_local)
        paused = read(state); paused['gateway']['paused'] = True; write(state, paused)
        check('paused gateway never reports ready or auto-resumes', all(r['status'] == 'GATEWAY_PAUSED' for r in cli('mcp', 'check', '--live', expected=2)['checks']))
        connect(expected=2)
        paused = read(state); paused['gateway']['paused'] = False; write(state, paused)
        bad = read(state); bad['tools'] = []; write(state, bad)
        missing = cli('mcp', 'check', '--mode', 'cli-read', '--live', expected=2)
        check('selecting optional mode requires its real tools', all(not r['optional'] and r['status'] == 'TOOLS_MISSING' for r in missing['checks']))
        bad = read(state); bad['tools'] = ['read_item']; bad['connections'][0]['status'] = 'configured'; bad['auth_fail'] = True; write(state, bad)
        probe_count=sum(c[:2]==['connections','tools'] for c in calls())
        auth = cli('mcp', 'check', '--mode', 'native-read', '--live', expected=2)
        check('first needs_auth transition uses fresh probe metadata for every target', all(r['status'] == 'NEEDS_AUTH' for r in auth['checks']) and sum(c[:2]==['connections','tools'] for c in calls())==probe_count+1)
        check('fresh authentication diagnostics never leak raw error', 'SYNTHETIC_PRIVATE_ENDPOINT' not in json.dumps(auth))
        bad=read(state);bad['probe_status']='error';write(state,bad)
        changed_auth=cli('mcp','check','--mode','native-read','--live',expected=2)
        check('stale needs_auth does not override a new generic probe failure',all(r['status']=='PROBE_FAILED' for r in changed_auth['checks']))
        bad = read(state); bad['auth_fail'] = False; bad['connections'][0]['status'] = 'ready'; bad['bindings'][0]['changed'] = True; write(state, bad)
        check('external native changes block readiness', any(r['status'] == 'NATIVE_CHANGED_OR_STALE' for r in cli('mcp', 'check', '--live', expected=2)['checks']))
        connect(expected=2)
        bad = read(state); bad['bindings'][0]['changed'] = False; bad['bindings'][0]['enabled'] = False; write(state, bad)
        check('disabled native entries are not ready', any(r['status'] == 'NATIVE_NOT_BOUND' for r in cli('mcp', 'check', '--live', expected=2)['checks']))
        bad = read(state); bad['bindings'][0]['enabled'] = True; bad['connections'][0]['active'] = False; write(state, bad)
        check('disabled upstream never passes live readiness', all(r['status'] == 'CONNECTION_DISABLED' for r in cli('mcp', 'check', '--live', expected=2)['checks']))
        bad = read(state); bad['connections'][0]['active'] = True; bad['agents'][0]['installed'] = False; write(state, bad)
        cli('mcp', 'bind', '--service', 'notes', '--connection', 'conn-a', '--source', 'codex=codex:user', '--reviewed', expected=2)
        check('uninstalled clients are not writable targets', read(local_path) == local)
        bad = read(state); bad['agents'][0]['installed'] = True; write(state, bad)
        bad = read(state); bad['bindings'][0]['changed'] = False; bad['sources'][0]['readOnly'] = True; write(state, bad)
        cli('mcp', 'bind', '--service', 'notes', '--connection', 'conn-a', '--source', 'codex=codex:user', '--reviewed', expected=2)
        check('read-only/native plugin sources are not writable targets', read(local_path) == local)
        bad = read(state); bad['sources'][0]['readOnly'] = False; bad['connections'][0]['id'] = 'renamed-id'; write(state, bad)
        missing = cli('mcp', 'check', '--live', expected=2)
        check('missing exact connection never selects same-name account', all(r['status'] == 'CONNECTION_MISSING' for r in missing['checks']))
        write(state, base)
        preview = connect(); partial = read(state); partial['partial'] = True; write(state, partial)
        partial = connect('--apply', '--reviewed', '--plan', preview['plan_id'], expected=2)
        before = len(calls()); connect('--apply', '--reviewed', '--plan', preview['plan_id'], expected=2)
        check('partial results retained and retry blocked before contacting backend', not partial['applied'] and len(calls()) == before and 'SYNTHETIC_PRIVATE_ENDPOINT' not in json.dumps(partial))
        write(state, base)
        preview = connect(); uncertain = read(state); uncertain['uncertain'] = True; write(state, uncertain)
        unclear = connect('--apply', '--reviewed', '--plan', preview['plan_id'], expected=2)
        receipt = read(repo / '.local/mcp-plans' / (preview['plan_id'] + '.json'))
        before = len(calls()); connect('--apply', '--reviewed', '--plan', preview['plan_id'], expected=2)
        check('uncertain applied response is retained and not replayed or leaked', receipt['state'] == 'UNCERTAIN' and len(calls()) == before and 'SYNTHETIC_PRIVATE_ENDPOINT' not in json.dumps(unclear))
        check('no business calls, imports or login performed by workflow', all(c[0] in ['schema', 'status', 'connections', 'changes', 'rpc'] for c in calls()))
        check('local bindings and receipts are ignored by Git', not run(['git', 'ls-files', '.local']).strip())
        clone = work / 'other-device'
        run(['git', 'clone', repo, clone])
        check('another Git clone receives no local binding or credentials', not (clone / '.local').exists())
        cleanup_workdir(work, 'aa-mcp-')
        report['status'] = 'PASS'
        report['source_sha256'] = hashlib.sha256((ROOT / 'src/agent_asset_kit/skill_mcp.py').read_bytes()).hexdigest()
        print(json.dumps({'status': 'PASS', 'assertions': len(report['assertions']), 'report': str(out / 'report.json')}))
    finally:
        write(out / 'report.json', report)


if __name__ == '__main__':
    main()
