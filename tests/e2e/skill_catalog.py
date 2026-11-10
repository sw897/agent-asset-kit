#!/usr/bin/env python3
"""CLI catalog contracts in a synthetic home, including no-turn protocol probes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / 'src/agent_asset_kit/resources/library'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scanner', required=True)
    args = parser.parse_args()
    output = ROOT / 'artifacts/e2e/skill-catalog' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
    repo, home = output / 'repository', output / 'home'
    repo.mkdir(parents=True)
    home.mkdir()
    assertions, commands = [], []
    report = {'status': 'FAIL', 'environment': platform.platform(), 'python': sys.version,
              'scope': 'synthetic CLI, native protocol stubs, no real providers or business operations',
              'scanner_sha256': hashlib.sha256(Path(args.scanner).read_bytes()).hexdigest()}
    env = dict(os.environ)

    def write(path, text, executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if executable:
            path.chmod(0o755)

    def cli(*args, expected=0):
        argv = ['aak', '--root', str(repo), *args]
        p = subprocess.run(argv, cwd=repo, env=env, capture_output=True, text=True, timeout=60)
        commands.append({'argv': argv, 'exit': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
        assert p.returncode == expected, p.stdout[:800] + p.stderr[:200]
        return json.loads(p.stdout)

    def check(name, condition):
        assert condition, name
        assertions.append(name)

    def update(mutator):
        path = repo / 'skills/manifest.json'
        data = json.loads(path.read_text())
        mutator(data)
        path.write_text(json.dumps(data))

    try:
        subprocess.run(['git', 'init', '-b', 'main'], cwd=repo, capture_output=True, check=True)
        (repo / '.agent-asset-kit.json').write_text('{"tool":"AgentAssetKit","format_version":1}\n', encoding='utf-8')
        cli('configure', '--home', str(home), '--gitleaks', str(Path(args.scanner).resolve()),
            '--agents', 'codex,claude,hermes')
        for name in ('alpha', 'beta'):
            write(repo / f'skills/custom/{name}/SKILL.md',
                  f'---\nname: {name}\ndescription: Synthetic {name} skill.\n---\n\n# {name}\n'
                  '[Resource](references/detail.md)\n`![sample](missing-output.png)`\n'
                  '```md\n[example](uncreated-file.md)\n```\n')
            write(repo / f'skills/custom/{name}/references/detail.md', '# Synthetic resource\n')
        alpha = cli('register', '--path', 'skills/custom/alpha', '--targets', 'codex,claude,hermes',
                    '--agent', 'codex', '--reviewed')['skill_id']
        beta = cli('register', '--path', 'skills/custom/beta', '--agent', 'codex', '--reviewed')['skill_id']
        write(repo / 'skills/categories.json', json.dumps({'service': {'label': 'Synthetic service'}}))
        def classify(d):
            for r in d['packages'].values():
                r.update(category='service', tags=['fixture'], summary='Synthetic service')
            d['packages'][alpha]['dependencies'] = [{'skill': 'beta', 'when': 'core'}]
            d['packages'][alpha]['legacy_names'] = ['old-alpha']
        update(classify)
        failed = cli('audit', expected=2)
        check('unbound required dependency blocks audit',
              sum(i['code'] == 'DEPENDENCY_NOT_BOUND' for i in failed['issues']) == 3)
        check('audit never auto-enables a dependency', not json.loads((repo / 'skills/manifest.json').read_text())['packages'][beta]['bindings'])
        cli('index', '--agent', 'codex', '--reviewed', expected=2)
        check('invalid contracts do not produce an index', not (repo / 'skills/README.md').exists())
        for target in ('codex', 'claude', 'hermes'):
            cli('enable', '--skill-id', beta, '--target', target, '--agent', 'codex', '--reviewed')
        def optional(d):
            d['packages'][alpha]['requirements'] = {'files': [
                {'path': str(output / 'missing-data'), 'mode': 'optional-mode', 'optional': True}]}
        update(optional)
        pending = cli('audit')
        check('optional mode absence is a warning with pending installs',
              pending['status'] == 'PENDING' and any(i['code'] == 'RUNTIME_REQUIREMENT_MISSING' and i['severity'] == 'warning' for i in pending['issues']))
        check('inline and fenced examples are not mistaken for package references',
              not any(i['code'] == 'MISSING_FILE_REFERENCE' for i in pending['issues']))
        cli('index', '--agent', 'codex', expected=2)
        cli('index', '--agent', 'codex', '--reviewed')
        generated = (repo / 'skills/README.md').read_bytes()
        cli('index', '--agent', 'codex', '--reviewed')
        check('reviewed index is deterministic and includes classification and dependencies',
              generated == (repo / 'skills/README.md').read_bytes() and b'Synthetic service' in generated and b'beta' in generated)
        before = (repo / 'skills/manifest.json').read_bytes()
        absent = cli('resolve', '--skill', alpha, '--target', 'codex')
        check('resolver reports expected path without fabricating installed state',
              not absent['native_paths'][0]['matches_registered'] and (repo / 'skills/manifest.json').read_bytes() == before)
        for target in ('codex', 'claude', 'hermes'):
            for name in ('alpha', 'beta'):
                shutil.copytree(repo / 'skills/custom' / name, home / ('.' + target) / 'skills' / name)
        legacy = home / '.claude/commands/legacy.md'
        write(legacy, '# Unverified old command\n')
        legacy_hash = hashlib.sha256(legacy.read_bytes()).hexdigest()
        audited = cli('audit')
        check('matching independent files pass while native catalogs remain unclaimed',
              audited['status'] == 'PASS' and len(audited['copies']) == 6 and all(r['status'] == 'MATCH' for r in audited['copies']) and not audited['native_discovery'])
        check('legacy commands are read-only and excluded from managed skills',
              len(audited['claude_legacy_commands']) == 1 and audited['claude_legacy_commands'][0]['origin'] == 'UNVERIFIED'
              and audited['canonical_packages'] == 2 and hashlib.sha256(legacy.read_bytes()).hexdigest() == legacy_hash)
        check('resolver detects installed complete copy', cli('resolve', '--skill', 'alpha', '--target', 'hermes')['native_paths'][0]['matches_registered'])
        alias = cli('resolve', '--skill', 'old-alpha', '--target', 'codex')
        check('legacy caller resolves to its maintained package without installing an old package',
              alias['skill_id'] == alpha and alias['matched_alias'])
        update(lambda d: d['packages'][alpha]['requirements'].update(python=[{
            'interpreter': '.venv/bin/python', 'mode': 'python-mode', 'distributions': ['synthetic-missing']}]))
        missing_runtime = cli('audit', expected=2)
        check('missing package interpreter is a distinct runtime failure even when copies match',
              all(r['status'] == 'MATCH' for r in missing_runtime['copies']) and
              sum(i['code'] == 'PYTHON_RUNTIME_NOT_READY' for i in missing_runtime['issues']) == 3)
        update(lambda d: d['packages'][alpha]['requirements'].pop('python'))
        write(repo / 'skills/custom/alpha/references/detail.md', '[broken](absent.md)\n')
        cli('revision', '--skill-id', alpha, '--agent', 'codex', '--reviewed')
        broken = cli('audit', expected=2)
        check('actual broken relative reference is a hard error', any(i['code'] == 'MISSING_FILE_REFERENCE' for i in broken['issues']))
        write(repo / 'skills/custom/alpha/references/detail.md', '# Synthetic resource\n')
        cli('revision', '--skill-id', alpha, '--agent', 'codex', '--reviewed')
        update(lambda d: d['packages'][alpha].update(category='unknown'))
        check('unknown category is rejected', any(i['code'] == 'UNKNOWN_CATEGORY' for i in cli('audit', expected=2)['issues']))
        update(lambda d: d['packages'][alpha].update(category='service'))
        # Owned protocol stubs provide catalogs; every input frame is logged for assertions.
        bin_dir = output / 'bin'
        env['PATH'] = str(bin_dir) + os.pathsep + env.get('PATH', '')
        env['SYNTHETIC_SKILL_HOME'] = str(home)
        env['SYNTHETIC_PROTOCOL_LOG'] = str(output / 'protocol.jsonl')
        stub = '''import json,os,sys
from pathlib import Path
home=Path(os.environ['SYNTHETIC_SKILL_HOME'])
kind=Path(sys.argv[0]).name
for line in sys.stdin:
 m=json.loads(line)
 with open(os.environ['SYNTHETIC_PROTOCOL_LOG'],'a') as f: f.write(json.dumps({'client':kind,'frame':m})+'\\n')
 if kind=='codex' and m.get('method')=='initialize': out={'id':m['id'],'result':{}}
 elif kind=='codex' and m.get('method')=='initialized': continue
 elif kind=='codex' and m.get('method')=='skills/list':
  out={'id':m['id'],'result':{'data':[{'skills':[{'name':'namespace:beta' if n=='beta' else n,'path':str(home/'.codex/skills'/n/'SKILL.md'),'enabled': not (home/'hidden').exists()} for n in ('alpha','beta')],'errors':[]}]}}
 elif kind=='claude' and m.get('type')=='control_request' and m['request']['subtype']=='initialize':
  out={'type':'control_response','response':{'subtype':'success','request_id':m['request_id'],'response':{'commands':[{'name':n} for n in ('alpha','beta')]}}}
 else: sys.exit(17)
 print(json.dumps(out),flush=True)
'''
        for executable in ('codex', 'claude'):
            write(bin_dir / executable, '#!' + sys.executable + '\n' + stub, executable=True)
        runtime = home / '.hermes/hermes-agent'
        (runtime / 'venv/bin').mkdir(parents=True)
        (runtime / 'venv/bin/python').symlink_to(sys.executable)
        write(runtime / 'tools/__init__.py', '')
        write(runtime / 'tools/skills_tool.py', '''import os
from pathlib import Path
def _skill_catalog(*,skip_disabled=False):
 assert skip_disabled is False
 root=Path(os.environ['SYNTHETIC_SKILL_HOME'])/'.hermes/skills'
 return [{'name':n,'load_name':n,'path':root/n/'SKILL.md','status':'active'} for n in ('alpha','beta')]
''')
        native = cli('doctor', '--native', '--output', str(output / 'native.json'))
        check('native catalogs independently identify all registered copies', native['status'] == 'PASS' and
              all(r['discovery'] == 'VISIBLE' for r in native['copies']))
        check('native namespace is recorded by exact path without changing canonical identity',
              next(r for r in native['copies'] if r['name']=='beta' and r['target']=='codex')['native_names'] == ['namespace:beta'])
        frames = [json.loads(row) for row in (output / 'protocol.jsonl').read_text().splitlines()]
        check('discovery sends initialize and catalog only, no user prompt or model turn',
              len(frames) == 4 and all(r['frame'].get('method') in ('initialize','initialized','skills/list')
              or r['frame'].get('type') == 'control_request' and r['frame']['request']['subtype'] == 'initialize' for r in frames)
              and all(r['model_turns_sent'] == 0 for r in native['native_discovery'].values()))
        write(home / 'hidden', 'synthetic disabled catalog entries')
        hidden = cli('doctor', '--native', '--target', 'codex', expected=2)
        check('native-disabled skills fail visibility even with matching files',
              all(r['status'] == 'MATCH' and r['discovery'] == 'NOT_VISIBLE' for r in hidden['copies']))
        write(bin_dir / 'codex', '#!' + sys.executable + '\nimport sys\nsys.exit(1)\n', executable=True)
        failed = cli('doctor', '--native', '--target', 'codex', expected=2)
        check('native probe failure remains UNVERIFIED rather than a successful file check',
              failed['status'] == 'UNVERIFIED' and all(r['discovery'] == 'UNVERIFIED' for r in failed['copies']))
        check('read-only discovery leaves legacy bytes and managed package count unchanged',
              hashlib.sha256(legacy.read_bytes()).hexdigest() == legacy_hash and failed['canonical_packages'] == 2)
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
    report.update(assertions=assertions, assertion_count=len(assertions), commands=commands)
    (output / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'status': report['status'], 'assertions': len(assertions), 'report': str(output / 'report.json'),
                      'error': report.get('error')}, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
