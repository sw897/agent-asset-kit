#!/usr/bin/env python3
"""Portable installed-CLI checks for common and legacy project templates."""

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

from fixture_cleanup import cleanup_workdir

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / 'src/agent_asset_kit/resources/library'
TEMPLATES = (SEED if SEED.is_dir() else ROOT) / 'templates/agents'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scanner', required=True)
    args = parser.parse_args()
    scanner = Path(args.scanner).resolve()
    output = ROOT / 'artifacts/e2e/project-templates' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
    output.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix='aa-project-template-'))
    library, home = work / 'asset library 示例', work / 'synthetic-home'
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / '.config'),
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_COUNT='0',
               GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never', PYTHONDONTWRITEBYTECODE='1')
    for name in ('AGENT_ASSET_KIT_ROOT', 'GH_TOKEN', 'GITHUB_TOKEN'):
        env.pop(name, None)
    commands, checks = [], []
    report = {'status':'FAIL', 'environment':platform.platform(), 'python':sys.version,
              'scope':'installed CLI and synthetic projects only; no native clients, providers or remote Git',
              'workdir':str(work), 'scanner_sha256':hashlib.sha256(scanner.read_bytes()).hexdigest()}

    def run(argv, cwd=work, expected=0):
        p = subprocess.run([str(x) for x in argv], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding='utf-8', timeout=60)
        commands.append({'argv':[str(x) for x in argv], 'exit_status':p.returncode,
                         'stdout':p.stdout, 'stderr':p.stderr})
        assert p.returncode == expected, p.stdout[:600] + p.stderr[:300]
        return p.stdout

    def cli(*args, expected=0):
        return json.loads(run(['aak', '--root', library, *args], expected=expected))

    def check(name, condition):
        assert condition, name
        checks.append(name)

    def proposed(plan):
        return Path(plan['proposed_file']).read_text(encoding='utf-8')

    def metadata(plan):
        return json.loads((Path(plan['proposed_file']).parent / 'plan.json').read_text(encoding='utf-8'))

    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='')

    try:
        run(['aak', 'init', library, '--git'])
        cli('configure', '--home', home, '--agents', 'codex', '--gitleaks', scanner)
        run(['git', 'config', 'user.name', 'Synthetic Template E2E'], library)
        run(['git', 'config', 'user.email', 'fixture@example.invalid'], library)
        run(['git', 'add', '.'], library)
        run(['git', '-c', 'commit.gpgsign=false', 'commit', '-m', 'Synthetic template library'], library)
        empty = work / 'empty project 包'
        empty.mkdir()
        packaged = cli('init-project', empty)
        check('packaged defaults work without purpose or commands', metadata(packaged)['template'] == 'base')
        check('preview does not create the target instructions', not (empty / 'AGENTS.md').exists())

        # Libraries can maintain their own templates independently of the wheel.
        shutil.copytree(TEMPLATES, library / 'templates/agents', dirs_exist_ok=True)
        target = work / 'project with spaces 中文'
        original = '# Existing project rules\nKeep the established local constraint.\n'
        write(target / 'AGENTS.md', original)
        write(target / 'README.md', 'PROJECT_FACT_FROM_README_DO_NOT_COPY\n')
        write(target / 'package.json', '{"name":"PROJECT_FACT_FROM_PACKAGE_DO_NOT_COPY","scripts":{"test":"DO_NOT_EXECUTE_PROJECT_COMMAND"}}\n')
        write(target / 'go.mod', 'module example.invalid/project\n')
        write(target / 'pyproject.toml', '[project]\nname="PROJECT_FACT_FROM_PYTHON_DO_NOT_COPY"\n')
        before = (target / 'AGENTS.md').read_bytes()
        plan = cli('init-project', target)
        text = proposed(plan)
        base = (TEMPLATES / 'base.md').read_text(encoding='utf-8')
        check('default selection is the common base only', metadata(plan)['template'] == 'base' and base in text)
        check('project facts are not copied into common instructions',
              target.name not in text and 'PROJECT_FACT_' not in text and 'DO_NOT_EXECUTE_PROJECT_COMMAND' not in text)
        check('no project placeholders or private library path remain', '{{' not in text and str(library) not in text)
        check('preview preserves the existing target bytes', (target / 'AGENTS.md').read_bytes() == before)
        check('existing rules remain in the proposal', original.rstrip() in text)
        for kind in ('software', 'research', 'documents'):
            selected = cli('init-project', target, '--template', kind)
            body = proposed(selected)
            check(kind + ' module is optional and needs no project facts',
                  metadata(selected)['template'] == kind and base in body and
                  (TEMPLATES / 'modules' / (kind + '.md')).read_text(encoding='utf-8') in body and
                  'PROJECT_FACT_' not in body and '{{' not in body)
        explicit = cli('init-project', target, '--purpose', 'LEGACY_PURPOSE_DO_NOT_COPY',
                       '--command', 'LEGACY_COMMAND_DO_NOT_EXECUTE')
        check('legacy arguments do not inject facts into common templates',
              'LEGACY_PURPOSE_DO_NOT_COPY' not in proposed(explicit) and
              'LEGACY_COMMAND_DO_NOT_EXECUTE' not in proposed(explicit))
        cli('apply-project', plan['plan'], expected=2)
        check('unreviewed application leaves the target unchanged', (target / 'AGENTS.md').read_bytes() == before)
        concurrent = original + 'Concurrent project constraint.\n'
        write(target / 'AGENTS.md', concurrent)
        cli('apply-project', plan['plan'], '--reviewed', expected=2)
        check('stale preview cannot overwrite concurrent edits', (target / 'AGENTS.md').read_text(encoding='utf-8') == concurrent)
        fresh = cli('init-project', target)
        applied = cli('apply-project', fresh['plan'], '--reviewed')
        value = (target / 'AGENTS.md').read_bytes()
        check('reviewed application matches the actual file hash', applied['sha256'] == hashlib.sha256(value).hexdigest())
        check('reviewed application retains old and concurrent project rules', concurrent.rstrip() in value.decode('utf-8'))
        backup = Path(fresh['proposed_file']).parent / 'original-AGENTS.md'
        check('existing instructions have a rollback copy', backup.read_bytes() == concurrent.encode('utf-8'))
        cli('init-project', target, expected=2)
        check('already initialized projects are not automatically overwritten', (target / 'AGENTS.md').read_bytes() == value)
        created = cli('apply-project', packaged['plan'], '--reviewed')
        check('reviewed application can create previously absent instructions',
              created['applied'] and (empty / 'AGENTS.md').is_file())

        legacy = work / 'legacy project 示例'
        write(legacy / 'package.json', '{"name":"legacy-fixture"}\n')
        write(library / 'templates/agents/base.md', '# {{PROJECT}}\n{{PURPOSE}}\n{{STACK}}\n{{COMMANDS}}\n')
        write(library / 'templates/agents/modules/software.md', 'Module purpose: {{PURPOSE}}\n')
        plans = library / '.local/project-plans'
        count = len(list(plans.iterdir()))
        cli('init-project', legacy, '--template', 'software', expected=2)
        check('legacy purpose requirement fails before writing a plan or target',
              len(list(plans.iterdir())) == count and not (legacy / 'AGENTS.md').exists())
        legacy_plan = cli('init-project', legacy, '--template', 'software', '--purpose', 'Synthetic legacy purpose',
                          '--command', 'DO_NOT_EXECUTE_LEGACY_COMMAND')
        body = proposed(legacy_plan)
        check('existing custom project placeholders still render',
              legacy.name in body and 'Synthetic legacy purpose' in body and
              'JavaScript/TypeScript' in body and 'DO_NOT_EXECUTE_LEGACY_COMMAND' in body and '{{' not in body)
        check('known placeholders also render inside an optional module', 'Module purpose: Synthetic legacy purpose' in body)
        write(library / 'templates/agents/modules/software.md', '{{UNSUPPORTED_TEMPLATE_FIELD}}\n')
        count = len(list(plans.iterdir()))
        cli('init-project', legacy, '--template', 'software', '--purpose', 'Synthetic', expected=2)
        check('unknown module fields cannot leak into a generated proposal',
              len(list(plans.iterdir())) == count and not (legacy / 'AGENTS.md').exists())
        check('initialization does not commit or modify project code',
              (target / 'package.json').read_text(encoding='utf-8').startswith('{"name":"PROJECT_FACT_') and
              not (target / '.git').exists() and not (legacy / '.git').exists())
        report['status'] = 'PASS'
    except Exception as error:
        report.update(error=str(error), traceback=traceback.format_exc())
    finally:
        report.update(checks=checks, commands=commands)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if report['status'] == 'PASS':
            cleanup_workdir(work, 'aa-project-template-')
    print(json.dumps({'status':report['status'], 'assertions':len(checks), 'report':str(output / 'report.json')}, ensure_ascii=False))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
