#!/usr/bin/env python3
"""Home Repo onboarding through the real CLI and synthetic user directories.

Failure modes: initialization depends on the current/install directory; quick
commands create a second hidden library; commands cannot find the selected Home
Repo; setup changes native files or existing content without explicit action.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import shlex
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
    scanner = Path(args.scanner).resolve()
    output = ROOT / 'artifacts/e2e/home-repo' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
    output.mkdir(parents=True)
    checks, commands = [], []
    report = {'status': 'FAIL', 'environment': platform.platform(), 'python': sys.version,
              'scope': 'real CLI/files/Git/scanner; synthetic homes, no real Agent or remote services'}

    with tempfile.TemporaryDirectory(prefix='aak-home-repo-') as temporary:
        base = Path(temporary).resolve()
        home, install, project = (base / name for name in ('synthetic home', 'tool-installation', 'project'))
        for folder in (home, install, project):
            folder.mkdir()
        (install / 'program-sentinel.txt').write_text('Synthetic installed program.\n', encoding='utf-8')
        environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                           PATH=str(scanner.parent) + os.pathsep + os.environ.get('PATH', ''))
        environment.pop('AGENT_ASSET_KIT_ROOT', None)
        default = home / 'agent-home'

        def run(arguments, cwd=project, expected=0):
            p = subprocess.run(list(map(str, arguments)), cwd=cwd, env=environment, capture_output=True,
                               text=True, encoding='utf-8', timeout=90)
            commands.append({'argv': list(map(str, arguments)), 'cwd': str(cwd), 'exit': p.returncode,
                             'stdout': p.stdout, 'stderr': p.stderr})
            assert p.returncode == expected, f'{arguments[:4]}: {p.returncode}, expected {expected}: {p.stdout[:600]} {p.stderr[:300]}'
            return p.stdout

        def cli(*arguments, cwd=project, expected=0):
            return json.loads(run(['aak', *arguments], cwd, expected))

        def check(name, condition):
            assert condition, name
            checks.append(name)

        try:
            stopped = cli('try', ROOT / 'tests/fixtures/native-skill', '--to', 'codex', expected=2)
            check('trying before initialization gives one explicit setup instruction and creates no library',
                  'aak init' in stopped['reason'] and not default.exists() and not (home / '.agent-asset-kit').exists()
                  and not (home / '.codex').exists())
            initialized = cli('init', cwd=install)
            check('bare init creates one visible Home Repo under the user home', initialized['initialized'] == str(default))
            check('initialization is independent of the installation and current project directories',
                  not (install / 'agent-home').exists() and not (project / 'agent-home').exists() and
                  (install / 'program-sentinel.txt').read_text(encoding='utf-8') == 'Synthetic installed program.\n')
            check('Git starts on main without creating a commit or remote', initialized['git_initialized'] and
                  run(['git', '-C', default, 'branch', '--show-current']).strip() == 'main' and
                  not run(['git', '-C', default, 'remote']).strip())
            run(['git', '-C', default, 'rev-parse', '--verify', 'HEAD'], expected=128)
            check('plain init leaves Agent selection and native files for explicit setup',
                  not initialized['configured'] and not (home / '.codex').exists() and not (default / '.local/config.json').exists())
            status = cli('status')
            check('status finds an unconfigured Home Repo from an unrelated project and explains the next step',
                  status['primary'] == str(default) and not status['configured'] and 'configure' in status['next'])
            before = (default / 'USER.md').read_bytes()
            cli('init', '--agents', 'claude', expected=2)
            check('repeat initialization preserves existing content and configuration',
                  (default / 'USER.md').read_bytes() == before and not (default / '.local/config.json').exists())
            configured = cli('configure', '--agents', 'codex')
            config = json.loads((default / '.local/config.json').read_text(encoding='utf-8'))
            check('configuration needs neither cd, root nor an explicit scanner path', configured['enabled_agents'] == ['codex']
                  and Path(config['gitleaks']).resolve() == scanner and not config['publication_enabled'])
            instruction = home / '.codex/AGENTS.md'
            instruction.parent.mkdir()
            instruction.write_text('# Existing synthetic instruction\n', encoding='utf-8')
            preview = cli('attach')
            check('attachment preview from a project preserves the current Agent instructions', len(preview['changes']) == 1
                  and instruction.read_text(encoding='utf-8') == '# Existing synthetic instruction\n')
            attached = cli('attach', '--apply')
            check('explicit attachment includes the Home Repo and retains existing instructions', len(attached['changes']) == 1
                  and str(default) in instruction.read_text(encoding='utf-8')
                  and instruction.read_text(encoding='utf-8').startswith('# Existing synthetic instruction\n'))
            installed = cli('try', ROOT / 'tests/fixtures/native-skill', '--to', 'codex')
            check('the trial uses the same Home Repo and prints a root-free save command', installed['library'] == str(default)
                  and '--root' not in installed['next'] and not (home / '.agent-asset-kit').exists())
            save_command = ([shutil.which('pwsh') or 'powershell', '-NoProfile', '-NonInteractive', '-Command', installed['next']]
                            if os.name == 'nt' else shlex.split(installed['next']))
            saved = json.loads(run(save_command))
            check('save from the project returns edits to the default Home Repo', saved['library'] == str(default)
                  and cli('catalog')['packages'][saved['skill_id']]['name'] == 'fixture-skill')
            run(['git', '-C', default, 'config', 'user.name', 'Synthetic Home Repo'])
            run(['git', '-C', default, 'config', 'user.email', 'fixture@example.invalid'])
            committed = cli('sync', '--agent', 'codex', '--message', 'Synthetic onboarding', '--local-only')
            check('local sync from the project commits only the Home Repo', bool(committed['head']) and not committed['pushed'])

            custom = base / 'custom home repo'
            result = cli('init', custom, '--agents', 'claude')
            custom_config = json.loads((custom / '.local/config.json').read_text(encoding='utf-8'))
            check('init with agents completes local setup in one command without touching native files',
                  result['configured'] and result['enabled_agents'] == ['claude'] and
                  Path(custom_config['gitleaks']).resolve() == scanner and not (home / '.claude').exists())
            nested = custom / 'memory/shared'
            check('the enclosing initialized library takes precedence over the default', cli('status', cwd=nested)['primary'] == str(custom))
            environment['AGENT_ASSET_KIT_ROOT'] = str(custom)
            check('an environment-selected library works from any project', cli('status')['primary'] == str(custom))
            check('explicit root takes precedence over the environment', cli('--root', default, 'status')['primary'] == str(default))
            environment.pop('AGENT_ASSET_KIT_ROOT')
            selected = base / 'selected by root'
            result = cli('--root', selected, 'init', '--agents', 'codex')
            check('init honors an explicitly selected root', result['initialized'] == str(selected) and result['configured'])
            conflict = base / 'ambiguous target'
            cli('--root', default, 'init', conflict, expected=2)
            check('conflicting directory and root selections stop before creating content', not conflict.exists())
            invalid = base / 'invalid agents'
            cli('init', invalid, '--agents', 'unknown-client', expected=2)
            check('invalid initial Agent selection stops before creating a Home Repo', not invalid.exists())
            plain = base / 'files-only repo'
            cli('init', plain, '--no-git')
            check('explicit file-only initialization remains available', not (plain / '.git').exists()
                  and cli('--root', plain, 'status')['git_initialized'] is False)
            legacy = home / '.agent-asset-kit'
            cli('init', legacy, '--no-git')
            check('older marked library locations remain usable through explicit root without changing the default',
                  cli('--root', legacy, 'catalog')['packages'] == {} and cli('status')['primary'] == str(default))

            other_home = base / 'other user'
            ordinary = other_home / 'agent-home'
            ordinary.mkdir(parents=True)
            (ordinary / 'README.md').write_text('Existing ordinary home repo.\n', encoding='utf-8')
            environment.update(HOME=str(other_home), USERPROFILE=str(other_home))
            cli('status', expected=2)
            cli('init', '--agents', 'codex', expected=2)
            check('an existing ordinary default directory is never adopted or modified',
                  (ordinary / 'README.md').read_text(encoding='utf-8') == 'Existing ordinary home repo.\n'
                  and not (ordinary / '.agent-asset-kit.json').exists() and not (ordinary / '.local').exists())
            if os.name != 'nt':
                linked_home = base / 'linked user'
                linked_home.mkdir()
                (linked_home / 'agent-home').symlink_to(default, target_is_directory=True)
                environment.update(HOME=str(linked_home), USERPROFILE=str(linked_home))
                cli('status', expected=2)
                cli('init', expected=2)
                check('a default-directory link is never silently followed', (linked_home / 'agent-home').is_symlink())
            report['status'] = 'PASS'
        except Exception:
            report['error'] = traceback.format_exc()
    report.update(assertions=checks, assertion_count=len(checks), commands=commands)
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'assertions': len(checks), 'error': report.get('error'),
                      'report': str(output / 'report.json')}, ensure_ascii=False))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
