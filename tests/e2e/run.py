#!/usr/bin/env python3
"""Exercise real CLI/files/Git with a synthetic home; writes repeatable evidence."""

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
    scanner = Path(args.scanner).resolve()
    ident = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid())
    output = ROOT / 'artifacts/e2e/core' / ident
    repo, home = output / 'repository', output / 'home'
    repo.mkdir(parents=True)
    home.mkdir()
    commands, assertions = [], []
    report = {'result': 'FAIL', 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'os': platform.platform(), 'python': sys.version, 'scenario': 'synthetic-home CLI E2E',
              'real_provider': False, 'remote_github': 'NOT_RUN', 'native_model_loading': 'NOT_RUN',
              'scanner_sha256': hashlib.sha256(scanner.read_bytes()).hexdigest()}

    def execute(argv, cwd=repo, expected=0):
        result = subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, text=True, timeout=60)
        commands.append({'argv': [str(a) for a in argv], 'cwd': str(cwd), 'exit': result.returncode,
                         'stdout': result.stdout, 'stderr': result.stderr})
        assert result.returncode == expected, f'{argv[0:3]} returned {result.returncode}, expected {expected}: {result.stdout[:400]}'
        return result.stdout

    def cli(*argv, cwd=repo, expected=0):
        return json.loads(execute(['aak', '--root', str(cwd), *argv], cwd=cwd, expected=expected))

    def apply_deploy(*args):
        preview = cli('deploy', *args)
        return cli('deploy', '--apply', *args, '--plan', preview['plan_id'])

    def check(name, predicate):
        assert predicate, name
        assertions.append(name)

    def read(path):
        return json.loads(path.read_text())

    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def package_at(path):
        shutil.copytree(ROOT / 'tests/fixtures/native-skill', path)
        (path / 'assets').mkdir()
        (path / 'assets/fixture.bin').write_bytes(bytes(range(256)))
        (path / 'assets/crlf.txt').write_bytes(b'one\r\ntwo\r\n')
        (path / 'scripts/helper.py').chmod(0o755)
        (path / '.gitignore').write_text('assets/fixture.bin\nuv.lock\n')
        (path / 'references').mkdir()
        (path / 'references/internal.md').symlink_to('../SKILL.md')

    try:
        for directory in ('templates', 'agents'):
            shutil.copytree(SEED / directory, repo / directory, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(ROOT / '.gitignore', repo / '.gitignore')
        shutil.copy2(SEED / '.gitattributes', repo / '.gitattributes')
        write(repo / 'README.md', '# Synthetic assets\n')
        (repo / '.agent-asset-kit.json').write_text('{"tool":"AgentAssetKit","format_version":1}\n', encoding='utf-8')
        execute(['git', 'init', '-b', 'main'])
        execute(['git', 'config', 'user.name', 'AgentAssetKit E2E'])
        execute(['git', 'config', 'user.email', 'synthetic@example.invalid'])
        report['git_version'] = execute(['git', '--version']).strip()
        report['gitleaks_version'] = execute([scanner, 'version']).strip()
        legacy = home / 'legacy/demo'
        package_at(legacy)
        write(legacy / '.venv/lib/runtime-state.txt', 'Synthetic local-only runtime\n')
        for agent in ('.codex', '.claude'):
            directory = home / agent / 'skills'
            directory.mkdir(parents=True)
            (directory / 'demo').symlink_to(legacy, target_is_directory=True)
        package_at(home / '.hermes/skills/development/custom')
        package_at(home / '.hermes/skills/category/builtin')
        write(home / '.hermes/skills/.bundled_manifest', 'builtin:synthetic\n')
        package_at(home / '.codex/skills/.system/builtin')
        for name in ('MEMORY.md', 'USER.md'):
            write(home / '.hermes/memories' / name, 'Original native memory ' + name)
        native_memory = {p.name: p.read_bytes() for p in (home / '.hermes/memories').iterdir()}
        instruction_paths = [home / '.codex/AGENTS.md', home / '.claude/CLAUDE.md', home / '.hermes/SOUL.md']
        for path in instruction_paths:
            write(path, '# Existing native identity and rules\n')

        cli('configure', '--home', str(home), '--gitleaks', str(scanner))
        collected = cli('collect')
        check('builtin excluded; two physical user packages captured', len(collected['candidates']) == 2 and
              not collected['blocked'] and 'hermes:category/builtin' in collected['builtin_excluded'])
        check('collection does not change legacy links', (home / '.codex/skills/demo').is_symlink())
        first = collected['candidates'][0]['id']
        cli('accept', first, '--agent', 'codex', '--kind', 'custom', expected=2)
        check('unreviewed candidate cannot enter registry', not (repo / 'skills/manifest.json').exists())
        for item in collected['candidates']:
            cli('accept', item['id'], '--agent', 'codex', '--kind', 'custom', '--reviewed')
        manifest = read(repo / 'skills/manifest.json')
        demo_id, demo = next((i, p) for i, p in manifest['packages'].items() if p['name'] == 'demo')
        check('one canonical asset retains both native bindings', len(demo['bindings']) == 2)
        cli('sync', '--agent', 'codex', '--message', 'unauthorized', expected=2)
        check('disabled publication creates no commit', subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], cwd=repo,
              capture_output=True).returncode != 0)
        config = read(repo / '.local/config.json')
        config['publication_enabled'] = True  # Explicit synthetic authorization, never personal configuration.
        (repo / '.local/config.json').write_text(json.dumps(config))
        cli('sync', '--agent', 'codex', '--message', 'synthetic baseline', '--local-only')
        tracked = execute(['git', 'ls-files']).splitlines()
        check('complete assets override upstream ignores', demo['path'] + '/uv.lock' in tracked and
              demo['path'] + '/assets/fixture.bin' in tracked)
        clone = output / 'clone-check'
        execute(['git', 'clone', '--local', str(repo), str(clone)])
        check('Git clone retains exact binary/CRLF bytes and ignored dependency lock',
              (clone / demo['path'] / 'assets/fixture.bin').read_bytes() == bytes(range(256)) and
              (clone / demo['path'] / 'assets/crlf.txt').read_bytes() == b'one\r\ntwo\r\n' and
              (clone / demo['path'] / 'uv.lock').read_bytes() == (repo / demo['path'] / 'uv.lock').read_bytes())
        preview = cli('deploy')
        check('deployment preview is read-only', len(preview['actions']) == 2 and
              (home / '.codex/skills/demo').is_symlink())
        cli('deploy', '--apply', '--plan', preview['plan_id'], expected=2)
        check('native runtime requires explicit preservation before switching', (home / '.codex/skills/demo').is_symlink())
        applied = apply_deploy('--preserve-runtime')
        check('legacy links replaced with independent ordinary directories',
              all(not (home / a / 'skills/demo').is_symlink() for a in ('.codex', '.claude')))
        check('binary bytes and POSIX execution intent retained',
              (home / '.claude/skills/demo/assets/fixture.bin').read_bytes() == bytes(range(256)) and
              bool((home / '.claude/skills/demo/scripts/helper.py').stat().st_mode & 0o111))
        check('internal file references become ordinary copies',
              not (home / '.claude/skills/demo/references/internal.md').is_symlink() and
              (home / '.claude/skills/demo/references/internal.md').read_bytes() == (legacy / 'SKILL.md').read_bytes())
        journal = read(Path(applied['journal']))
        check('previous native links retained as backups', all(Path(c['backup']).is_symlink() for c in journal['changes']))
        check('repeated deployment is idempotent', not apply_deploy()['actions'])
        check('local runtime preserved in each copy and excluded from Git assets',
              all((home / a / 'skills/demo/.venv/lib/runtime-state.txt').read_text() == 'Synthetic local-only runtime\n'
                  for a in ('.codex', '.claude')) and not (repo / demo['path'] / '.venv').exists())

        classified = read(repo / 'skills/manifest.json')
        classification = {'category': 'engineering', 'tags': ['synthetic'],
                          'support': {'codex': 'current-device', 'claude': 'current-device'}}
        classified['packages'][demo_id].update(classification)
        (repo / 'skills/manifest.json').write_text(json.dumps(classified))
        local_file = home / '.codex/skills/demo/SKILL.md'
        local_file.write_text(local_file.read_text() + '\nCreator revision.\n')
        changed = cli('collect')
        check('native edit creates one candidate without touching canonical', len(changed['candidates']) == 1 and
              'Creator revision' not in (repo / demo['path'] / 'SKILL.md').read_text())
        cli('accept', changed['candidates'][0]['id'], '--agent', 'codex', '--reviewed')
        accepted_metadata = read(repo / 'skills/manifest.json')['packages'][demo_id]
        check('native backflow preserves reviewed classification and target contract',
              all(accepted_metadata.get(k) == v for k, v in classification.items()))
        cli('sync', '--agent', 'codex', '--message', 'native revision', '--local-only')
        apply_deploy('--preserve-runtime')
        check('accepted revision returns to other independent copies',
              'Creator revision' in (home / '.claude/skills/demo/SKILL.md').read_text())

        canonical = repo / demo['path'] / 'SKILL.md'
        base = canonical.read_text()
        canonical.write_text(base + '\nRepository revision.\n')
        cli('revision', '--skill-id', demo_id, '--agent', 'codex', '--reviewed')
        revised_metadata = read(repo / 'skills/manifest.json')['packages'][demo_id]
        check('reviewed revision preserves classification and target contract',
              all(revised_metadata.get(k) == v for k, v in classification.items()))
        other = home / '.claude/skills/demo/SKILL.md'
        other.write_text(base + '\nIndependent native revision.\n')
        cli('deploy', '--apply', '--plan', preview['plan_id'], expected=2)
        check('two-sided conflict preserves both complete versions', 'Repository revision' in canonical.read_text() and
              'Independent native revision' in other.read_text())
        candidate = cli('collect')['candidates'][0]
        cli('accept', candidate['id'], '--agent', 'claude', '--reviewed', expected=2)
        check('conflicted acceptance does not replace canonical', 'Repository revision' in canonical.read_text())
        canonical.write_text(base)
        other.write_text(base)
        cli('revision', '--skill-id', demo_id, '--agent', 'codex', '--reviewed')
        cli('sync', '--agent', 'codex', '--message', 'restored baseline for removal scenario', '--local-only')
        shutil.rmtree(home / '.claude/skills/demo')
        cli('deploy', '--apply', '--plan', preview['plan_id'], expected=2)
        check('missing copy does not delete canonical', canonical.exists())
        custom_id = next(i for i, p in manifest['packages'].items() if p['name'] == 'custom')
        other_copy = home / '.hermes/skills/development/custom/SKILL.md'
        other_before = other_copy.read_bytes()
        cli('enable', '--skill-id', custom_id, '--target', 'claude', '--path', 'custom',
            '--agent', 'codex', '--reviewed')
        cli('sync', '--agent', 'codex', '--message', 'prepare scoped deployment', '--local-only')
        cli('deploy', '--skill-id', custom_id, '--skill-id', 'unknown-id', expected=2)
        scoped = cli('deploy', '--skill-id', custom_id)
        check('scoped preview ignores unrelated missing copies without restoring them',
              len(scoped['actions']) == 1 and scoped['actions'][0]['skill_id'] == custom_id and
              not (home / '.claude/skills/custom').exists() and not (home / '.claude/skills/demo').exists())
        cli('deploy', '--apply', '--skill-id', custom_id, '--plan', scoped['plan_id'])
        check('scoped apply creates selected copy and preserves unrelated missing copy',
              not (home / '.claude/skills/demo').exists() and other_copy.read_bytes() == other_before and
              (home / '.claude/skills/custom/SKILL.md').read_bytes() == other_before and
              not (home / '.claude/skills/custom').is_symlink())
        cli('deploy', '--apply', '--skill-id', demo_id, '--plan', scoped['plan_id'], expected=2)
        check('selected missing copy still requires explicit restoration',
              not (home / '.claude/skills/demo').exists())
        apply_deploy('--restore-missing')
        cli('enable', '--skill-id', demo_id, '--target', 'hermes', '--path', 'category/builtin',
            '--agent', 'codex', '--reviewed', expected=2)
        check('native builtin cannot be claimed by manifest change', (home / '.hermes/skills/category/builtin/SKILL.md').exists())

        project = output / 'project with spaces'
        write(project / 'AGENTS.md', '# Existing project rules\nKeep these rules.\n')
        write(project / 'package.json', '{"scripts":{"build":"synthetic-build"}}')
        old = (project / 'AGENTS.md').read_text()
        plan = cli('init-project', str(project), '--purpose', 'Synthetic project', '--command', 'npm run build')
        check('project initialization only creates a preview', (project / 'AGENTS.md').read_text() == old)
        cli('apply-project', plan['plan'], expected=2)
        (project / 'AGENTS.md').write_text(old + 'Concurrent edit.\n')
        cli('apply-project', plan['plan'], '--reviewed', expected=2)
        fresh = cli('init-project', str(project), '--purpose', 'Synthetic project', '--command', 'npm run build')
        cli('apply-project', fresh['plan'], '--reviewed')
        result = (project / 'AGENTS.md').read_text()
        check('reviewed template preserves current rules without private paths or unresolved fields',
              old in result and 'Concurrent edit.' in result and str(repo) not in result and '{{' not in result)
        cli('init-project', str(project), '--purpose', 'Do not overwrite', expected=2)

        cli('attach')
        check('instruction preview preserves native files', all('AGENT_ASSET_KIT_START' not in p.read_text() for p in instruction_paths))
        attachment = cli('attach', '--apply')
        check('additive pointers preserve original instructions and backups',
              len(attachment['changes']) == 3 and all(p.read_text().startswith('# Existing native identity') for p in instruction_paths) and
              all(Path(c['backup']).read_text() == '# Existing native identity and rules\n' for c in attachment['changes']))
        check('instruction attachment is idempotent', not cli('attach', '--apply')['changes'])
        for path in instruction_paths:
            text = path.read_text()
            check('library pointer uses installed module and excludes client-specific policies',
                  text.count('<!-- AGENT_ASSET_KIT_START -->') == 1 and
                  'aak' in text and '--root' in text and 'AGENT_BROWSER' not in text)
        malformed = instruction_paths[-1]
        valid = malformed.read_text()
        malformed.write_text(valid + '\n<!-- AGENT_ASSET_KIT_START -->\n')
        snapshots = {p: p.read_bytes() for p in instruction_paths}
        cli('attach', '--apply', expected=2)
        check('malformed markers stop before any native instruction changes',
              all(p.read_bytes() == data for p, data in snapshots.items()))
        malformed.write_text(valid)
        check('native memory bytes remain unchanged', native_memory ==
              {p.name: p.read_bytes() for p in (home / '.hermes/memories').iterdir()})

        interrupted = repo / '.local/deployments/synthetic-interruption.json'
        interrupted.write_text(json.dumps({'state': 'SWITCHING', 'changes': []}))
        cli('deploy', expected=2)
        checked = cli('recheck', 'synthetic-interruption', '--reviewed')
        check('interrupted intent requires explicit recheck and is never replayed', checked['replayed'] is False)
        cli('sync', '--agent', 'codex', '--message', 'fixture reconciliation', '--local-only')
        one = Path(cli('workspace', '--agent', 'codex')['workspace'])
        two = Path(cli('workspace', '--agent', 'hermes')['workspace'])
        body = output / 'note.txt'
        body.write_text('A synthetic durable fact with explicit scope and source.\n')
        for flags in ([], ['--inbox']):
            rejected = cli('note', '--agent', 'codex', '--scope', 'synthetic-project', *flags,
                           '--title', 'project fact', '--source', 'synthetic user instruction',
                           '--file', str(body), cwd=one, expected=2)
            check('project notes are rejected without creating storage, flags=' + str(flags),
                  'outside AgentAssetKit' in rejected['reason'] and
                  not (one / 'memory/projects').exists() and not (one / 'memory/inbox').exists())
        pending = cli('note', '--agent', 'codex', '--scope', 'global', '--inbox',
                      '--title', 'pending fact', '--source', 'synthetic user instruction',
                      '--file', str(body), cwd=one)
        check('pending global Agent preferences retain scope and source in the agent inbox',
              pending['note'].startswith('memory/inbox/codex/') and
              'scope: "global"' in (one / pending['note']).read_text() and
              'source: "synthetic user instruction"' in (one / pending['note']).read_text())
        for agent, directory in [('codex', one), ('hermes', two)]:
            cli('note', '--agent', agent, '--scope', 'global', '--title', agent + ' fact',
                '--source', 'synthetic user instruction', '--file', str(body), cwd=directory)
            cli('sync', '--agent', agent, '--message', 'independent session note', '--local-only', cwd=directory)
        check('independent workspaces merge without staging each others unfinished edits',
              len(list((repo / 'memory/shared').glob('*.md'))) == 2 and one != two)
        unknown = one / 'memory/shared/unknown.md'
        write(unknown, 'Unknown staged edit\n')
        execute(['git', 'add', str(unknown)], cwd=one)
        cli('sync', '--agent', 'codex', '--message', 'must stop', '--local-only', cwd=one, expected=2)
        check('unknown staged content remains for inspection', 'unknown.md' in execute(['git', 'diff', '--cached', '--name-only'], cwd=one))

        secret = 'ghp_' + hashlib.sha256(b'agent-assets-e2e-credential-guard').hexdigest()[:36]
        unsafe = home / '.codex/skills/unsafe'
        package_at(unsafe)
        (unsafe / 'SKILL.md').write_text((unsafe / 'SKILL.md').read_text() + '\naccess_token = "' + secret + '"\n')
        rejected = cli('collect', expected=2)
        check('secret-bearing new skill never enters canonical assets', bool(rejected['blocked']) and
              not any(p['name'] == 'unsafe' for p in read(repo / 'skills/manifest.json')['packages'].values()))
        check('scanner diagnostics contain no credential text', secret not in (repo / '.local/last-scan.json').read_text())
        body.write_text('access_token = "' + secret + '"\n')
        cli('note', '--agent', 'codex', '--scope', 'global', '--title', 'rejected', '--source', 'synthetic',
            '--file', str(body), cwd=two, expected=2)
        report.update(result='PASS', exit_status=0)
    except BaseException as error:
        report.update(error=str(error), traceback=traceback.format_exc(), exit_status=1)
    finally:
        report['assertions'] = assertions
        report['commands'] = len(commands)
        sources = {}
        for prefix in ('scripts', 'templates', 'tests', 'agents'):
            for path in (ROOT / prefix).rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts:
                    sources[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        report['source_hashes'] = sources
        report['source_revision'] = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
            capture_output=True, text=True).stdout.strip() or 'uncommitted'
        (output / 'commands.json').write_text(json.dumps(commands, indent=2))
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        digest = hashlib.sha256((output / 'commands.json').read_bytes()).hexdigest()
        (output / 'SHA256SUMS').write_text(digest + '  commands.json\n' +
            hashlib.sha256((output / 'report.json').read_bytes()).hexdigest() + '  report.json\n')
        (output / 'REPRODUCE.txt').write_text('From repository root:\npython3 tests/e2e/run.py --scanner /absolute/path/to/verified/gitleaks\n'
            'Prerequisites: Python 3.11+, Git and Gitleaks 8.30.1. Synthetic files are generated from tests/fixtures/native-skill; no accounts.\n')
        print(json.dumps({'result': report['result'], 'assertions': len(assertions), 'report': str(output / 'report.json'),
                          'error': report.get('error')}, ensure_ascii=False))
    return report['exit_status']


if __name__ == '__main__':
    sys.exit(main())
