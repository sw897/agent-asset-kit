#!/usr/bin/env python3
"""Real CLI regression scenarios for library ownership, privacy and backflow."""

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scanner', required=True)
    args = parser.parse_args()
    scanner = str(Path(args.scanner).resolve())
    out = ROOT / 'artifacts/e2e/boundaries' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
    out.mkdir(parents=True)
    checks, failures, skipped, commands = [], [], [], []
    entry = ['aak']

    def run(argv, cwd, expected=0, env=None):
        p = subprocess.run([str(a) for a in argv], cwd=cwd, env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
        commands.append({'argv': [str(a) for a in argv], 'exit': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
        assert p.returncode == expected, f'exit {p.returncode}, expected {expected}: {p.stdout[:500]} {p.stderr[:200]}'
        return p.stdout

    def cli(lib, *argv, expected=0, env=None):
        return json.loads(run(entry + ['--root', str(lib), *map(str, argv)], lib, expected, env))

    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def read(path):
        return json.loads(path.read_text(encoding='utf-8'))

    def change_config(lib, **updates):
        path = lib / '.local/config.json'
        value = read(path); value.update(updates)
        write(path, json.dumps(value))

    def head(lib):
        return run(['git', 'rev-parse', 'HEAD'], lib).strip()

    def commit(lib):
        return cli(lib, 'sync', '--agent', 'codex', '--message', 'Synthetic reviewed change', '--local-only')['head']

    def library(base, name):
        lib = base / name
        run(entry + ['init', str(lib), '--git'], base)
        cli(lib, 'configure', '--home', base / (name + '-home'), '--agents', 'codex', '--gitleaks', scanner)
        run(['git', 'config', 'user.name', 'Synthetic boundary review'], lib)
        run(['git', 'config', 'user.email', 'fixture@example.invalid'], lib)
        return lib

    def skill(lib):
        path = lib / 'skills/custom/demo/SKILL.md'
        write(path, '---\nname: demo\ndescription: Synthetic boundary fixture.\n---\n\nBase contents.\n')
        ident = cli(lib, 'register', '--path', 'skills/custom/demo', '--targets', 'codex', '--agent', 'codex', '--reviewed')['skill_id']
        return ident, path

    def apply(lib):
        plan = cli(lib, 'deploy')
        return cli(lib, 'deploy', '--apply', '--plan', plan['plan_id'])

    def check(name, condition):
        assert condition, name
        checks.append(name)

    with tempfile.TemporaryDirectory(prefix='aak-boundaries-') as temporary:
        base = Path(temporary)

        def ordinary_root():
            path = base / 'ordinary'; path.mkdir()
            cli(path, 'configure', '--home', base / 'unused-home', '--agents', 'codex', expected=2)
            check('ordinary explicit roots acquire no asset state', not (path / '.local').exists())

        def foreign_primary():
            parent, child = base / 'foreign-parent', base / 'foreign-worktree'
            parent.mkdir()
            run(['git', 'init', '-b', 'main'], parent)
            run(['git', 'config', 'user.name', 'Synthetic foreign repo'], parent)
            run(['git', 'config', 'user.email', 'fixture@example.invalid'], parent)
            run(['git', 'commit', '--allow-empty', '-m', 'Synthetic base'], parent)
            run(['git', 'worktree', 'add', '-b', 'fixture', str(child)], parent)
            write(child / '.agent-asset-kit.json', '{"tool":"AgentAssetKit","format_version":1}\n')
            cli(child, 'configure', '--home', base / 'foreign-home', '--agents', 'codex', expected=2)
            check('foreign primary is not adopted through a marked worktree', not (parent / '.local').exists())

        def linked_paths():
            if os.name == 'nt':
                skipped.append('directory-link regression needs symlink creation permission'); return
            lib = library(base, 'linked-library')
            outside = base / 'outside-package'; outside.mkdir()
            write(outside / 'demo/SKILL.md', '---\nname: demo\ndescription: Synthetic linked entry.\n---\n')
            parent = lib / 'skills/custom'; (parent / '.gitkeep').unlink(); parent.rmdir()
            parent.symlink_to(outside, target_is_directory=True)
            cli(lib, 'register', '--path', 'skills/custom/demo', '--agent', 'codex', '--reviewed', expected=2)
            check('canonical parent links are rejected before registration', not read(lib / 'skills/manifest.json')['packages'])
            parent.unlink(); parent.mkdir()
            destination = base / 'outside-memory'; destination.mkdir()
            shared = lib / 'memory/shared'; (shared / '.gitkeep').unlink(); shared.rmdir()
            shared.symlink_to(destination, target_is_directory=True)
            body = base / 'note.txt'; write(body, 'Synthetic confirmed preference.\n')
            cli(lib, 'note', '--scope', 'global', '--agent', 'codex', '--title', 'fixture', '--source', 'synthetic', '--file', body, expected=2)
            check('shared-memory parent links preserve the external directory', not list(destination.iterdir()))

        def canonical_edit():
            lib = library(base, 'backflow')
            native = Path(read(lib / '.local/config.json')['agents']['codex']['skills_root']) / 'demo/SKILL.md'
            write(native, '---\nname: demo\ndescription: Synthetic backflow.\n---\n\nBase.\n')
            candidate = cli(lib, 'collect')['candidates'][0]['id']
            accepted = cli(lib, 'accept', candidate, '--kind', 'custom', '--agent', 'codex', '--reviewed')
            commit(lib); apply(lib)
            write(native, native.read_text() + 'Native edit.\n')
            incoming = cli(lib, 'collect')['candidates'][0]['id']
            canonical = lib / accepted['path'] / 'SKILL.md'
            write(canonical, canonical.read_text() + 'Unrecorded canonical edit.\n')
            before = canonical.read_bytes()
            cli(lib, 'accept', incoming, '--agent', 'codex', '--reviewed', expected=2)
            check('native acceptance preserves unrecorded canonical edits', canonical.read_bytes() == before and 'Native edit.' in native.read_text())

        def deployment_scan():
            lib = library(base, 'deploy-scan'); skill(lib); commit(lib)
            change_config(lib, gitleaks=str(base / 'missing-scanner'))
            plan = cli(lib, 'deploy')
            cli(lib, 'deploy', '--apply', '--plan', plan['plan_id'], expected=2)
            native = Path(read(lib / '.local/config.json')['agents']['codex']['skills_root']) / 'demo'
            check('stable deployment requires a successful scan before any native switch', not native.exists())
            check('failed preparation retains no staged package', not list((native.parent.parent / '.agent-asset-kit-staging').glob('package-*')))

        def unfinished_trials():
            lib = library(base, 'trials'); ident, source = skill(lib); commit(lib); apply(lib)
            write(source, source.read_text() + 'Trial revision.\n')
            cli(lib, 'revision', '--skill-id', ident, '--agent', 'codex', '--trial', '--reviewed'); commit(lib)
            argv = ['trial', 'start', '--skill-id', ident, '--target', 'codex', '--agent', 'codex', '--reviewed']
            preview = cli(lib, *argv)
            trial = cli(lib, *argv, '--apply', '--plan', preview['plan_id'])
            path = lib / '.local/trials' / trial['trial_id'] / 'trial.json'
            receipt = read(path)
            native = Path(read(lib / '.local/config.json')['agents']['codex']['skills_root']) / 'demo/SKILL.md'
            before = native.read_bytes()
            for state in ['preparing', 'rolling_back']:
                receipt['state'] = state; write(path, json.dumps(receipt))
                plan = cli(lib, 'deploy')
                check(state + ' trial retains target ownership', not plan['actions'] and bool(plan['skipped']))
                cli(lib, 'deploy', '--apply', '--plan', plan['plan_id'])
                check(state + ' trial survives ordinary deployment', native.read_bytes() == before)
                cli(lib, *argv, expected=2)
                check(state + ' trial blocks a second owner', read(path)['state'] == state)

        def manifest_contracts():
            lib = library(base, 'manifest'); ident, _ = skill(lib); commit(lib)
            path = lib / 'skills/manifest.json'; original = read(path); before = head(lib)
            manifest = read(path); manifest['packages'][ident]['bindings'][0]['instance'] = 'local-fixture-alias'
            write(path, json.dumps(manifest))
            cli(lib, 'sync', '--agent', 'codex', '--message', 'Invalid local alias', '--local-only', expected=2)
            check('local instance aliases never enter portable manifests', head(lib) == before)
            for key, value in [('file_modes', ['invalid']), ('requirements', []), ('dependencies', {})]:
                invalid = json.loads(json.dumps(original))
                invalid['packages'][ident][key] = value
                write(path, json.dumps(invalid))
                cli(lib, 'catalog', expected=2)
                check('invalid ' + key + ' is rejected without mutating Git', head(lib) == before)
            write(path, json.dumps(original))

        def sensitive_files():
            for index, name in enumerate(['.env.production', 'native.sqlite3']):
                lib = library(base, 'sensitive-' + str(index))
                native = Path(read(lib / '.local/config.json')['agents']['codex']['skills_root']) / 'demo'
                write(native / 'SKILL.md', '---\nname: demo\ndescription: Synthetic file policy.\n---\n')
                write(native / name, 'Synthetic private configuration without a recognizable token.\n')
                capture = cli(lib, 'collect', expected=2)
                check(name + ' is rejected before becoming a candidate', capture['blocked'] and not capture['candidates'])

        def invalid_entry():
            lib = library(base, 'entrypoint')
            path = lib / 'skills/custom/demo/SKILL.md'; write(path, '# Missing entry fields\n')
            cli(lib, 'register', '--path', 'skills/custom/demo', '--agent', 'codex', '--reviewed', expected=2)
            check('direct registration enforces entry fields', not read(lib / 'skills/manifest.json')['packages'])

        def sync_race():
            if os.name == 'nt':
                skipped.append('scanner process-hook regression is POSIX-specific'); return
            lib = library(base, 'sync-race'); commit(lib)
            workspace = Path(cli(lib, 'workspace', '--agent', 'codex')['workspace'])
            write(workspace / 'README.md', '# Reviewed fixture change\n')
            unexpected = workspace / 'memory/shared/appeared-during-scan.md'
            wrapper = lib / '.local/scanner-wrapper'
            write(wrapper, '#!' + sys.executable + '\nimport subprocess,sys\nfrom pathlib import Path\np=subprocess.run([' + repr(scanner) + ',*sys.argv[1:]])\nPath(' + repr(str(unexpected)) + ').write_text("Synthetic concurrent fact.\\n")\nsys.exit(p.returncode)\n')
            wrapper.chmod(0o755); change_config(lib, gitleaks=str(wrapper))
            before = head(lib)
            cli(workspace, 'sync', '--agent', 'codex', '--message', 'Do not partially commit', '--local-only', expected=2)
            check('new files during scanning stop before commit and merge', head(workspace) == before and head(lib) == before and unexpected.exists())

        def worktree_remote():
            if os.name == 'nt':
                skipped.append('gh transport trap is POSIX-specific'); return
            lib = library(base, 'remote-policy'); commit(lib)
            run(['git', 'config', 'extensions.worktreeConfig', 'true'], lib)
            run(['git', 'remote', 'add', 'origin', 'https://github.com/example/asset-library.git'], lib)
            workspace = Path(cli(lib, 'workspace', '--agent', 'codex')['workspace'])
            run(['git', 'config', '--worktree', 'remote.origin.url', 'https://github.com/example/unrelated.git'], workspace)
            change_config(lib, publication_enabled=True, github_repository='example/asset-library')
            trap = base / 'gh-was-called'; fake = base / 'fake-bin/gh'
            write(fake, '#!' + sys.executable + '\nfrom pathlib import Path\nPath(' + repr(str(trap)) + ').write_text("Forbidden transport call")\nraise SystemExit(1)\n'); fake.chmod(0o755)
            env = dict(os.environ, PATH=str(fake.parent) + os.pathsep + os.environ.get('PATH', ''))
            result = cli(workspace, 'sync', '--agent', 'codex', '--message', 'Validate actual transport', '--branch-only', expected=2, env=env)
            check('worktree remote overrides stop before gh or push', 'origin' in result['reason'] and not trap.exists())

        def resolved_target():
            if os.name == 'nt':
                skipped.append('physical target redirection needs directory symlinks'); return
            lib = library(base, 'physical-target'); skill(lib); commit(lib)
            native = Path(read(lib / '.local/config.json')['agents']['codex']['skills_root'])
            first, second = base / 'profile-a', base / 'profile-b'
            first.mkdir(); second.mkdir(); native.parent.parent.mkdir(parents=True)
            native.parent.symlink_to(first, target_is_directory=True)
            plan = cli(lib, 'deploy')
            native.parent.unlink(); native.parent.symlink_to(second, target_is_directory=True)
            cli(lib, 'deploy', '--apply', '--plan', plan['plan_id'], expected=2)
            check('redirected physical targets invalidate a frozen plan', not (second / 'skills/demo').exists())

        def state_links():
            if os.name == 'nt':
                skipped.append('local state path regression needs directory symlinks'); return
            lib = library(base, 'state-links')
            outside = base / 'outside-state'; outside.mkdir()
            (lib / '.local/candidates').symlink_to(outside, target_is_directory=True)
            native = Path(read(lib / '.local/config.json')['agents']['codex']['skills_root']) / 'demo/SKILL.md'
            write(native, '---\nname: demo\ndescription: Synthetic state boundary.\n---\n')
            cli(lib, 'collect', expected=2)
            check('candidate links cannot move snapshots outside local state', not list(outside.iterdir()))

        def target_during_scan():
            if os.name == 'nt':
                skipped.append('preparation redirection needs POSIX directory links'); return
            lib = library(base, 'target-during-scan'); skill(lib); commit(lib)
            native = Path(read(lib / '.local/config.json')['agents']['codex']['skills_root'])
            first, second = base / 'scan-profile-a', base / 'scan-profile-b'
            first.mkdir(); second.mkdir(); native.parent.parent.mkdir(parents=True)
            native.parent.symlink_to(first, target_is_directory=True)
            wrapper = lib / '.local/redirecting-scanner'
            write(wrapper, '#!' + sys.executable + '\nimport subprocess,sys\nfrom pathlib import Path\np=subprocess.run([' + repr(scanner) + ',*sys.argv[1:]])\nlink=Path(' + repr(str(native.parent)) + ')\nlink.unlink()\nlink.symlink_to(' + repr(str(second)) + ',target_is_directory=True)\nsys.exit(p.returncode)\n')
            wrapper.chmod(0o755); change_config(lib, gitleaks=str(wrapper))
            plan = cli(lib, 'deploy')
            cli(lib, 'deploy', '--apply', '--plan', plan['plan_id'], expected=2)
            check('target redirection during preparation preserves the new location', not (second / 'skills').exists())
            check('physical staging is cleaned after a redirected parent', not list((first / '.agent-asset-kit-staging').glob('package-*')))

        def names_and_revision():
            lib = library(base, 'names')
            path = lib / 'skills/custom/demo/SKILL.md'
            body = '---\nname: demo\ndescription: Synthetic name fixture.\n---\n'
            write(path, body)
            ident = cli(lib, 'register', '--path', 'skills/custom/demo', '--targets', 'codex,codex', '--agent', 'codex', '--reviewed')['skill_id']
            check('repeated target arguments do not create duplicate bindings', len(read(lib / 'skills/manifest.json')['packages'][ident]['bindings']) == 1)
            write(lib / 'skills/third-party/demo/SKILL.md', body)
            cli(lib, 'register', '--path', 'skills/third-party/demo', '--agent', 'codex', '--reviewed', expected=2)
            check('duplicate names stop before poisoning the manifest', len(read(lib / 'skills/manifest.json')['packages']) == 1)
            original = (lib / 'skills/manifest.json').read_bytes()
            write(path, '# Missing revision entry fields\n')
            cli(lib, 'revision', '--skill-id', ident, '--agent', 'codex', '--reviewed', expected=2)
            check('invalid revision leaves registered metadata intact', (lib / 'skills/manifest.json').read_bytes() == original)

        scenarios = [ordinary_root, foreign_primary, linked_paths, canonical_edit, deployment_scan,
                     unfinished_trials, manifest_contracts, sensitive_files, invalid_entry, sync_race, worktree_remote,
                     resolved_target, state_links, names_and_revision, target_during_scan]
        for scenario in scenarios:
            try:
                scenario()
            except Exception as error:
                failures.append({'scenario': scenario.__name__, 'error': str(error), 'traceback': traceback.format_exc()})
    report = {'status': 'FAIL' if failures else 'PASS', 'environment': platform.platform(), 'python': sys.version,
              'scope': 'synthetic library ownership/privacy/backflow; no real native clients or GitHub transport',
              'assertions': len(checks), 'checks': checks, 'failures': failures, 'skipped': skipped}
    (out / 'commands.json').write_text(json.dumps(commands, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'assertions': len(checks), 'failures': [f['scenario'] for f in failures], 'report': str(out / 'report.json')}))
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
