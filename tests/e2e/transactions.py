#!/usr/bin/env python3
"""CLI failure scenarios for acceptance and sync, using synthetic data only.

Failure modes: metadata writes fail after a package switch; a process exits after
partial metadata publication; recovery encounters subsequent edits or exits;
invalid branch-only requests must preserve both the index and Git history.
Python audit hooks inject filesystem errors/exits at the CLI process boundary.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import traceback


ROOT = Path(__file__).resolve().parents[2]
BODY = '---\nname: demo\ndescription: Synthetic transaction fixture.\n---\n\nBase contents.\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scanner', required=True)
    args = parser.parse_args()
    scanner = str(Path(args.scanner).resolve())
    output = ROOT / 'artifacts/e2e/transactions' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
    output.mkdir(parents=True)
    commands, checks, failures, skipped = [], [], [], []

    with tempfile.TemporaryDirectory(prefix='aak-transactions-') as temporary:
        base = Path(temporary).resolve()
        fault_cli = base / 'fault_cli.py'
        fault_cli.write_text('''import errno, json, os, runpy, sys
from pathlib import Path
destination = Path(os.environ['AAK_E2E_FAULT_PATH']).resolve()
fired = False
def fault(event, arguments):
    global fired
    if event != 'os.rename' or fired:
        return
    target = Path(os.fsdecode(arguments[1])).resolve()
    mode = os.environ['AAK_E2E_FAULT_MODE']
    completing = mode == 'complete-error' and target.parent == destination and json.loads(Path(arguments[0]).read_text(encoding='utf-8')).get('state') == 'COMPLETE'
    if target == destination or completing:
        fired = True
        if mode in ('error', 'complete-error'):
            raise OSError(errno.ENOSPC, 'synthetic metadata write failure')
        os._exit(73)
if os.environ['AAK_E2E_FAULT_MODE'] == 'size':
    import resource, signal
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (2048, 2048))
else:
    sys.addaudithook(fault)
runpy.run_module('agent_asset_kit', run_name='__main__')
''', encoding='utf-8')

        def run(argv, cwd=base, expected=0, env=None):
            p = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, capture_output=True,
                               text=True, encoding='utf-8', timeout=90)
            commands.append({'argv': list(map(str, argv)), 'exit': p.returncode,
                             'stdout': p.stdout, 'stderr': p.stderr})
            assert p.returncode == expected, f'{argv[:4]}: {p.returncode}, expected {expected}: {p.stdout[:600]} {p.stderr[:300]}'
            return p.stdout

        def cli(lib, *argv, expected=0, fault=None, env=None):
            entry = ['aak']
            if fault:
                mode, path = fault
                env = dict(os.environ, AAK_E2E_FAULT_MODE=mode, AAK_E2E_FAULT_PATH=str(path))
                entry = [sys.executable, fault_cli]
            result = run([*entry, '--root', lib, *argv], lib, expected, env)
            return json.loads(result) if result.strip() else None

        def write(path, text):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')

        def read(path):
            return json.loads(path.read_text(encoding='utf-8'))

        def check(name, condition):
            assert condition, name
            checks.append(name)

        def library(name):
            lib = base / name
            run(['aak', 'init', lib, '--git'])
            cli(lib, 'configure', '--home', base / (name + '-home'), '--agents', 'codex', '--gitleaks', scanner)
            run(['git', 'config', 'user.name', 'Synthetic transactions'], lib)
            run(['git', 'config', 'user.email', 'fixture@example.invalid'], lib)
            return lib

        def native(lib):
            return Path(read(lib / '.local/config.json')['agents']['codex']['skills_root']) / 'demo/SKILL.md'

        def collect(lib, body=BODY):
            write(native(lib), body)
            return cli(lib, 'collect')['candidates'][0]['id']

        def accept(lib, candidate, *argv, **kwargs):
            return cli(lib, 'accept', candidate, '--agent', 'codex', '--kind', 'custom', '--reviewed', *argv, **kwargs)

        def metadata(lib):
            return {name: (lib / name).read_bytes() if (lib / name).exists() else None for name in
                    ('skills/manifest.json', 'skills/releases.json', 'skills/decisions.json', '.local/accepted-copies.json')}

        def receipts(lib):
            return sorted((lib / '.local/acceptances').glob('*.json'), key=lambda p: p.stat().st_mtime_ns)

        def failed_new_acceptance():
            lib = library('new-acceptance')
            candidate = collect(lib)
            (lib / 'skills/releases.json').unlink()
            before = metadata(lib)
            accept(lib, candidate, '--trial', expected=2, fault=('error', lib / 'skills/manifest.json'))
            check('failed new acceptance restores every metadata file and leaves no canonical orphan',
                  metadata(lib) == before and not (lib / 'skills/custom/demo').exists())
            check('failed acceptance retains its native source, candidate and rollback receipt',
                  native(lib).read_text(encoding='utf-8') == BODY and
                  (lib / '.local/candidates' / candidate / 'package/SKILL.md').is_file() and
                  read(receipts(lib)[-1])['state'] == 'ROLLED_BACK')
            accepted = accept(lib, candidate, '--trial')
            check('the exact same candidate succeeds after a failed write',
                  read(lib / 'skills/manifest.json')['packages'][accepted['skill_id']]['lifecycle'] == 'reviewed')

        def failed_existing_acceptance():
            lib = library('existing-acceptance')
            accepted = accept(lib, collect(lib))
            cli(lib, 'sync', '--agent', 'codex', '--message', 'Synthetic baseline', '--local-only')
            cli(lib, 'collect')
            before = metadata(lib)
            candidate = collect(lib, BODY + 'Incoming native edit.\n')
            accept(lib, candidate, '--trial', expected=2, fault=('error', lib / 'skills/manifest.json'))
            canonical = lib / accepted['path'] / 'SKILL.md'
            check('failed replacement restores the previous package, stable release and catalog bytes',
                  canonical.read_text(encoding='utf-8') == BODY and metadata(lib) == before)
            check('rollback preserves incoming native edits and the rejected prepared copy',
                  'Incoming native edit.' in native(lib).read_text(encoding='utf-8') and
                  any('Incoming native edit.' in p.read_text(encoding='utf-8')
                      for p in (lib / 'skills/custom/.asset-backups').glob('*/SKILL.md')))
            retried = accept(lib, candidate, '--trial')
            check('retry keeps the existing stable skill identity', retried['skill_id'] == accepted['skill_id'])

        def bounded_preparation():
            if os.name == 'nt':
                skipped.append('OS file-size limit is POSIX-specific; write failure/exit scenarios still run'); return
            lib = library('bounded-preparation')
            write(native(lib), BODY)
            for index in range(100):
                write(native(lib).parent / f'synthetic-resource-{index:03d}.txt', 'Synthetic data.\n')
            candidate = cli(lib, 'collect')['candidates'][0]['id']
            before = metadata(lib)
            accept(lib, candidate, expected=2, fault=('size', lib / 'skills/manifest.json'))
            check('OS file-size failure during preparation cannot orphan a canonical directory or change metadata',
                  metadata(lib) == before and not (lib / 'skills/custom/demo').exists() and
                  not cli(lib, 'status')['pending_acceptances'])
            accepted = accept(lib, candidate)
            check('a candidate whose preparation failed can be retried without manual cleanup',
                  read(lib / 'skills/manifest.json')['packages'][accepted['skill_id']]['files'] == 101)

        def reconciled_acceptance():
            lib = library('reconciled-acceptance')
            accepted = accept(lib, collect(lib))
            cli(lib, 'sync', '--agent', 'codex', '--message', 'Synthetic merge baseline', '--local-only')
            cli(lib, 'collect')
            candidate = collect(lib, BODY + 'Native detail.\n')
            canonical = lib / accepted['path'] / 'SKILL.md'
            write(canonical, BODY + 'Repository detail.\n')
            cli(lib, 'revision', '--skill-id', accepted['skill_id'], '--agent', 'codex', '--reviewed')
            proposed = cli(lib, 'merge', '--candidate', candidate)
            write(Path(proposed['merged_package']) / 'SKILL.md', BODY + 'Repository detail.\nNative detail.\n')
            merged = cli(lib, 'merge', '--finalize', proposed['merge_id'], '--reviewed')['candidate']
            before = metadata(lib)
            old_package = canonical.read_bytes()
            accept(lib, merged, expected=2, fault=('complete-error', lib / '.local/acceptances'))
            check('failure of the final receipt rolls back canonical content and every published metadata file',
                  metadata(lib) == before and canonical.read_bytes() == old_package)
            check('rolled-back reconciliation cannot leave a new native replacement approval behind',
                  not (lib / '.local/accepted-copies.json').exists() and
                  read(receipts(lib)[-1])['state'] == 'ROLLED_BACK')
            accept(lib, merged)
            approvals = read(lib / '.local/accepted-copies.json')
            check('retry of a reconciled candidate publishes its exact native replacement approval',
                  approvals['codex:demo']['candidate'] == merged and 'Native detail.' in canonical.read_text(encoding='utf-8'))

        def interrupted_acceptance():
            lib = library('interrupted-acceptance')
            upstream = base / 'upstream'
            write(upstream / 'SKILL.md', BODY)
            write(upstream / 'LICENSE', 'MIT License\nCopyright Synthetic Fixture\n')
            run(['git', 'init', '-b', 'main'], upstream)
            run(['git', 'add', '.'], upstream)
            run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Synthetic source'], upstream)
            candidate = cli(lib, 'fetch', upstream, '--local-source')['candidate']
            cli(lib, 'evaluate', candidate, '--decision', 'adopt', '--reason', 'Synthetic transaction test',
                '--use-case', 'Exercise metadata recovery', '--agent', 'codex', '--reviewed')
            before = metadata(lib)
            cli(lib, 'accept', candidate, '--agent', 'codex', '--reviewed', '--trial',
                expected=73, fault=('exit', lib / 'skills/decisions.json'))
            receipt = receipts(lib)[-1]
            ident = receipt.stem
            check('an interrupted acceptance exposes its owning workspace and ID',
                  any(r['id'] == ident and r['root'] == str(lib.resolve())
                      for r in cli(lib, 'status')['pending_acceptances']))
            index = run(['git', 'write-tree'], lib)
            cli(lib, 'sync', '--agent', 'codex', '--message', 'Must not publish partial acceptance', '--local-only', expected=2)
            cli(lib, 'deploy', expected=2)
            check('unfinished acceptance blocks sync and deployment before index changes', run(['git', 'write-tree'], lib) == index)
            recover = ['accept', '--recover', ident, '--agent', 'codex']
            cli(lib, *recover, expected=2)
            cli(lib, *recover, '--reviewed', candidate, expected=2)
            canonical = lib / 'skills/third-party/demo/SKILL.md'
            original = canonical.read_bytes()
            after_crash = metadata(lib)
            canonical.write_bytes(original + b'Concurrent canonical edit.\n')
            cli(lib, *recover, '--reviewed', expected=2)
            check('recovery refuses later package edits without changing metadata',
                  canonical.read_bytes().endswith(b'Concurrent canonical edit.\n') and metadata(lib) == after_crash)
            canonical.write_bytes(original)
            decisions = lib / 'skills/decisions.json'
            write(decisions, '{"version":1,"entries":[{"reason":"Concurrent metadata edit"}]}\n')
            changed = metadata(lib)
            cli(lib, *recover, '--reviewed', expected=2)
            check('recovery checks every metadata file before moving the package',
                  metadata(lib) == changed and canonical.read_bytes() == original)
            decisions.write_bytes(after_crash['skills/decisions.json'])
            cli(lib, *recover, '--reviewed', expected=73, fault=('exit', lib / 'skills/manifest.json'))
            result = cli(lib, *recover, '--reviewed')
            check('recovery can resume after its own process interruption and restore exact previous metadata',
                  result['state'] == 'ROLLED_BACK' and metadata(lib) == before and not canonical.exists())
            check('explicit recovery retains the prepared package and releases the workspace',
                  any(p.read_bytes() == original for p in (lib / 'skills/third-party/.asset-backups').glob('*/SKILL.md'))
                  and not cli(lib, 'status')['pending_acceptances'])
            accepted = cli(lib, 'accept', candidate, '--agent', 'codex', '--reviewed', '--trial')
            complete = metadata(lib)
            cli(lib, *recover, '--reviewed')
            check('repeated recovery cannot undo a later successful acceptance', metadata(lib) == complete)
            check('successful retry records the external decision exactly once',
                  len(read(lib / 'skills/decisions.json')['entries']) == 1 and
                  accepted['skill_id'] in read(lib / 'skills/manifest.json')['packages'])

        def worktree_recovery():
            lib = library('acceptance-worktree')
            cli(lib, 'sync', '--agent', 'codex', '--message', 'Synthetic worktree baseline', '--local-only')
            workspace = Path(cli(lib, 'workspace', '--agent', 'codex')['workspace'])
            write(native(lib), BODY)
            candidate = cli(workspace, 'collect')['candidates'][0]['id']
            before, primary_before = metadata(workspace), metadata(lib)
            accept(workspace, candidate, '--trial', expected=73, fault=('exit', workspace / 'skills/manifest.json'))
            ident = receipts(lib)[-1].stem
            recover = ['accept', '--recover', ident, '--agent', 'codex', '--reviewed']
            cli(lib, *recover, expected=2)
            cli(lib, 'sync', '--agent', 'codex', '--message', 'Wrong workspace', '--local-only', expected=2)
            check('shared local receipts block other workspaces and require the original recovery root',
                  metadata(lib) == primary_before and (workspace / 'skills/custom/demo/SKILL.md').is_file() and
                  cli(lib, 'status')['pending_acceptances'][0]['root'] == str(workspace))
            cli(workspace, *recover)
            check('worktree recovery restores only its own canonical content and shared local metadata',
                  metadata(workspace) == before and metadata(lib) == primary_before and
                  not (workspace / 'skills/custom/demo').exists() and not cli(lib, 'status')['pending_acceptances'])

        def branch_only_preflight():
            lib = library('branch-only')
            config = read(lib / '.local/config.json')
            config.update(publication_enabled=True, github_repository='example/synthetic-library')
            write(lib / '.local/config.json', json.dumps(config))
            run(['git', 'remote', 'add', 'origin', 'https://github.com/example/synthetic-library.git'], lib)
            fake = base / 'fake-gh'
            fake.mkdir()
            calls = base / 'gh-calls'
            gh = fake / ('gh.cmd' if os.name == 'nt' else 'gh')
            gh_script = fake / 'gh_stub.py'
            write(gh_script, 'from pathlib import Path\nPath(' + repr(str(calls)) + ').write_text("called")\nprint("true")\n')
            write(gh, ('@"' + sys.executable + '" "' + str(gh_script) + '" %*\r\n') if os.name == 'nt' else
                  '#!' + sys.executable + '\nexec(' + repr(gh_script.read_text(encoding='utf-8')) + ')\n')
            gh.chmod(0o755)
            env = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ.get('PATH', ''))
            index = run(['git', 'write-tree'], lib)
            result = cli(lib, 'sync', '--agent', 'codex', '--message', 'Invalid primary mode', '--branch-only', expected=2, env=env)
            run(['git', 'rev-parse', '--verify', 'HEAD'], lib, expected=128)
            check('branch-only on primary stops before authentication, staging or initial commit',
                  '--branch-only needs' in result['reason'] and not calls.exists() and run(['git', 'write-tree'], lib) == index)
            cli(lib, 'sync', '--agent', 'codex', '--message', 'Synthetic initial commit', '--local-only')
            workspace = base / 'unmanaged-branch'
            run(['git', 'worktree', 'add', '-b', 'topic/synthetic', workspace], lib)
            write(workspace / 'README.md', '# Synthetic unpublished edit\n')
            head = run(['git', 'rev-parse', 'HEAD'], workspace)
            index = run(['git', 'write-tree'], workspace)
            result = cli(workspace, 'sync', '--agent', 'codex', '--message', 'Invalid branch name', '--branch-only', expected=2, env=env)
            check('branch-only on an unmanaged worktree branch preserves both checkouts and index',
                  '--branch-only needs' in result['reason'] and not calls.exists() and
                  run(['git', 'rev-parse', 'HEAD'], workspace) == head and run(['git', 'rev-parse', 'HEAD'], lib) == head and
                  run(['git', 'write-tree'], workspace) == index and (workspace / 'README.md').read_text(encoding='utf-8') == '# Synthetic unpublished edit\n')

        for scenario in (failed_new_acceptance, failed_existing_acceptance, bounded_preparation,
                         reconciled_acceptance, interrupted_acceptance, worktree_recovery, branch_only_preflight):
            try:
                scenario()
            except Exception as error:
                failures.append({'scenario': scenario.__name__, 'error': str(error), 'traceback': traceback.format_exc()})

    report = {'status': 'FAIL' if failures else 'PASS', 'environment': platform.platform(), 'python': sys.version,
              'scope': 'real CLI/files/Git/scanner with synthetic sources and injected filesystem failures/process exits; no native models or network',
              'assertions': len(checks), 'checks': checks, 'failures': failures, 'skipped': skipped}
    (output / 'commands.json').write_text(json.dumps(commands, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'assertions': len(checks),
                      'failures': [f['scenario'] for f in failures], 'report': str(output / 'report.json')}))
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
