#!/usr/bin/env python3
"""Portable CLI E2E: six adapters, two Git clones, intake/trials/backflow.

All homes, repositories, evidence and native protocols are synthetic. No account,
native model turn, real Agent configuration or external service is used.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / 'src/agent_asset_kit/resources/library'
KINDS = ('codex', 'claude', 'hermes', 'opencode', 'zcode', 'deepseekharness')
B_KINDS = ('codex', 'opencode', 'zcode', 'deepseekharness')


def cleanup_workdir(work):
    target = work.resolve()
    if target.parent != Path(tempfile.gettempdir()).resolve() or not target.name.startswith('aa-workflows-'):
        raise RuntimeError('refuse cleanup outside the synthetic workflow directory')

    def remove_readonly(function, path, exc_info):
        item = Path(path)
        error = exc_info[1]
        info = item.lstat()
        attributes = getattr(info, 'st_file_attributes', 0)
        if (os.name != 'nt' or function not in (os.unlink, os.remove)
                or not isinstance(error, PermissionError) or not stat.S_ISREG(info.st_mode)
                or attributes & 0x400 or not attributes & 0x1
                or not item.resolve().is_relative_to(target)):
            raise error
        item.chmod(info.st_mode | stat.S_IWRITE)
        function(path)

    shutil.rmtree(target, onerror=remove_readonly)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scanner', required=True)
    args = parser.parse_args()
    scanner = Path(args.scanner).resolve()
    run_id = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid())
    output = ROOT / 'artifacts/e2e/workflows' / run_id
    output.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix='aa-workflows-'))
    a, b, upstream, origin = (work / name for name in ('repo-a', 'repo-b', 'upstream', 'origin.git'))
    home_a, home_b = work / 'Mac home 示例', work / 'Windows home 示例'
    commands, assertions = [], []
    report = {'status': 'FAIL', 'environment': platform.platform(), 'python': sys.version,
              'scope': 'synthetic six-Agent protocols and two real Git clones; no native model/provider',
              'workdir': str(work), 'scanner_sha256': hashlib.sha256(scanner.read_bytes()).hexdigest()}

    def execute(argv, cwd=a, expected=0):
        p = subprocess.run([str(x) for x in argv], cwd=cwd, capture_output=True,
                           text=True, encoding='utf-8', timeout=90)
        commands.append({'argv': [str(x) for x in argv], 'cwd': str(cwd), 'exit': p.returncode,
                         'stdout': p.stdout, 'stderr': p.stderr})
        assert p.returncode == expected, f'{argv[:4]}: {p.returncode}, expected {expected}: {p.stdout[:600]} {p.stderr[:300]}'
        return p.stdout

    def cli(*argv, cwd=a, expected=0):
        return json.loads(execute(['aak', '--root', str(cwd), *argv], cwd, expected))

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding='utf-8', newline='')

    def read(path):
        return json.loads(path.read_text(encoding='utf-8'))

    def check(name, condition):
        assert condition, name
        assertions.append(name)

    def configure(repo, home, agents):
        cli('configure', '--home', home, '--agents', ','.join(agents), '--gitleaks', scanner, cwd=repo)
        p = repo / '.local/config.json';value = read(p);value['publication_enabled'] = True
        write(p, json.dumps(value))
        execute(['git', 'config', 'user.name', 'Synthetic E2E'], repo)
        execute(['git', 'config', 'user.email', 'fixture@example.invalid'], repo)
        execute(['git', 'config', 'core.longpaths', 'true'], repo)

    def sync(repo=a):
        return cli('sync', '--agent', 'codex', '--message', 'Synthetic reviewed revision', '--local-only', cwd=repo)['head']

    def apply_deploy(repo=a):
        preview = cli('deploy', cwd=repo)
        return cli('deploy', '--apply', '--plan', preview['plan_id'], cwd=repo)

    def native(repo, kind):
        config = read(repo / '.local/config.json')
        return Path(config['agents'][kind]['skills_root']) / 'workflow-demo'

    def evidence(repo, ident, digest, status='PASS'):
        p = repo / '.local/evidence' / (digest + '-' + status + '.json')
        write(p, json.dumps({'skill_id': ident, 'sha256': digest, 'status': status,
                             'checks': [{'scope': 'synthetic file observation', 'observed': status}]}))
        return p

    def start(repo, ident, kind, ref='HEAD'):
        preview = cli('trial', 'start', '--skill-id', ident, '--target', kind,
                      '--ref', ref, '--agent', 'codex', '--reviewed', cwd=repo)
        return cli('trial', 'start', '--skill-id', ident, '--target', kind,
                   '--ref', ref, '--agent', 'codex', '--reviewed', '--apply',
                   '--plan', preview['plan_id'], cwd=repo)

    def pass_trial(repo, trial):
        ident = read(repo / '.local/trials' / trial['trial_id'] / 'trial.json')['skill_id']
        cli('trial', 'finish', trial['trial_id'], '--result', 'passed', '--reason', 'Observed exact synthetic files',
            '--evidence', evidence(repo, ident, trial['sha256']), '--reviewed', cwd=repo)

    try:
        a.mkdir();upstream.mkdir()
        for folder in ('templates',):
            shutil.copytree(SEED / folder, a / folder, ignore=shutil.ignore_patterns('__pycache__'))
        (a / 'agents').mkdir()
        for name in ('.gitignore', '.gitattributes'):
            shutil.copy2(SEED / name, a / name)
        write(a / 'README.md', '# Synthetic workflow repository\n')
        (a / '.agent-asset-kit.json').write_text('{"tool":"AgentAssetKit","format_version":1}\n', encoding='utf-8')
        execute(['git', 'init', '-b', 'main'])
        configure(a, home_a, ('codex', 'claude', 'hermes'))
        report['git_version'] = execute(['git', '--version']).strip()
        report['scanner_version'] = execute([scanner, 'version']).strip()
        execute(['git', 'init', '-b', 'main'], upstream)
        execute(['git', 'config', 'user.name', 'Synthetic upstream'], upstream)
        execute(['git', 'config', 'user.email', 'upstream@example.invalid'], upstream)
        body = ('---\nname: workflow-demo\ndescription: Synthetic workflow skill.\n---\n\n'
                'Local detail base\n\nRemote detail base\n')
        write(upstream / 'skills/demo/SKILL.md', body)
        write(upstream / 'skills/demo/scripts/helper.py', 'print("fixture business result: 42")\n')
        write(upstream / 'LICENSE', 'MIT License\nCopyright Synthetic Fixture\n')
        execute(['git', 'add', '.'], upstream)
        execute(['git', 'update-index', '--chmod=+x', 'skills/demo/scripts/helper.py'], upstream)
        execute(['git', 'commit', '-m', 'Synthetic upstream v1'], upstream)
        source_ref = execute(['git', 'rev-parse', 'HEAD'], upstream).strip()
        fetched = cli('fetch', upstream, '--local-source', '--path', 'skills/demo', '--ref', source_ref)
        candidate = fetched['candidate']
        check('fetch pins commit, includes license and performs no native install',
              fetched['origin']['commit'] == source_ref and fetched['license_file'] == 'LICENSE' and
              all(not native(a, kind).exists() for kind in ('codex', 'claude', 'hermes')))
        cli('accept', candidate, '--agent', 'codex', '--reviewed', expected=2)
        cli('evaluate', candidate, '--decision', 'trial', '--reason', 'Useful synthetic fixture',
            '--use-case', 'Read exact helper result', '--agent', 'codex', '--reviewed')
        metadata_path = a / '.local/candidates' / candidate / 'candidate.json'
        metadata = read(metadata_path); changed_metadata = dict(metadata, source='https://github.com/other/source')
        write(metadata_path, json.dumps(changed_metadata))
        refused = cli('accept', candidate, '--agent', 'codex', '--reviewed', expected=2)
        check('source metadata cannot change after semantic evaluation', 'metadata changed' in refused['reason'])
        write(metadata_path, json.dumps(metadata))
        ident = cli('accept', candidate, '--agent', 'codex', '--reviewed')['skill_id']
        for kind in KINDS:
            cli('enable', '--skill-id', ident, '--target', kind, '--agent', 'codex', '--reviewed')
        first_ref = sync()
        check('reviewed intake is not globally deployed before adoption', not cli('deploy')['actions'])
        refused = cli('deploy', '--apply', expected=2)
        check('stable apply without a preview plan stops before writing a journal or native copies',
              'preview plan' in refused['reason'] and not (a / '.local/deployments').exists() and
              all(not native(a, kind).exists() for kind in ('codex', 'claude', 'hermes')))
        refused = cli('trial', 'start', '--skill-id', ident, '--target', 'codex',
                      '--agent', 'codex', '--reviewed', '--apply', expected=2)
        check('trial apply without a preview plan stops before writing receipts or native copies',
              'preview plan' in refused['reason'] and not (a / '.local/trials').exists() and
              not (a / '.local/deployments').exists() and not native(a, 'codex').exists())
        wrong_target = cli('trial', 'start', '--skill-id', ident, '--target', 'claude',
                           '--agent', 'codex', '--reviewed')
        refused = cli('trial', 'start', '--skill-id', ident, '--target', 'codex',
                      '--agent', 'codex', '--reviewed', '--apply', '--plan', wrong_target['plan_id'], expected=2)
        check('trial plan for another target is rejected before writing receipts or native copies',
              'plan expired' in refused['reason'] and not (a / '.local/trials').exists() and
              not native(a, 'codex').exists() and not native(a, 'claude').exists())
        trial = start(a, ident, 'codex')
        check('initial trial affects only the selected Agent', native(a, 'codex').is_dir() and
              not native(a, 'claude').exists() and not native(a, 'hermes').exists())
        observed = execute([sys.executable, native(a, 'codex') / 'scripts/helper.py'])
        check('synthetic skill helper actually executes the expected behavior', observed.strip() == 'fixture business result: 42')
        pass_trial(a, trial)
        cli('promote', '--skill-id', ident, '--ref', first_ref, '--agent', 'codex', '--reason', 'Synthetic trial passed', '--reviewed')
        sync()
        wrong_target = cli('deploy', '--target', 'claude')
        refused = cli('deploy', '--apply', '--target', 'hermes', '--plan', wrong_target['plan_id'], expected=2)
        check('stable deployment cannot substitute another target after preview',
              'plan expired' in refused['reason'] and not native(a, 'claude').exists() and not native(a, 'hermes').exists())
        apply_deploy()
        check('stable adoption deploys to all three local targets and skips absent Agent kinds',
              all(native(a, kind).is_dir() for kind in ('codex', 'claude', 'hermes')) and
              not Path(read(a / '.local/config.json')['agents']['opencode']['skills_root']).exists() and
              not Path(read(a / '.local/config.json')['agents']['zcode']['skills_root']).exists() and
              not Path(read(a / '.local/config.json')['agents']['deepseekharness']['skills_root']).exists())
        tick = cli('tick')
        check('manual tick uses and records a frozen deployment plan without network in an unlinked fixture',
              tick['status'] == 'PASS' and tick['deployment']['plan_id'] and not tick['deployment']['actions'] and
              read(Path(tick['deployment']['journal']))['state'] == 'COMPLETE')
        legacy_path = a / 'skills/manifest.json'; legacy = read(legacy_path)
        expected_hash = legacy['packages'][ident]['sha256'];legacy['packages'][ident].pop('file_modes')
        write(legacy_path, json.dumps(legacy))
        cli('revision', '--skill-id', ident, '--agent', 'codex', '--reviewed')
        migrated = read(legacy_path)['packages'][ident]
        check('revising legacy metadata preserves Git executable intent and the original cross-platform hash',
              migrated['file_modes']['scripts/helper.py'] == 0o755 and migrated['sha256'] == expected_hash)
        sync()
        execute(['git', 'clone', '--bare', str(a), str(origin)], work)
        execute(['git', 'remote', 'add', 'origin', str(origin)])
        execute(['git', 'clone', str(origin), str(b)], work)
        configure(b, home_b, B_KINDS)
        apply_deploy(b)
        check('second computer uses one shared library with its different Agent combination',
              all(native(b, kind).is_dir() for kind in B_KINDS) and
              not (home_b / '.claude').exists() and not (home_b / '.hermes').exists())
        check('roundtrip collection produces no false executable-mode delta', not cli('collect', cwd=b)['candidates'])

        # Real protocol subprocesses, synthetic catalogs: no model request is sent.
        probe = work / 'native-probe.py'
        write(probe, '''import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--kind');p.add_argument('--root');a,rest=p.parse_known_args()
rows=[{'name':'workflow-demo','path':str(Path(a.root)/'workflow-demo/SKILL.md'),'visible':True}]
if a.kind in ('opencode','zcode','deepseekharness','opencode-v2','deepseek-incomplete'):
 expected={'opencode':['debug','skill'],'zcode':['skills','list','--json'],
           'deepseekharness':['catalog'],'deepseek-incomplete':['catalog'],
           'opencode-v2':['api','GET','/api/skill','--standalone']}[a.kind]
 assert rest==expected,rest
 if a.kind=='opencode-v2':
  print(json.dumps({'data':[{'id':'builtin','path':'skill://builtin/SKILL.md'},*rows]}))
 elif a.kind=='deepseek-incomplete':print(json.dumps({'complete':False,'skills':rows}))
 else:print(json.dumps({'skills':rows}))
 sys.exit(0)
for line in sys.stdin:
 m=json.loads(line)
 if a.kind=='codex':
  if m.get('method')=='initialize':print(json.dumps({'id':m['id'],'result':{}}),flush=True)
  elif m.get('method')=='skills/list':print(json.dumps({'id':m['id'],'result':{'data':[{'skills':rows,'errors':[]}]}}),flush=True)
 elif m.get('type')=='control_request':
  assert m['request']['subtype']=='initialize'
  print(json.dumps({'type':'control_response','response':{'request_id':m['request_id'],'subtype':'success','response':{'commands':[{'name':'workflow-demo'}]}}}),flush=True)
''')
        for repo in (a, b):
            config_path = repo / '.local/config.json';config = read(config_path)
            for kind, row in config['agents'].items():
                if not row['enabled'] or kind == 'shared':
                    continue
                if kind == 'hermes':
                    runtime = work / 'synthetic-hermes'
                    write(runtime / 'tools/__init__.py', '')
                    write(runtime / 'tools/skills_tool.py', 'import os\nfrom pathlib import Path\ndef _skill_catalog(skip_disabled=False):\n p=Path(os.environ["HERMES_HOME"])/"skills/workflow-demo/SKILL.md"\n return [{"name":"workflow-demo","load_name":"workflow-demo","path":p}]\n')
                    row.update(runtime=str(runtime), interpreter=sys.executable)
                else:
                    row['command'] = [sys.executable, str(probe), '--kind', kind, '--root', row['skills_root']]
                    if kind == 'deepseekharness':
                        row['probe_command'] = [*row['command'], 'catalog']
            write(config_path, json.dumps(config))
            doctor = cli('doctor', '--native', cwd=repo)
            check('native initialization contracts discover exact configured paths on ' + repo.name,
                  all(row.get('discovery') == 'VISIBLE' for row in doctor['copies']) and
                  all(value['model_turns_sent'] == 0 for value in doctor['native_discovery'].values()))

        config_path = b / '.local/config.json'; original = read(config_path)
        changed = read(config_path); row = changed['agents']['opencode']
        row['probe_command'] = [sys.executable, str(probe), '--kind', 'opencode-v2', '--root',
                                row['skills_root'], 'api', 'GET', '/api/skill', '--standalone']
        write(config_path, json.dumps(changed))
        doctor = cli('doctor', '--native', '--target', 'opencode', cwd=b)
        builtin = next(s for s in doctor['native_discovery']['opencode']['skills'] if s['name'] == 'builtin')
        check('OpenCode v2 virtual builtin URIs do not masquerade as filesystem copies',
              builtin['path'] is None and builtin['resource_uri'] == 'skill://builtin/SKILL.md' and
              all(row['discovery'] == 'VISIBLE' for row in doctor['copies']))
        changed = read(config_path); row = changed['agents']['deepseekharness']
        row['probe_command'] = [sys.executable, str(probe), '--kind', 'deepseek-incomplete',
                                '--root', row['skills_root'], 'catalog']
        write(config_path, json.dumps(changed))
        doctor = cli('doctor', '--native', '--target', 'deepseekharness', cwd=b, expected=2)
        check('incomplete native DeepSeek catalogs remain unverified without a model request',
              doctor['native_discovery']['deepseekharness']['status'] == 'UNVERIFIED' and
              doctor['native_discovery']['deepseekharness']['model_turns_sent'] == 0 and
              all(row['discovery'] == 'UNVERIFIED' for row in doctor['copies']))
        write(config_path, json.dumps(original))

        # Platform selectors must skip inapplicable modes without hiding missing local requirements.
        manifest_path = b / 'skills/manifest.json'; manifest_text = manifest_path.read_text(encoding='utf-8')
        releases_path = b / 'skills/releases.json'; releases_text = releases_path.read_text(encoding='utf-8')
        manifest = json.loads(manifest_text)
        existing_file = work / 'existing dependency 示例.txt'; write(existing_file, 'fixture data')
        manifest['packages'][ident]['requirements'] = {
            'commands': [
                {'mode': 'other-platform', 'any_of': ['agentassets-missing-tool'],
                 'platforms': ['darwin' if sys.platform != 'darwin' else 'win32']},
                {'mode': 'inactive-agent', 'any_of': ['agentassets-missing-tool'], 'requires_agent': 'hermes'},
                {'mode': 'current-platform', 'any_of': ['agentassets-missing-tool'], 'platforms': [sys.platform]},
            ],
            'files': [{'mode': 'platform-file', 'path': str(work / 'old-platform-path'),
                       'paths_by_platform': {sys.platform: [str(work / 'missing alternative'), str(existing_file)]}}],
            'python': [{'mode': 'platform-python', 'interpreter': '.venv/bin/python',
                        'interpreter_by_platform': {sys.platform: '.venv/selected/python'},
                        'distributions': ['synthetic-runtime']}],
        }
        write(manifest_path, json.dumps(manifest))
        cli('revision', '--skill-id', ident, '--agent', 'codex', '--reviewed', cwd=b)
        audit = cli('audit', '--target', 'codex', cwd=b, expected=2)
        requirements = next(p for p in audit['packages'] if p['skill_id'] == ident)['requirements']
        check('platform and inactive-Agent modes are explicitly not applicable',
              sum(r['status'] == 'NOT_APPLICABLE' for r in requirements) == 2)
        check('missing dependencies on the current platform still block the audit',
              any(r['mode'] == 'current-platform' and r['status'] == 'MISSING' for r in requirements) and
              any(i['code'] == 'RUNTIME_REQUIREMENT_MISSING' and i['severity'] == 'error' for i in audit['issues']))
        check('file requirements select an existing native platform alternative',
              any(r['mode'] == 'platform-file' and r['status'] == 'FOUND' and
                  r['file'] == str(existing_file) for r in requirements))
        runtime = next(r for r in audit['copies'] if r['skill_id'] == ident)['runtime'][0]
        check('native Python selection keeps a missing platform interpreter visible as an error',
              runtime['status'] == 'MISSING' and Path(runtime['interpreter']) == native(b, 'codex') / '.venv/selected/python' and
              any(i['code'] == 'PYTHON_RUNTIME_NOT_READY' and i['severity'] == 'error' for i in audit['issues']))
        write(manifest_path, manifest_text)
        write(releases_path, releases_text)

        # L changes on B while R changes on A: preserve and merge both against Git B.
        preview = cli('deploy', cwd=b)
        local_file = native(b, 'codex') / 'SKILL.md'
        write(local_file, local_file.read_text(encoding='utf-8').replace('Local detail base', 'Local detail edited'))
        tick = cli('tick', cwd=b, expected=2)
        check('tick preserves pending native edits and stops before refresh or deployment',
              tick['status'] == 'BLOCKED' and tick['collection']['pending'] == 1 and
              'refresh' not in tick and 'deployment' not in tick and 'Local detail edited' in local_file.read_text(encoding='utf-8'))
        cli('deploy', '--apply', '--plan', preview['plan_id'], cwd=b, expected=2)
        collected = cli('collect', cwd=b)['candidates'][0]['id']
        source = a / 'skills/third-party/workflow-demo/SKILL.md'
        write(source, source.read_text(encoding='utf-8').replace('Remote detail base', 'Remote detail updated'))
        cli('revision', '--skill-id', ident, '--agent', 'codex', '--reviewed');sync()
        execute(['git', 'push', '--set-upstream', 'origin', 'main']);execute(['git', 'pull', '--ff-only'], b)
        proposal = cli('merge', '--candidate', collected, cwd=b)
        check('independent local and remote edits merge without losing either side', not proposal['conflicts'])
        merged = cli('merge', '--finalize', proposal['merge_id'], '--reviewed', cwd=b)['candidate']
        cli('accept', merged, '--agent', 'codex', '--reviewed', cwd=b);sync(b);apply_deploy(b)
        check('reviewed merged result propagates to all enabled second-computer Agents',
              all('Local detail edited' in (native(b, kind) / 'SKILL.md').read_text(encoding='utf-8') and
                  'Remote detail updated' in (native(b, kind) / 'SKILL.md').read_text(encoding='utf-8')
                  for kind in B_KINDS))

        # An updated reviewed version must not replace old stable installations.
        source = b / 'skills/third-party/workflow-demo/SKILL.md'
        before = source.read_bytes()
        write(source, source.read_text(encoding='utf-8') + '\nTrial-only update\n')
        cli('revision', '--skill-id', ident, '--agent', 'codex', '--trial', '--reviewed', cwd=b)
        update_ref = sync(b)
        apply_deploy(b)
        check('stable release stays unchanged while an update awaits trial',
              all((native(b, kind) / 'SKILL.md').read_bytes() == before for kind in B_KINDS))
        failed = start(b, ident, 'zcode', update_ref)
        check('trial update remains isolated from the other Agent copies',
              b'Trial-only update' in (native(b, 'zcode') / 'SKILL.md').read_bytes() and
              (native(b, 'codex') / 'SKILL.md').read_bytes() == before)
        cli('trial', 'finish', failed['trial_id'], '--result', 'failed', '--reason', 'Synthetic rejection',
            '--evidence', evidence(b, ident, failed['sha256'], 'FAIL'), '--reviewed', cwd=b)
        cli('trial', 'rollback', failed['trial_id'], '--reviewed', cwd=b)
        check('failed trial restores previous exact content', (native(b, 'zcode') / 'SKILL.md').read_bytes() == before)
        protected = start(b, ident, 'zcode', update_ref)
        receipt_path = b / '.local/trials' / protected['trial_id'] / 'trial.json'
        interrupted = read(receipt_path);interrupted['state'] = 'interrupted'
        write(receipt_path, json.dumps(interrupted))
        check('a stale interrupted receipt recognizes the completed installation without replaying writes',
              cli('trial', 'recover', protected['trial_id'], '--reviewed', cwd=b)['state'] == 'active')
        changed = native(b, 'zcode') / 'SKILL.md';unchanged = changed.read_bytes();changed.write_bytes(unchanged + b'\nUncaptured local edit\n')
        cli('trial', 'rollback', protected['trial_id'], '--reviewed', cwd=b, expected=2)
        check('rollback refuses to overwrite intervening edits', b'Uncaptured local edit' in changed.read_bytes())
        changed.write_bytes(unchanged)  # Restore only this known synthetic test edit.
        cli('trial', 'rollback', protected['trial_id'], '--reviewed', cwd=b)
        interrupted = read(receipt_path);interrupted['state'] = 'interrupted'
        write(receipt_path, json.dumps(interrupted))
        check('recovery recognizes explicitly restored originals and closes the trial',
              cli('trial', 'recover', protected['trial_id'], '--reviewed', cwd=b)['state'] == 'rolled_back')
        adopted = start(b, ident, 'zcode', update_ref);pass_trial(b, adopted)
        cli('promote', '--skill-id', ident, '--ref', update_ref, '--agent', 'codex',
            '--reason', 'Exact updated version verified', '--reviewed', cwd=b)
        sync(b);apply_deploy(b)
        check('passing trial promotes one common version for all local Agent kinds',
              all(b'Trial-only update' in (native(b, kind) / 'SKILL.md').read_bytes() for kind in B_KINDS))
        execute(['git', 'push', 'origin', 'main'], b);execute(['git', 'pull', '--ff-only'])
        apply_deploy()
        check('another computer receives the same promoted skill through ordinary Git',
              all((native(a, kind) / 'SKILL.md').read_bytes() == (native(b, 'codex') / 'SKILL.md').read_bytes()
                  for kind in ('codex', 'claude', 'hermes')))
        check('both computers remain free from phantom return candidates',
              not cli('collect')['candidates'] and not cli('collect', cwd=b)['candidates'])
        check('registered stable IDs and logical bindings remain shared across computers',
              read(a / 'skills/manifest.json')['packages'][ident]['bindings'] ==
              read(b / 'skills/manifest.json')['packages'][ident]['bindings'])

        # Upstream upgrades preserve the maintained library's local increments.
        write(upstream / 'skills/demo/scripts/helper.py', 'print("fixture business result: 43")\n')
        write(upstream / 'skills/demo/references/new.txt', 'New upstream reference\n')
        execute(['git', 'add', '.'], upstream)
        execute(['git', 'update-index', '--chmod=+x', 'skills/demo/scripts/helper.py'], upstream)
        execute(['git', 'commit', '-m', 'Synthetic upstream v2'], upstream)
        upstream_ref = execute(['git', 'rev-parse', 'HEAD'], upstream).strip()
        update = cli('fetch', upstream, '--local-source', '--path', 'skills/demo', '--ref', upstream_ref)
        check('upstream update resolves the existing identity', update['existing_skill_ids'] == [ident])
        cli('evaluate', update['candidate'], '--decision', 'trial', '--reason', 'Review upstream update',
            '--use-case', 'New helper behavior with local context retained', '--agent', 'codex', '--reviewed')
        cli('accept', update['candidate'], '--agent', 'codex', '--reviewed', expected=2)
        proposal = cli('merge', '--candidate', update['candidate'])
        merged = cli('merge', '--finalize', proposal['merge_id'], '--reviewed')['candidate']
        cli('accept', merged, '--agent', 'codex', '--reviewed')
        ref = sync();upstream_trial = start(a, ident, 'codex', ref)
        check('upstream three-way merge retains local context and adds new resources',
              'Local detail edited' in (native(a, 'codex') / 'SKILL.md').read_text(encoding='utf-8') and
              'Remote detail updated' in (native(a, 'codex') / 'SKILL.md').read_text(encoding='utf-8') and
              (native(a, 'codex') / 'references/new.txt').is_file())
        check('upgraded helper behavior is observed rather than inferred from a file hash',
              execute([sys.executable, native(a, 'codex') / 'scripts/helper.py']).strip() == 'fixture business result: 43')
        pass_trial(a, upstream_trial)
        cli('promote', '--skill-id', ident, '--ref', ref, '--agent', 'codex', '--reason', 'New helper verified', '--reviewed')
        sync();apply_deploy();execute(['git', 'push', 'origin', 'main'])
        execute(['git', 'pull', '--ff-only'], b);apply_deploy(b)
        check('upstream provenance advances without changing the shared identity',
              read(a / 'skills/manifest.json')['packages'][ident]['origin']['commit'] == upstream_ref)

        # Same-line edits preserve all variants and require explicit resolution.
        local = native(b, 'opencode') / 'SKILL.md'
        write(local, local.read_text(encoding='utf-8').replace('Local detail edited', 'Incoming conflict detail'))
        original_local = local.read_bytes()
        candidate = cli('collect', cwd=b)['candidates'][0]['id']
        source = b / 'skills/third-party/workflow-demo/SKILL.md'
        write(source, source.read_text(encoding='utf-8').replace('Local detail edited', 'Repository conflict detail'))
        cli('revision', '--skill-id', ident, '--agent', 'codex', '--reviewed', cwd=b)
        proposal = cli('merge', '--candidate', candidate, cwd=b)
        check('overlapping edits are reported and all three originals remain available',
              'SKILL.md' in proposal['conflicts'] and all((b / '.local/merges' / proposal['merge_id'] / p / 'SKILL.md').is_file()
                                                       for p in ('base', 'incoming', 'repository')))
        cli('merge', '--finalize', proposal['merge_id'], '--reviewed', cwd=b, expected=2)
        merged_path = Path(proposal['merged_package']) / 'SKILL.md'
        write(merged_path, source.read_text(encoding='utf-8') + '\nReviewed incoming detail: Incoming conflict detail\n')
        resolved = cli('merge', '--finalize', proposal['merge_id'], '--reviewed', cwd=b)['candidate']
        local.write_bytes(original_local + b'\nEdit after reconciliation\n')
        cli('accept', resolved, '--agent', 'codex', '--reviewed', cwd=b, expected=2)
        check('acceptance refuses new edits made after a merge proposal', b'Edit after reconciliation' in local.read_bytes())
        local.write_bytes(original_local)
        cli('accept', resolved, '--agent', 'codex', '--reviewed', cwd=b);sync(b);apply_deploy(b)
        check('explicitly reviewed conflict resolution deploys without dropping local detail',
              all('Incoming conflict detail' in (native(b, kind) / 'SKILL.md').read_text(encoding='utf-8')
                  for kind in B_KINDS))

        # Existing categorized bindings are the default trial destinations.
        nested = a / 'skills/custom/nested-demo/SKILL.md'
        write(nested, '---\nname: nested-demo\ndescription: Synthetic categorized skill.\n---\nOld stable.\n')
        nested_id = cli('register', '--path', 'skills/custom/nested-demo', '--targets', 'codex',
                        '--agent', 'codex', '--reviewed')['skill_id']
        cli('enable', '--skill-id', nested_id, '--target', 'hermes', '--path', 'devops/nested-demo',
            '--agent', 'codex', '--reviewed')
        sync();apply_deploy()
        hermes_root = Path(read(a / '.local/config.json')['agents']['hermes']['skills_root'])
        categorized = hermes_root / 'devops/nested-demo/SKILL.md'
        old_bytes = categorized.read_bytes()
        baselines_before = read(a / '.local/baselines.json')
        write(nested, nested.read_text(encoding='utf-8').replace('Old stable.', 'New trial.'))
        cli('revision', '--skill-id', nested_id, '--agent', 'codex', '--trial', '--reviewed')
        nested_ref = sync()
        preview = cli('trial', 'start', '--skill-id', nested_id, '--target', 'hermes',
                      '--ref', nested_ref, '--agent', 'codex', '--reviewed')
        check('trial preview reuses the registered categorized path',
              len(preview['actions']) == 1 and preview['actions'][0]['path'] == 'devops/nested-demo')
        stale = categorized.read_bytes()
        categorized.write_bytes(stale + b'\nNew native edit\n')
        refused = cli('trial', 'start', '--skill-id', nested_id, '--target', 'hermes',
                      '--ref', nested_ref, '--agent', 'codex', '--reviewed', '--apply',
                      '--plan', preview['plan_id'], expected=2)
        check('a stale trial plan preserves new edits and creates no receipt',
              b'New native edit' in categorized.read_bytes() and
              not any(read(p).get('skill_id') == nested_id for p in (a / '.local/trials').glob('*/trial.json')))
        categorized.write_bytes(stale)  # Restore only the known synthetic edit.
        nested_trial = start(a, nested_id, 'hermes', nested_ref)
        codex_copy = Path(read(a / '.local/config.json')['agents']['codex']['skills_root']) / 'nested-demo/SKILL.md'
        check('categorized trial replaces the selected copy without creating a same-name duplicate',
              b'New trial.' in categorized.read_bytes() and not (hermes_root / 'nested-demo').exists() and
              codex_copy.read_bytes() == old_bytes)
        cli('trial', 'rollback', nested_trial['trial_id'], '--reviewed')
        check('categorized trial rollback restores exact content and baseline',
              categorized.read_bytes() == old_bytes and not (hermes_root / 'nested-demo').exists() and
              read(a / '.local/baselines.json')['hermes:devops/nested-demo'] ==
              baselines_before['hermes:devops/nested-demo'])
        isolated_preview = cli('trial', 'start', '--skill-id', nested_id, '--target', 'hermes',
                               '--path', 'sandbox/nested-demo', '--ref', nested_ref,
                               '--agent', 'codex', '--reviewed')
        check('explicit temporary trial path overrides the existing binding',
              len(isolated_preview['actions']) == 1 and isolated_preview['actions'][0]['path'] == 'sandbox/nested-demo' and
              not (hermes_root / 'sandbox/nested-demo').exists())

        # Rejection/retirement are shared decisions, not inferred native deletions.
        write(upstream / 'skills/rejected/SKILL.md', '---\nname: rejected-demo\ndescription: A synthetic rejected source.\n---\nUnused.\n')
        execute(['git', 'add', '.'], upstream);execute(['git', 'commit', '-m', 'Synthetic rejected source'], upstream)
        rejected = cli('fetch', upstream, '--local-source', '--path', 'skills/rejected')
        cli('evaluate', rejected['candidate'], '--decision', 'reject', '--reason', 'No additional value',
            '--use-case', 'Avoid duplicate capability', '--agent', 'codex', '--reviewed')
        cli('fetch', upstream, '--local-source', '--path', 'skills/rejected', expected=2)
        cli('evaluate', rejected['candidate'], '--decision', 'trial', '--reason', 'Attempt reuse',
            '--use-case', 'Fixture', '--agent', 'codex', '--reviewed', expected=2)
        check('rejected candidates cannot silently be fetched or reapproved',
              not (a / 'skills/third-party/rejected-demo').exists())
        cli('decision', '--skill-id', ident, '--decision', 'retired', '--reason', 'Synthetic withdrawal', '--agent', 'codex', '--reviewed', cwd=b)
        check('withdrawn assets stop redistribution without deleting native work',
              not cli('deploy', cwd=b)['actions'] and native(b, 'codex').is_dir())
        cli('decision', '--skill-id', ident, '--decision', 'allow', '--reason', 'Explicit synthetic restore', '--agent', 'codex', '--reviewed', cwd=b)
        check('a later explicit decision can reopen a known asset', not cli('collect', cwd=b)['candidates'])
        decisions_path = b / 'skills/decisions.json'; decisions = read(decisions_path)
        decisions['entries'].append(dict(decisions['entries'][-1], source_scope='repository-and-name',
            repository=fetched['origin']['url'], name='another-skill-in-the-same-repository', skill_id='different-stable-id',
            decision='retired'))
        write(decisions_path, json.dumps(decisions))
        check('retiring one historical repository member does not withdraw its sibling skills',
              all(c['status'] == 'MATCH' for c in cli('audit', cwd=b)['copies']))
        sync(b)

        # Endpoint changes are local and cannot make two aliases overwrite a trial.
        cli('agent', 'add', '--kind', 'codex', '--name', 'codex-alias', '--skills-root', native(b, 'codex').parent,
            '--reviewed', cwd=b)
        apply_deploy(b)
        source = b / 'skills/third-party/workflow-demo/SKILL.md'
        write(source, source.read_text(encoding='utf-8') + '\nAlias-protected trial\n')
        cli('revision', '--skill-id', ident, '--agent', 'codex', '--trial', '--reviewed', cwd=b);ref = sync(b)
        cli('trial', 'start', '--skill-id', ident, '--ref', ref, '--target', 'codex', '--instance', 'codex',
            '--agent', 'codex', '--reviewed', cwd=b, expected=2)
        check('partial trial cannot change an unselected Agent sharing the same root',
              b'Alias-protected trial' not in (native(b, 'codex') / 'SKILL.md').read_bytes())
        cli('agent', 'disable', '--name', 'codex-alias', '--reviewed', cwd=b)

        junction = work / 'linked native root'
        if os.name == 'nt':
            execute(['cmd', '/c', 'mklink', '/J', junction, native(b, 'codex').parent], b)
        else:
            junction.symlink_to(native(b, 'codex').parent, target_is_directory=True)
        cli('agent', 'add', '--kind', 'codex', '--name', 'linked-codex', '--skills-root', junction, '--reviewed', cwd=b)
        refused = cli('deploy', cwd=b, expected=2)
        check('native root symlinks or Windows junctions cannot silently enter deployment', 'not a link' in refused['reason'])
        cli('agent', 'disable', '--name', 'linked-codex', '--reviewed', cwd=b)

        cli('enable', '--skill-id', ident, '--target', 'codex', '--path', 'workflow-demo/child',
            '--agent', 'codex', '--reviewed', cwd=b)
        refused = cli('deploy', cwd=b, expected=2)
        check('nested managed package destinations stop before any native write',
              'cannot contain another managed package' in refused['reason'] and not (native(b, 'codex') / 'child').exists())

        cli('fetch', 'https://user:fixture@example.invalid/x', expected=2)
        cli('fetch', upstream, '--local-source', '--path', '../escape', expected=2)
        check('unsafe or ambiguous sources do not modify native directories',
              not cli('collect')['candidates'])
        report.update(status='PASS', assertion_count=len(assertions))
    except Exception:
        report['error'] = traceback.format_exc()
    if report['status'] == 'PASS':
        try:
            cleanup_workdir(work)
            report['cleanup'] = 'PASS'
        except Exception:
            report.update(status='FAIL', cleanup='FAIL', error=traceback.format_exc())
    else:
        report['cleanup'] = 'SKIPPED_FAILURE'
    report.update(assertions=assertions, commands=commands)
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'assertions': len(assertions),
                      'report': str(output / 'report.json'), 'error': report.get('error')}, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
