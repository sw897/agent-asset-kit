#!/usr/bin/env python3
"""Exercise library initialization and an installed CLI without real native data."""

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
import traceback

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scanner', required=True)
    args = parser.parse_args()
    output = ROOT / 'artifacts/e2e/bootstrap' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
    output.mkdir(parents=True)
    entry = ['aak']
    checks, commands = [], []
    report = {'status': 'FAIL', 'environment': platform.platform(), 'python': sys.version,
              'entry': 'aak',
              'scanner_sha256': hashlib.sha256(Path(args.scanner).read_bytes()).hexdigest(),
              'scope': 'synthetic local library, no real native clients, providers or GitHub'}

    environment = os.environ.copy()

    def run(argv, cwd, expected=0):
        p = subprocess.run([str(a) for a in argv], cwd=cwd, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=90)
        commands.append({'argv': [str(a) for a in argv], 'exit': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
        assert p.returncode == expected, p.stdout[:600] + p.stderr[:600]
        return p.stdout

    def cli(*argv, cwd, expected=0):
        return json.loads(run(entry + list(argv), cwd, expected))

    def check(name, condition):
        assert condition, name
        checks.append(name)

    try:
        with tempfile.TemporaryDirectory(prefix='aak-bootstrap-') as tmp:
            base = Path(tmp)
            environment.update(HOME=tmp, USERPROFILE=tmp)
            environment.pop('AGENT_ASSET_KIT_ROOT', None)
            library, home = base / 'asset library 示例', base / 'synthetic-home'
            check('CLI version is available outside a library', run(entry + ['--version'], base).strip() == '1.0.0')
            check('CLI help includes init and explicit root', 'init' in run(entry + ['--help'], base) and '--root' in run(entry + ['--help'], base))
            check('guide works before setup and matches the packaged guide source',
                  run(entry + ['guide'], base) == (ROOT / 'src/agent_asset_kit/resources/agent-guide.md').read_text(encoding='utf-8')
                  and not (base / '.agent-asset-kit').exists() and not (base / '.local').exists())
            stopped = cli('catalog', cwd=base, expected=2)
            check('unselected library is rejected without creating local state', stopped['status'] == 'STOPPED' and not (base / '.local').exists())
            result = cli('init', library, '--git', cwd=base)
            manifest = json.loads((library / 'skills/manifest.json').read_text())
            check('init creates an empty library outside the engine', result['packages'] == 0 and manifest['packages'] == {} and not (library / 'src').exists())
            check('packaged hidden files and empty directories survive installation',
                  (library / '.gitignore').is_file() and (library / '.gitattributes').is_file() and
                  (library / 'memory/inbox').is_dir() and (library / 'skills/custom').is_dir())
            check('init does not configure publication or native directories', not home.exists() and not (library / '.local').exists() and not result['remote_configured'])
            before = (library / 'USER.md').read_bytes()
            cli('init', library, cwd=base, expected=2)
            check('repeat init preserves existing files', (library / 'USER.md').read_bytes() == before)
            cli('--root', library, 'configure', '--home', home, '--agents', 'codex,claude', '--gitleaks', args.scanner, cwd=base)
            config = json.loads((library / '.local/config.json').read_text())
            check('configuration has no default repository or publication permission', config['github_repository'] == '' and config['publication_enabled'] is False and not home.exists())
            incompatible = cli('--root', library, 'sync', '--agent', 'codex', '--message', 'Must not commit', '--local-only', '--branch-only', cwd=base, expected=2)
            head = subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], cwd=library, capture_output=True)
            check('incompatible sync modes stop before a commit or staging', '--branch-only' in incompatible['reason'] and head.returncode != 0 and not run(['git', 'diff', '--cached', '--name-only'], library).strip())
            check('empty catalog is usable', cli('--root', library, 'catalog', cwd=base)['packages'] == {})
            child = library / 'memory/shared'
            check('library is discovered from a child directory', cli('catalog', cwd=child)['packages'] == {})
            run(['git', 'config', 'user.name', 'Synthetic Bootstrap'], library)
            run(['git', 'config', 'user.email', 'fixture@example.invalid'], library)
            cli('--root', library, 'sync', '--agent', 'codex', '--message', 'Synthetic initial library', cwd=base, expected=2)
            check('remote publication remains disabled', not run(['git', 'remote'], library).strip())
            local = cli('--root', library, 'sync', '--agent', 'codex', '--message', 'Synthetic initial library', '--local-only', cwd=base)
            check('local-only sync commits without enabling remote publication', bool(local['head']) and not local['pushed'] and json.loads((library / '.local/config.json').read_text())['publication_enabled'] is False)
            check('local configuration is not tracked', not run(['git', 'ls-files', '.local'], library).strip())
            refresh = cli('--root', library, 'refresh', cwd=base)
            check('refresh needs no remote for local use', refresh.get('refreshed') is False)
            workspace = cli('--root', library, 'workspace', '--agent', 'codex', cwd=base)
            status = cli('--root', workspace['workspace'], 'status', cwd=base)
            check('worktree resolves the primary library independently of engine location', status['primary'] == str(library.resolve()) and status['workspace'] == workspace['workspace'])
            check('generated command uses the installed module', 'aak' in workspace['command'] and '--root' in workspace['command'])
            preview = cli('--root', library, 'deploy', cwd=base)
            check('empty deployment is a read-only plan', not preview['actions'] and not home.exists())
            applied = cli('--root', library, 'deploy', '--apply', '--plan', preview['plan_id'], cwd=base)
            check('empty deployment remains harmless', not applied['actions'] and not home.exists())
            instruction = home / '.codex/AGENTS.md'
            instruction.parent.mkdir(parents=True)
            instruction.write_text('# Synthetic native instructions\n', encoding='utf-8')
            original = instruction.read_bytes()
            config['agents']['claude']['instructions'] = str(instruction)
            path = library / '.local/config.json'
            path.write_text(json.dumps(config), encoding='utf-8')
            cli('--root', library, 'attach', '--apply', cwd=base, expected=2)
            check('conflicting instances sharing an instruction path preserve native text', instruction.read_bytes() == original)
            config['agents']['claude']['kind'] = 'codex'
            path.write_text(json.dumps(config), encoding='utf-8')
            attached = cli('--root', library, 'attach', '--apply', cwd=base)
            attached_text = instruction.read_text(encoding='utf-8')
            check('equivalent instances write one instruction block and retain one original backup',
                  len(attached['changes']) == 1 and attached_text.count('<!-- AGENT_ASSET_KIT_START -->') == 1 and
                  'aak guide' in attached_text and str(library) in attached_text and
                  Path(attached['changes'][0]['backup']).read_bytes() == original)
            quick_home = base / 'quick home'
            quick_home.mkdir()
            environment.update(HOME=str(quick_home), USERPROFILE=str(quick_home),
                               PATH=str(Path(args.scanner).resolve().parent) + os.pathsep + environment['PATH'])
            environment.pop('AGENT_ASSET_KIT_ROOT', None)
            upstream = base / 'synthetic-source'
            shutil.copytree(ROOT / 'tests/fixtures/native-skill', upstream / 'demo')
            shutil.copytree(upstream / 'demo', upstream / 'nested/demo')
            run(['git', 'init', '-b', 'main'], upstream)
            run(['git', 'add', '.'], upstream)
            run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-m', 'Synthetic upstream'], upstream)
            url = 'https://github.com/example/quick-skill.git'
            environment.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='url.' + upstream.as_uri() + '.insteadOf',
                               GIT_CONFIG_VALUE_0=url, GIT_ALLOW_PROTOCOL='file')
            source = 'example/quick-skill'
            quick = quick_home / '.agent-asset-kit'
            missing = cli('try', './missing-skill', '--to', 'codex', cwd=base, expected=2)
            check('missing local path produces a local error', 'local skill directory does not exist' in missing['reason'])
            choices = cli('try', source, '--to', 'codex', cwd=base, expected=2)
            first = cli('try', source, '--skill', './demo', '--to', 'codex', cwd=base)
            check('multi-skill error lists an exact directory accepted by the suggested flag',
                  './demo' in choices['reason'] and './nested/demo' in choices['reason'] and first['installed'] == 'fixture-skill')
            check('first save hint needs neither a root nor an Agent flag',
                  '--root' not in first['next'] and '--from' not in first['next'])
            trial = Path(first['target'])
            claude = quick_home / '.claude/skills/fixture-skill'
            def records():
                return json.loads((quick / 'skills/manifest.json').read_text())['packages']
            check('one command installs an independent copy on just one Agent before adoption',
                  trial.is_dir() and not trial.is_symlink() and not records() and not claude.exists())
            pending = cli('--root', quick, 'collect', cwd=base)
            check('background collection leaves adoption of a quick trial to save', not pending['candidates'])
            helper = trial / 'scripts/helper.py'
            helper.write_text('print("local change")\n')
            again = cli('try', source, '--skill', './demo', '--to', 'codex', cwd=base)
            check('retry preserves native edits', again['already_installed'] and 'local change' in helper.read_text())
            preview = cli('save', 'fixture-skill', '--from', 'codex', '--dry-run', cwd=base)
            check('preview reports edited file without adoption', preview['changed_since_install'] == ['scripts/helper.py'] and not records())
            lic = trial / 'LICENSE'
            original_license = lic.read_bytes()
            lic.unlink()
            cli('save', 'fixture-skill', '--from', 'codex', cwd=base, expected=2)
            check('missing upstream license leaves library empty', not records())
            lic.write_bytes(original_license)
            check('default library can be inspected from the original working directory',
                  cli('catalog', cwd=base)['packages'] == {} and cli('agent', 'list', cwd=base)['agents'])
            saved = cli('save', 'fixture-skill', cwd=base)
            item = records()[saved['skill_id']]
            check('save adopts the edited bytes, preserving upstream commit and initial target',
                  (quick / item['path'] / 'scripts/helper.py').read_bytes() == helper.read_bytes() and
                  item['origin']['commit'] == run(['git', 'rev-parse', 'HEAD'], upstream).strip() and
                  item['bindings'] == [{'agent': 'codex', 'path': 'fixture-skill'}] and not claude.exists())
            cli('save', 'fixture-skill', '--from', 'codex', cwd=base)
            cli('share', 'fixture-skill', '--to', 'claude', cwd=base)
            check('share installs saved bytes without a Git commit', (claude / 'scripts/helper.py').read_bytes() == helper.read_bytes()
                  and len(records()[saved['skill_id']]['bindings']) == 2 and not run(['git', 'remote'], quick).strip())
            ambiguous = cli('save', 'fixture-skill', cwd=base, expected=2)
            check('save identifies both sources when the choice is ambiguous',
                  all(s in ambiguous['reason'] for s in ('--from', 'codex', 'claude')))
            cli('agent', 'disable', '--name', 'claude', '--reviewed', cwd=base)
            cli('share', 'fixture-skill', '--to', 'claude', cwd=base, expected=2)
            check('explicitly disabled Agent stays disabled after an explicit share',
                  not json.loads((quick / '.local/config.json').read_text())['agents']['claude']['enabled'])
            cli('agent', 'enable', '--name', 'claude', '--reviewed', cwd=base)
            (claude / 'scripts/helper.py').write_text('shared copy improved')
            cli('save', 'fixture-skill', '--from', 'claude', cwd=base)
            check('edits on the second Agent can return independently', (quick / item['path'] / 'scripts/helper.py').read_text() == 'shared copy improved'
                  and helper.read_text() != 'shared copy improved')
            cli('share', 'fixture-skill', '--to', 'codex', cwd=base)
            (claude / 'scripts/helper.py').write_text('independent edit')
            helper.write_text('different new edit')
            cli('save', 'fixture-skill', '--from', 'codex', cwd=base)
            cli('share', 'fixture-skill', '--to', 'claude', cwd=base, expected=2)
            check('share preserves conflicting edits on the second Agent', (claude / 'scripts/helper.py').read_text() == 'independent edit')
            candidates = cli('--root', quick, 'collect', cwd=base)['candidates']
            incoming = next(c for c in candidates if c['bindings'][0]['agent'] == 'claude')
            merged = cli('--root', quick, 'merge', '--candidate', incoming['id'], cwd=base)
            check('divergent copies retain a common base for merge before the first Git commit',
                  'scripts/helper.py' in merged['conflicts'] and 'shared copy improved' in
                  (Path(merged['merged_package']).parent / 'base/scripts/helper.py').read_text())
            local_source = upstream / 'demo'
            md = local_source / 'SKILL.md'
            md.write_text(md.read_text().replace('fixture-skill', 'local-demo'))
            collision = quick_home / '.codex/skills/local-demo'
            collision.mkdir()
            (collision / 'notes').write_text('keep')
            cli('try', local_source, '--to', 'codex', cwd=base, expected=2)
            check('try retains an unrelated same-name directory', (collision / 'notes').read_text() == 'keep')
            shutil.rmtree(collision)
            cli('try', local_source, '--to', 'codex', cwd=base)
            secret_path = collision / 'config.txt'
            secret_path.write_text('access_token = "ghp_' + hashlib.sha256(b'synthetic quick rejection').hexdigest()[:36] + '"')
            candidate_root = quick / '.local/candidates'
            before = set(candidate_root.iterdir())
            cli('save', 'local-demo', '--from', 'codex', cwd=base, expected=2)
            check('secret rejection preserves the native edit without retaining a candidate or catalog entry',
                  secret_path.is_file() and before == set(candidate_root.iterdir()) and not (quick / 'skills/custom/local-demo').exists())
            secret_path.unlink()
            cli('save', 'local-demo', '--from', 'codex', cwd=base)
            check('local directory trial follows the same path to the custom catalog', (quick / 'skills/custom/local-demo/SKILL.md').is_file())
            # A second device starts with the portable files only.
            second = base / 'second library'
            shutil.copytree(quick, second, ignore=shutil.ignore_patterns('.git', '.local'))
            second_home = base / 'second home'
            second_home.mkdir()
            environment.update(HOME=str(second_home), USERPROFILE=str(second_home))
            received = cli('share', 'local-demo', '--to', 'claude', cwd=second)
            check('first share on another device needs neither configure nor a Git commit',
                  Path(received['target']).resolve() == (second_home / '.claude/skills/local-demo').resolve()
                  and (Path(received['target']) / 'SKILL.md').is_file())
        report.update(status='PASS', checks=checks, assertions=len(checks))
    except Exception as error:
        report.update(error=str(error), traceback=traceback.format_exc(), checks=checks)
    (output / 'commands.json').write_text(json.dumps(commands, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'assertions': len(checks), 'report': str(output / 'report.json')}))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
