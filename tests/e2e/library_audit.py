#!/usr/bin/env python3
"""Real CLI checks for Home Repo validation without a local Agent environment.

Failure modes: CI reads local config/runtime state or requires client software;
unreviewed bytes, broken references/dependencies/releases or nonportable tracked
paths pass; the new mode changes the existing local audit or writes assets.
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

from fixture_cleanup import cleanup_workdir

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scanner', required=True)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix='aak-library-audit-')).resolve()
    repo, home = work / 'home repo 示例', work / 'synthetic-clients'
    output = ROOT / 'artifacts/e2e/library-audit' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
    output.mkdir(parents=True)
    commands, checks = [], []
    environment = dict(os.environ)
    environment.pop('AGENT_ASSET_KIT_ROOT', None)
    report = {'status': 'FAIL', 'platform': platform.platform(), 'python': sys.version,
              'scope': 'installed CLI, synthetic library/client files and local Git only; no real client or provider'}

    def run(argv, cwd=repo, expected=0, data=None):
        p = subprocess.run(list(map(str, argv)), cwd=cwd, env=environment, input=data,
                           capture_output=True, text=True, encoding='utf-8', timeout=90)
        commands.append({'argv': list(map(str, argv)), 'exit': p.returncode,
                         'stdout': p.stdout, 'stderr': p.stderr})
        assert p.returncode == expected, f'{argv[:4]}: exit {p.returncode}, expected {expected}: {p.stdout[:800]} {p.stderr[:300]}'
        return p.stdout

    def cli(*argv, expected=0):
        return json.loads(run(['aak', '--root', repo, *argv], expected=expected))

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')

    def check(name, condition):
        assert condition, name
        checks.append(name)

    try:
        run(['aak', 'init', repo, '--git'], cwd=work)
        clean = cli('audit', '--library-only')
        check('empty unconfigured library is valid', clean['status'] == 'PASS' and clean['canonical_packages'] == 0)
        cli('configure', '--home', home, '--agents', 'codex', '--gitleaks', Path(args.scanner).resolve())
        source = home / '.codex/skills/library-demo/SKILL.md'
        source.parent.mkdir(parents=True)
        source.write_text('---\nname: library-demo\ndescription: Synthetic library verification.\n---\n\nUse the reviewed fixture.\n', encoding='utf-8')
        candidate = cli('collect')['candidates'][0]['id']
        ident = cli('accept', candidate, '--kind', 'custom', '--agent', 'codex', '--reviewed')['skill_id']
        manifest_path = repo / 'skills/manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        record = manifest['packages'][ident]
        record['requirements'] = {
            'commands': [{'mode': 'synthetic', 'any_of': ['aak-fixture-command-that-is-not-installed'], 'optional': False}],
            'files': [{'mode': 'synthetic', 'path': str(work / 'missing-runtime-file'), 'optional': False}]}
        write(manifest_path, manifest)
        run(['git', 'config', 'user.name', 'Library audit fixture'])
        run(['git', 'config', 'user.email', 'fixture@example.invalid'])
        cli('sync', '--agent', 'codex', '--message', 'Reviewed synthetic assets', '--local-only')
        commit = run(['git', 'rev-parse', 'HEAD']).strip()
        releases = {'version': 1, 'skills': {ident: {'stable': {'commit': commit, 'sha256': record['sha256']}}}}
        write(repo / 'skills/releases.json', releases)

        config = repo / '.local/config.json'
        original_config = config.read_bytes()
        for relative in ('config.json', 'baselines.json', 'trials/ignored/trial.json'):
            path = repo / '.local' / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{not valid local JSON', encoding='utf-8')
        before = {str(p.relative_to(repo)): p.read_bytes() for p in (repo / 'skills').rglob('*') if p.is_file()}
        result = cli('audit', '--library-only', '--output', work / 'library-report.json')
        check('canonical library passes without local config, runtime or trial parsing',
              result['status'] == 'PASS' and result['scope'] == 'library-only' and result['canonical_packages'] == 1)
        check('native copies and discovery are not checked', result['copies'] == [] and result['native_discovery'] == {})
        check('runtime dependencies are not probed', result['packages'][0]['requirements'] == [])
        check('output report matches stdout', json.loads((work / 'library-report.json').read_text(encoding='utf-8')) == result)
        check('asset bytes are unchanged', before == {str(p.relative_to(repo)): p.read_bytes() for p in (repo / 'skills').rglob('*') if p.is_file()})
        check('ordinary audit still requires valid local configuration', cli('audit', expected=2)['status'] == 'STOPPED')
        for option in (['--target', 'codex'], ['--instance', 'fixture']):
            check('local selection rejected: ' + option[0], cli('audit', '--library-only', *option, expected=2)['status'] == 'STOPPED')

        canonical = repo / record['path'] / 'SKILL.md'
        original = canonical.read_bytes()
        canonical.write_bytes(original + b'\nUnreviewed edit.\n')
        result = cli('audit', '--library-only', expected=2)
        check('unreviewed package bytes fail', any(i['code'] == 'CANONICAL_INVALID' for i in result['issues']))
        canonical.write_bytes(original)
        record['dependencies'] = [{'skill': 'missing-fixture-skill'}]
        write(manifest_path, manifest)
        result = cli('audit', '--library-only', expected=2)
        check('broken skill dependency fails', any(i['code'] == 'UNKNOWN_DEPENDENCY' for i in result['issues']))
        record.pop('dependencies')
        write(manifest_path, manifest)

        config.write_bytes(original_config)
        canonical.write_bytes(original + b'\n[Required reference](references/missing.md)\n')
        cli('revision', '--skill-id', ident, '--agent', 'codex', '--reviewed')
        config.write_text('{not valid local JSON', encoding='utf-8')
        result = cli('audit', '--library-only', expected=2)
        check('reviewed package with a missing reference fails', any(i['code'] == 'MISSING_FILE_REFERENCE' for i in result['issues']))
        canonical.write_bytes(original)
        write(manifest_path, manifest)
        releases['skills'][ident]['stable']['commit'] = 'missing-fixture-revision'
        write(repo / 'skills/releases.json', releases)
        check('unresolvable stable release fails', cli('audit', '--library-only', expected=2)['status'] == 'STOPPED')
        releases['skills'][ident]['stable']['commit'] = commit
        write(repo / 'skills/releases.json', releases)

        oid = run(['git', 'hash-object', '-w', '--stdin'], data='Synthetic tracked path\n').strip()
        run(['git', 'update-index', '--add', '--cacheinfo', '100644,' + oid + ',CON.md'])
        check('nonportable tracked checkout fails', cli('audit', '--library-only', expected=2)['status'] == 'STOPPED')
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        try:
            cleanup_workdir(work, 'aak-library-audit-')
        except Exception:
            report['status'] = 'FAIL'
            report['cleanup_error'] = traceback.format_exc()
        report.update(checks=checks, assertions=len(checks), commands=commands)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'assertions': len(checks), 'report': str(output / 'report.json')}))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
