#!/usr/bin/env python3
"""Portable dependency declarations through real CLI workflows in synthetic homes.

Failure modes: removed connection commands still run; declarations or references
stop being validated; revision/backflow/Git copies lose dependency metadata;
ordinary asset workflows read legacy connection state or invoke an MCP manager.
"""

import argparse
import copy
import hashlib
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
    scanner = str(Path(args.scanner).resolve())
    output = ROOT / 'artifacts/e2e/skill-dependencies' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
    output.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix='aak-dependencies-')).resolve()
    repo, home = work / 'repository', work / 'home'
    commands, checks = [], []
    environment = dict(os.environ)
    report = {'status': 'FAIL', 'platform': platform.platform(), 'python': sys.version,
              'scope': 'real asset CLI/files/Git/scanner; synthetic homes and executable traps; no MCP service, credentials or native client calls',
              'workdir': str(work)}

    def run(argv, cwd=repo, expected=0):
        p = subprocess.run(list(map(str, argv)), cwd=cwd, env=environment, capture_output=True,
                           text=True, encoding='utf-8', timeout=90)
        commands.append({'argv': list(map(str, argv)), 'exit': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
        assert p.returncode == expected, f'{argv[:4]}: {p.returncode}, expected {expected}: {p.stdout[:600]} {p.stderr[:300]}'
        return p.stdout

    def cli(*argv, cwd=repo, expected=0):
        return json.loads(run(['aak', '--root', cwd, *argv], cwd, expected))

    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def read(path):
        return json.loads(path.read_text(encoding='utf-8'))

    def check(name, condition):
        assert condition, name
        checks.append(name)

    def sync():
        cli('sync', '--agent', 'codex', '--message', 'Synthetic declared skill', '--local-only')

    def deploy():
        plan = cli('deploy')
        return cli('deploy', '--apply', '--plan', plan['plan_id'])

    try:
        work.mkdir(exist_ok=True)
        run(['aak', 'init', repo, '--git'], work)
        run(['git', 'config', 'user.name', 'Synthetic dependencies'])
        run(['git', 'config', 'user.email', 'fixture@example.invalid'])
        cli('configure', '--home', home, '--agents', 'codex,claude', '--gitleaks', scanner)
        trap = work / 'unexpected-external-call'
        program = work / 'trap.py'
        write(program, 'from pathlib import Path\nPath(' + repr(str(trap)) + ').write_text("unexpected external invocation")\nraise SystemExit(91)\n')
        binary_dir = work / 'bin'
        for name in ('mcplink', 'fixture-helper'):
            executable = binary_dir / (name + ('.cmd' if os.name == 'nt' else ''))
            write(executable, ('@"' + sys.executable + '" "' + str(program) + '" %*\r\n') if os.name == 'nt' else
                  '#!' + sys.executable + '\nexec(' + repr(program.read_text(encoding='utf-8')) + ')\n')
            executable.chmod(0o755)
        environment.update(MCPLINK_BIN=str(binary_dir / ('mcplink.cmd' if os.name == 'nt' else 'mcplink')),
                           PATH=str(binary_dir) + os.pathsep + environment.get('PATH', ''))
        legacy = {repo / '.local/mcp-bindings.json': 'opaque legacy connection state; not JSON\n',
                  repo / '.local/mcp-plans/legacy.json': 'opaque legacy receipt; not JSON\n',
                  home / '.codex/config.toml': '[mcp_servers.mcplink]\ncommand = "synthetic-manager"\nargs = ["bridge", "--connection", "synthetic-entry"]\n',
                  home / '.claude.json': '{"mcpServers":{"fixture":{"command":"synthetic-manager"}}}\n'}
        for path, data in legacy.items():
            write(path, data)
        before_legacy = {path: path.read_bytes() for path in legacy}
        check('connection management is absent from CLI help', 'mcp' not in run(['aak', '--help']))
        for command in ('audit', 'doctor'):
            check(command + ' exposes no live MCP flag', '--mcp' not in run(['aak', command, '--help']))
        for arguments in [('mcp', 'check'), ('mcp', 'bind'), ('mcp', 'connect'), ('audit', '--mcp'), ('doctor', '--mcp')]:
            run(['aak', '--root', repo, *arguments], expected=2)
        check('removed commands and flags fail without reading legacy state or starting an external program',
              not trap.exists() and all(path.read_bytes() == data for path, data in before_legacy.items()))

        skill = repo / 'skills/custom/demo'
        write(skill / 'SKILL.md', '---\nname: demo\ndescription: Synthetic capability declaration.\n---\n\n[Dependency context](references/service.md)\n')
        write(skill / 'references/service.md', '# Synthetic service\n\nRead one synthetic item with read_item.\n')
        ident = cli('register', '--path', 'skills/custom/demo', '--targets', 'codex', '--agent', 'codex', '--reviewed')['skill_id']
        declarations = [dict(service='notes', mode='read', via='native', tools=['read_item'], optional=False, reference='references/service.md'),
                        dict(service='notes', mode='fallback', via='cli', tools=['read_item'], optional=True, reference='references/service.md')]
        requirements = {'mcp': declarations, 'commands': [{'any_of': ['fixture-helper'], 'mode': 'local-helper'}]}
        manifest_path = repo / 'skills/manifest.json'
        manifest = read(manifest_path)
        manifest['packages'][ident]['requirements'] = requirements
        write(manifest_path, json.dumps(manifest, indent=2) + '\n')
        audited = cli('audit')
        check('existing native and CLI capability declarations remain valid portable metadata',
              audited['packages'][0]['mcp_declarations'] == declarations and not audited['issues'])
        check('audit reports declarations without a connection-readiness result',
              'mcp_checked' not in audited and 'mcp' not in audited and
              'availability is not checked' in audited['limits'])
        check('generic command requirements locate executables without running them',
              audited['packages'][0]['requirements'][0]['status'] == 'FOUND' and not trap.exists())
        cli('index', '--agent', 'codex', '--reviewed')
        index = (repo / 'skills/README.md').read_text(encoding='utf-8')
        check('generated navigation retains capability declarations and contains no removed commands',
              'notes / read' in index and 'read_item' in index and 'mcp check' not in index and '--mcp' not in index)

        invalid = [('machine-specific connection ID', dict(declarations[0], connection_id='synthetic-device-id')),
                   ('machine-specific source', dict(declarations[0], source='synthetic:user')),
                   ('empty tools', dict(declarations[0], tools=[])),
                   ('duplicate tools', dict(declarations[0], tools=['read_item', 'read_item'])),
                   ('non-string tool', dict(declarations[0], tools=[{}])),
                   ('invalid service identity', dict(declarations[0], service='')),
                   ('invalid access method', dict(declarations[0], via='managed-connection')),
                   ('non-boolean optional flag', dict(declarations[0], optional='yes')),
                   ('missing reference', dict(declarations[0], reference='references/absent.md')),
                   ('escaping reference', dict(declarations[0], reference='../outside.md'))]
        for label, declaration in invalid:
            altered = copy.deepcopy(manifest)
            altered['packages'][ident]['requirements']['mcp'] = [declaration]
            write(manifest_path, json.dumps(altered))
            result = cli('audit', expected=2)
            check(label + ' is rejected by offline declaration validation',
                  any(issue['code'] == 'INVALID_MCP_DECLARATION' for issue in result['issues']) and not trap.exists())
        for label, values in [('non-list declarations', {}), ('duplicate service/mode', [declarations[0], declarations[0]])]:
            altered = copy.deepcopy(manifest)
            altered['packages'][ident]['requirements']['mcp'] = values
            write(manifest_path, json.dumps(altered))
            result = cli('audit', expected=2)
            check(label + ' is rejected without connection discovery',
                  any(issue['code'] == 'INVALID_MCP_DECLARATION' for issue in result['issues']) and not trap.exists())
        cli('index', '--agent', 'codex', '--reviewed', expected=2)
        write(manifest_path, json.dumps(manifest, indent=2) + '\n')
        sync()
        deploy()
        cli('share', 'demo', '--to', 'claude')
        sync()
        check('skills with required MCP declarations deploy and share without a connection manager',
              all((home / ('.' + kind) / 'skills/demo/SKILL.md').is_file() for kind in ('codex', 'claude')) and not trap.exists())
        for command in ('audit', 'doctor'):
            result = cli(command)
            check(command + ' validates installed skills without claiming service availability',
                  result['status'] == 'PASS' and result['packages'][0]['mcp_declarations'] == declarations and
                  'mcp' not in result and not trap.exists())
        native = home / '.codex/skills/demo/SKILL.md'
        write(native, native.read_text(encoding='utf-8') + '\nNative improvement.\n')
        candidate = cli('collect')['candidates'][0]['id']
        cli('accept', candidate, '--agent', 'codex', '--reviewed')
        check('native backflow preserves the full reviewed dependency declaration',
              read(manifest_path)['packages'][ident]['requirements'] == requirements)
        sync()
        deploy()
        check('backflow reaches other copies while native MCP configuration stays intact',
              'Native improvement.' in (home / '.claude/skills/demo/SKILL.md').read_text(encoding='utf-8') and
              all(path.read_bytes() == data for path, data in before_legacy.items()))
        write(skill / 'SKILL.md', (skill / 'SKILL.md').read_text(encoding='utf-8') + '\nReviewed library improvement.\n')
        cli('revision', '--skill-id', ident, '--agent', 'codex', '--reviewed')
        check('direct reviewed revision also retains dependency metadata', read(manifest_path)['packages'][ident]['requirements'] == requirements)
        sync()
        clone = work / 'other-device'
        run(['git', 'clone', '--local', repo, clone], work)
        check('Git copies preserve declarations without local connection state',
              read(clone / 'skills/manifest.json')['packages'][ident]['requirements'] == requirements and not (clone / '.local').exists())
        cli('configure', '--home', work / 'other-home', '--agents', 'codex', '--gitleaks', scanner, cwd=clone)
        cli('share', 'demo', '--to', 'codex', cwd=clone)
        check('a second device can install the declared skill without MCP enrollment',
              (work / 'other-home/.codex/skills/demo/SKILL.md').read_bytes() == (skill / 'SKILL.md').read_bytes() and not trap.exists())
        check('legacy local receipts and all native connection files remain byte-for-byte unchanged',
              all(path.read_bytes() == data for path, data in before_legacy.items()) and not trap.exists())
        cleanup_workdir(work, 'aak-dependencies-')
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
