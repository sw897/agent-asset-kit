"""Read-only skill contracts, native discovery and generated navigation.

Native discovery only initializes clients and reads their catalogs. It never sends
a user prompt, creates a model turn, calls a business tool or approves a request.
"""

import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

from .assetlib import (ROOT, LOCAL, Stop, atomic, cfg, files_in, inventory, key,
                      load, native_path, package, registry, relative, save, tree_hash, read_native, ACTIVE_TRIAL_STATES)
from . import agent_adapters
from . import skill_mcp
from .skill_versions import effective

TARGETS = agent_adapters.KINDS
SKILL_CALL = re.compile(
    r'''skill_view\(\s*(?:name\s*=\s*)?["']([^"']+)["']'''
    r'''(?:\s*,\s*(?:file_path\s*=\s*)?["']([^"']+)["'])?''')


def entry_fields(path):
    """Extract index scalars, not a substitute for a client's YAML parser."""
    text = path.read_text(encoding='utf-8')
    if not text.startswith('---\n') and not text.startswith('---\r\n'):
        raise Stop('frontmatter must start on the first line')
    parts = re.split(r'^---\s*$', text, maxsplit=2, flags=re.M)
    if len(parts) != 3:
        raise Stop('frontmatter closing marker is missing')
    fields = {}
    lines = parts[1].splitlines()
    for i, line in enumerate(lines):
        match = re.match(r'^(name|description):\s*(.*)$', line)
        if not match:
            continue
        value = match[2].strip()
        if value in ('|', '>', '|-', '>-'):
            following = []
            for row in lines[i + 1:]:
                if row and not row[0].isspace():
                    break
                following.append(row.strip())
            value = ' '.join(following)
        elif len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[match[1]] = value
    if not fields.get('name') or not fields.get('description'):
        raise Stop('name and description are required for discovery')
    return fields


def targets(value):
    return TARGETS if value == 'all' else (value,)


def contracts(manifest):
    by_name = {r['name']: r for r in manifest['packages'].values()}
    issues = skill_mcp.contracts(manifest)
    aliases = {}
    categories = load(ROOT / 'skills/categories.json', {})
    for r in manifest['packages'].values():
        category = r.get('category')
        if category and category not in categories:
            issues.append({'skill': r['name'], 'code': 'UNKNOWN_CATEGORY', 'severity': 'error'})
        for alias in r.get('legacy_names', []):
            if alias in by_name or alias in aliases:
                issues.append({'skill': r['name'], 'code': 'AMBIGUOUS_LEGACY_NAME', 'severity': 'error'})
            aliases[alias] = r['name']
        for dep in r.get('dependencies', []):
            if not isinstance(dep, dict) or dep.get('skill') not in by_name:
                issues.append({'skill': r['name'], 'code': 'UNKNOWN_DEPENDENCY', 'severity': 'error'})
                continue
            installed_targets = {b['agent'] for b in by_name[dep['skill']]['bindings']}
            for b in r['bindings']:
                if b['agent'] in TARGETS and b['agent'] not in installed_targets:
                    issues.append({'skill': r['name'], 'dependency': dep['skill'],
                                   'target': b['agent'], 'when': dep.get('when'),
                                   'code': 'DEPENDENCY_NOT_BOUND',
                                   'severity': 'warning' if dep.get('conditional') else 'error'})
    return issues


def reference_checks(manifest):
    by_name = {r['name']: r for r in manifest['packages'].values()}
    rows, issues = [], []
    placeholders = {'url', 'path', '链接', '相对路径', '相对路径.png'}
    for r in manifest['packages'].values():
        folder = ROOT / r['path']
        for name, (raw, _) in files_in(folder).items():
            if not name.endswith('.md'):
                continue
            text = raw.decode('utf-8')
            # Sample output templates and fenced syntax are not live file links.
            prose = re.sub(r'^(`{3,}|~{3,}).*?^\1\s*$', '', text, flags=re.M | re.S)
            link_prose = re.sub(r'(`+)[^`\n]*?\1', '', prose)
            for match in re.finditer(r'\[[^\]\n]*\]\(([^)\n]+)\)', link_prose):
                dest = match[1].split('#', 1)[0]
                if (not dest or re.match(r'^[a-z]+:', dest, re.I) or dest.startswith(('/', '~'))
                        or any(c in dest for c in '<>*|{} …') or dest.lower() in placeholders
                        or '...' in dest):
                    continue
                resolved = (folder / name).parent / dest
                status = 'PASS' if resolved.exists() else 'MISSING'
                rows.append({'skill': r['name'], 'file': name, 'reference': dest, 'status': status})
                if status != 'PASS':
                    issues.append({'skill': r['name'], 'file': name, 'reference': dest,
                                   'code': 'MISSING_FILE_REFERENCE', 'severity': 'error'})
            for match in SKILL_CALL.finditer(prose):
                dep, dest = match.groups()
                if not dest or any(c in dest for c in '<>*{}'):
                    continue
                if dep not in by_name:
                    rows.append({'skill': r['name'], 'file': name, 'dependency': dep,
                                 'reference': dest, 'status': 'NATIVE_OR_EXTERNAL_UNVERIFIED'})
                    continue
                path = ROOT / by_name[dep]['path'] / relative(dest)
                status = 'PASS' if path.is_file() else 'MISSING'
                rows.append({'skill': r['name'], 'file': name, 'dependency': dep,
                             'reference': dest, 'status': status})
                if status != 'PASS':
                    issues.append({'skill': r['name'], 'file': name, 'dependency': dep,
                                   'reference': dest, 'code': 'MISSING_SKILL_REFERENCE', 'severity': 'error'})
    return rows, issues


def legacy_commands(config):
    """Observe native command entry points; never adopt or rewrite them."""
    result = []
    for row in agent_adapters.endpoints(config, {'agent': 'claude'}):
        root = Path(row.get('instructions') or Path(row['skills_root']).parent / 'CLAUDE.md').parent / 'commands'
        result.extend({'name': p.relative_to(root).with_suffix('').as_posix(), 'path': str(p),
                       'instance': row['id'], 'origin': 'UNVERIFIED', 'management': 'native-unregistered'}
                      for p in sorted(root.rglob('*.md')) if p.is_file())
    return result


def applicability(spec, config):
    platforms = spec.get('platforms', [])
    if not isinstance(platforms, list) or any(p not in ('win32', 'darwin', 'linux') for p in platforms):
        raise Stop('requirement platforms must name supported Python platforms')
    if platforms and sys.platform not in platforms:
        return {'status': 'NOT_APPLICABLE', 'reason': 'mode is limited to other platforms',
                'platforms': platforms}
    required = spec.get('requires_agent')
    if required and required not in agent_adapters.KINDS:
        raise Stop('requirement references an unknown Agent kind')
    if required and not any(row['kind'] == required for row in agent_adapters.configured(config)):
        return {'status': 'NOT_APPLICABLE', 'reason': 'mode requires an enabled local Agent',
                'requires_agent': required}
    return None


def command_checks(record, config):
    rows = []
    requirements = record.get('requirements', {})
    for spec in requirements.get('commands', []):
        options = spec.get('any_of', [])
        skipped = applicability(spec, config)
        if skipped:
            rows.append({'mode': spec.get('mode', 'default'), 'any_of': options,
                         'optional': bool(spec.get('optional')), **skipped})
            continue
        found = next((shutil.which(name) for name in options if shutil.which(name)), None)
        rows.append({'mode': spec.get('mode', 'default'), 'any_of': options,
                     'optional': bool(spec.get('optional')), 'status': 'FOUND' if found else 'MISSING',
                     'resolved_path': found, 'level': 'executable-location-only'})
    for spec in requirements.get('files', []):
        skipped = applicability(spec, config)
        if skipped:
            rows.append({'mode': spec.get('mode', 'default'), 'optional': bool(spec.get('optional')), **skipped})
            continue
        override = os.environ.get(spec.get('env', '')) if spec.get('env') else None
        choices = spec.get('paths_by_platform', {}).get(sys.platform, [spec['path']])
        if isinstance(choices, str):
            choices = [choices]
        if not isinstance(choices, list) or not choices or any(not isinstance(p, str) for p in choices):
            raise Stop('file requirements need nonempty platform path strings')
        paths = [Path(os.path.expandvars(p)).expanduser() for p in ([override] if override else choices)]
        path = next((p for p in paths if p.exists()), paths[0])
        rows.append({'mode': spec.get('mode', 'default'), 'file': str(path),
                     'optional': bool(spec.get('optional')), 'status': 'FOUND' if path.exists() else 'MISSING',
                     'level': 'file-existence-only'})
    return rows


def runtime_checks(path, record, config):
    rows = []
    for spec in record.get('requirements', {}).get('python', []):
        skipped = applicability(spec, config)
        if skipped:
            rows.append({'mode': spec.get('mode', 'default'), 'optional': bool(spec.get('optional')), **skipped})
            continue
        selected = spec.get('interpreter_by_platform', {}).get(sys.platform, spec['interpreter'])
        interpreter = path / relative(selected)
        row = {'mode': spec.get('mode', 'default'), 'interpreter': str(interpreter),
               'level': 'installed-distribution-metadata-only', 'optional': bool(spec.get('optional'))}
        if not interpreter.is_file():
            row.update(status='MISSING', missing=spec['distributions'])
        else:
            script = '''import importlib.metadata as m,json,sys
out={}
for name in json.loads(sys.argv[1]):
 try: out[name]=m.version(name)
 except m.PackageNotFoundError: out[name]=None
print(json.dumps(out))
'''
            try:
                result = subprocess.run([str(interpreter), '-c', script, json.dumps(spec['distributions'])],
                                        capture_output=True, text=True, timeout=10)
                versions = json.loads(result.stdout) if result.returncode == 0 else {}
                missing = [name for name in spec['distributions'] if not versions.get(name)]
                row.update(status='MISSING' if missing else 'FOUND', versions=versions, missing=missing)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                row.update(status='UNVERIFIED')
        rows.append(row)
    return rows


class JsonLinesClient:
    """A bounded, owned subprocess; stdout contains only protocol messages."""
    def __init__(self, command, cwd):
        self.process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.messages = queue.Queue()
        self.buffer = []
        def reader():
            try:
                for line in self.process.stdout:
                    if len(line) > 16 * 1024 * 1024:
                        break
                    try:
                        self.messages.put(json.loads(line))
                    except (ValueError, UnicodeDecodeError):
                        continue
            finally:
                self.messages.put(None)
        threading.Thread(target=reader, daemon=True).start()

    def send(self, message):
        self.process.stdin.write((json.dumps(message) + '\n').encode())
        self.process.stdin.flush()

    def receive(self, predicate, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if message is None:
                raise Stop('native catalog process ended before discovery completed')
            if predicate(message):
                return message
            if message.get('type') == 'control_request' or ('method' in message and 'id' in message):
                raise Stop('native discovery requested an interaction; no approval was supplied')
        raise Stop('native catalog discovery timed out')

    def close(self):
        if self.process.stdin:
            self.process.stdin.close()
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        if self.process.stdout:
            self.process.stdout.close()


def codex_catalog(config, instance=None):
    client = JsonLinesClient(agent_adapters.command(config, 'codex', ('app-server',), instance), ROOT)
    try:
        client.send({'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'agentassets_doctor', 'version': '1.0'}}})
        response = client.receive(lambda m: m.get('id') == 1)
        if 'error' in response:
            raise Stop('Codex catalog initialization was rejected')
        client.send({'method': 'initialized', 'params': {}})
        client.send({'id': 2, 'method': 'skills/list', 'params': {
            'cwds': [str(ROOT)], 'forceReload': True}})
        response = client.receive(lambda m: m.get('id') == 2)
        if 'error' in response:
            raise Stop('Codex skills/list was rejected')
        data = response.get('result', {}).get('data', [])
        rows, errors = [], []
        for result in data:
            errors.extend(result.get('errors', []))
            for s in result.get('skills', []):
                rows.append({'name': s['name'], 'path': s.get('path'),
                             'visible': s.get('enabled', True), 'scope': s.get('scope')})
        return {'method': 'app-server skills/list', 'skills': rows,
                'native_parse_errors': len(errors), 'model_turns_sent': 0}
    finally:
        client.close()


def claude_catalog(config, instance=None):
    client = JsonLinesClient(agent_adapters.command(config, 'claude', ('--print', '--input-format', 'stream-json',
                             '--output-format', 'stream-json', '--verbose',
                             '--no-session-persistence'), instance), ROOT)
    try:
        client.send({'type': 'control_request', 'request_id': 'agentassets-init',
                     'request': {'subtype': 'initialize'}})
        message = client.receive(lambda m: m.get('type') == 'control_response' and
                                 m.get('response', {}).get('request_id') == 'agentassets-init')
        response = message['response']
        if response.get('subtype') != 'success':
            raise Stop('Claude native catalog initialization was rejected')
        data = response.get('response', {})
        rows = [{'name': c['name'], 'visible': True} for c in data.get('commands', [])]
        return {'method': 'SDK initialize command catalog', 'skills': rows,
                'native_parse_errors': 0, 'model_turns_sent': 0,
                'path_resolution': 'command name plus independently checked native file'}
    finally:
        client.close()


def hermes_catalog(config, instance=None):
    row = agent_adapters.endpoint(config, {'agent': 'hermes', 'instance': instance} if instance else {'agent': 'hermes'})
    runtime = Path(row.get('runtime') or Path(row['skills_root']).parent / 'hermes-agent')
    interpreter = Path(row.get('interpreter') or runtime / ('venv/Scripts/python.exe' if os.name == 'nt' else 'venv/bin/python'))
    if not interpreter.is_file():
        raise Stop('Hermes native interpreter is unavailable')
    script = '''import json,sys,os
sys.dont_write_bytecode=True
os.environ['HERMES_HOME']=sys.argv[2]
sys.path.insert(0,sys.argv[1])
from tools.skills_tool import _skill_catalog
rows=[{"name":s.get("load_name"),"declared_name":s["name"],"path":str(s["path"]),
       "visible":bool(s.get("load_name")),"category":s.get("category"),"status":s.get("status")}
      for s in _skill_catalog(skip_disabled=False)]
print(json.dumps(rows))
'''
    result = subprocess.run([str(interpreter), '-c', script, str(runtime), str(Path(row['skills_root']).parent)], cwd=ROOT,
                            capture_output=True, text=True, encoding='utf-8', timeout=25)
    if result.returncode:
        raise Stop('Hermes native catalog query failed')
    return {'method': 'native _skill_catalog (no skill_view)',
            'skills': json.loads(result.stdout), 'native_parse_errors': 0, 'model_turns_sent': 0}


def discover(target, config, instance=None):
    try:
        result = ({'codex': codex_catalog, 'claude': claude_catalog, 'hermes': hermes_catalog}[target](config, instance)
                  if target in ('codex', 'claude', 'hermes') else agent_adapters.json_catalog(config, target, instance))
        result.update(status='PASS', target=target, instance=instance or target)
        return result
    except (Stop, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        # Do not persist raw stderr, protocol frames, auth configuration or values.
        return {'status': 'UNVERIFIED', 'target': target, 'reason': str(error), 'skills': [],
                'model_turns_sent': 0}


def audit(args, native=False):
    config = cfg()
    manifest, owners = registry()
    baselines = load(LOCAL / 'baselines.json', {})
    issues = contracts(manifest)
    references, reference_issues = reference_checks(manifest)
    issues.extend(reference_issues)
    packages, copies = [], []
    selected = tuple(dict.fromkeys(row['kind'] for row in agent_adapters.configured(config)
                                  if row['kind'] in targets(args.target) and
                                  (not getattr(args, 'instance', None) or row['id'] == args.instance)))
    trials = {}
    for path in (LOCAL / 'trials').glob('*/trial.json'):
        trial = load(path)
        if trial.get('state') in ACTIVE_TRIAL_STATES:
            trials.update({key(b): trial for b in trial.get('bindings', [])})
            if trial.get('state') in {'preparing', 'rolling_back', 'interrupted'}:
                issues.append({'trial_id': trial['id'], 'code': 'TRIAL_RECOVERY_REQUIRED', 'severity': 'error'})
    for ident, record in sorted(manifest['packages'].items(), key=lambda p: p[1]['name']):
        item = {'skill_id': ident, 'name': record['name'], 'category': record.get('category'),
                'kind': record['origin']['kind'], 'bindings': record['bindings'],
                'requirements': command_checks(record, config), 'canonical_sha256': record['sha256']}
        item['mcp_declarations'] = record.get('requirements', {}).get('mcp', [])
        for requirement in item['requirements']:
            if requirement['status'] == 'MISSING':
                issues.append({'skill': record['name'], 'mode': requirement['mode'],
                               'code': 'RUNTIME_REQUIREMENT_MISSING',
                               'severity': 'warning' if requirement['optional'] else 'error'})
        try:
            package(ROOT, record)
            fields = entry_fields(ROOT / record['path'] / 'SKILL.md')
            item.update(canonical='PASS', discovery_name=fields['name'])
            if fields['name'] != record['name']:
                item['name_alias'] = True
        except (Stop, OSError, ValueError) as error:
            item.update(canonical='FAIL', reason=str(error))
            issues.append({'skill': record['name'], 'code': 'CANONICAL_INVALID', 'severity': 'error'})
        packages.append(item)
        stable, _, stable_commit = effective(ident, record, ROOT)
        expected_record = stable or record
        for binding in [b for logical in record['bindings'] for b in agent_adapters.expand(config, logical)]:
            if binding['agent'] not in selected:
                continue
            if getattr(args, 'instance', None) and binding['instance'] != args.instance:
                continue
            row = {'skill_id': ident, 'name': record['name'], 'target': binding['agent'],
                   'instance': binding['instance'], 'binding': key(binding), 'expected_sha256': expected_record['sha256']}
            try:
                _, path = native_path(config, binding)
                row['path'] = str(path)
                row['runtime'] = runtime_checks(path, expected_record, config)
                for runtime in row['runtime']:
                    if runtime['status'] not in ('FOUND', 'NOT_APPLICABLE'):
                        issues.append({'skill': record['name'], 'target': binding['agent'],
                                       'mode': runtime['mode'], 'code': 'PYTHON_RUNTIME_NOT_READY',
                                       'severity': 'warning' if runtime['optional'] or not path.exists() else 'error'})
                actual = tree_hash(read_native(config, binding, expected_record, baselines)) if path.exists() else None
                row['actual_sha256'] = actual
                base = baselines.get(key(binding), {}).get('sha256')
                trial = trials.get(key(binding))
                if trial and actual == trial['sha256']:
                    row.update(status='TRIAL', trial_id=trial['id'], expected_sha256=trial['sha256'])
                elif stable is None:
                    row['status'] = 'NOT_RELEASED'
                elif actual == expected_record['sha256'] and not path.is_symlink():
                    row['status'] = 'MATCH'
                elif actual is None:
                    row['status'] = 'MISSING' if base else 'INSTALL_PENDING'
                elif actual == base:
                    row['status'] = 'UPDATE_PENDING'
                else:
                    row['status'] = 'CONFLICT'
                if row['status'] in ('MISSING', 'CONFLICT'):
                    issues.append({'skill': record['name'], 'target': binding['agent'],
                                   'code': row['status'], 'severity': 'error'})
            except (Stop, OSError, ValueError) as error:
                row.update(status='ERROR', reason=str(error))
                issues.append({'skill': record['name'], 'target': binding['agent'],
                               'code': 'COPY_CHECK_FAILED', 'severity': 'error'})
            copies.append(row)
    sources, builtin = inventory(config)
    unregistered = [dict(source=source['source'], bindings=[b for b in source['bindings'] if b['agent'] + ':' + b['path'] not in owners])
                    for source in sources if any(b['agent'] + ':' + b['path'] not in owners for b in source['bindings'])]
    native_results = {r['id']: discover(r['kind'], config, r['id']) for r in agent_adapters.configured(config)
                      if r['kind'] in selected and (not getattr(args, 'instance', None) or r['id'] == args.instance)} if native else {}
    if native and args.target != 'all' and not selected:
        native_results[args.target] = {'status': 'UNVERIFIED', 'target': args.target, 'skills': [],
            'reason': 'Agent is not enabled on this computer', 'model_turns_sent': 0}
    for instance, result in native_results.items():
        target = result['target']
        if result.get('native_parse_errors'):
            issues.append({'target': target, 'code': 'NATIVE_PARSE_ERRORS', 'severity': 'error'})
        for row in (r for r in copies if r['instance'] == instance):
            if row['status'] in ('NOT_RELEASED', 'INSTALL_PENDING'):
                row['discovery'] = 'NOT_INSTALLED'
                continue
            if result['status'] != 'PASS':
                row['discovery'] = 'UNVERIFIED'
                continue
            expected = (entry_fields(Path(row['path']) / 'SKILL.md')['name'] if row['status'] in ('MATCH', 'TRIAL') else
                        next(p.get('discovery_name', p['name']) for p in packages if p['skill_id'] == row['skill_id']))
            # Native namespaces may change a load name without changing its file.
            # When paths exist, identify the exact registered file first.
            if any(s.get('path') for s in result['skills']):
                found = [s for s in result['skills'] if s.get('visible') and s.get('path') and
                         (Path(s['path']) if Path(s['path']).name == 'SKILL.md' else
                          Path(s['path']) / 'SKILL.md').resolve() ==
                         (Path(row['path']) / 'SKILL.md').resolve()]
            else:
                found = [s for s in result['skills'] if s['name'] == expected and s.get('visible')]
            row['discovery'] = 'VISIBLE' if found else 'NOT_VISIBLE'
            row['native_names'] = sorted({s['name'] for s in found})
            if not found:
                issues.append({'skill': row['name'], 'target': target, 'code': 'NOT_VISIBLE', 'severity': 'error'})
    mcp = skill_mcp.check(target=args.target, instance=getattr(args, 'instance', None), live=True) if getattr(args, 'mcp', False) else None
    if mcp:
        issues.extend({'skill': r['skill'], 'target': r['instance'], 'service': r['service'],
                       'mode': r['mode'], 'code': 'MCP_' + r['status'],
                       'severity': 'warning' if r['optional'] else 'error'}
                      for r in mcp['checks'] if not r['ready'])
    errors = sum(i['severity'] == 'error' for i in issues)
    incomplete = any(r['status'] != 'PASS' for r in native_results.values())
    pending = any(r['status'] in ('INSTALL_PENDING', 'UPDATE_PENDING') for r in copies)
    status = 'BLOCKED' if errors else 'UNVERIFIED' if incomplete else 'PENDING' if pending else 'PASS'
    result = {'status': status, 'canonical_packages': len(packages),
              'packages': packages, 'copies': copies, 'issues': issues, 'references': references,
              'unregistered_native_packages': unregistered, 'native_builtin_excluded': builtin,
              'local_exclusions': [{'binding': k, 'management': 'local-only'} for k in config.get('excluded_bindings', {})],
              'claude_legacy_commands': legacy_commands(config), 'native_discovery': native_results,
              'mcp_checked': mcp is not None, 'mcp': mcp,
              'inactive_agent_kinds': sorted(set(TARGETS) - {r['kind'] for r in agent_adapters.configured(config)}),
              'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'limits': 'File identity, metadata, declarations and optional native catalogs; MCP live checks require --mcp. No model/business tool is executed or native config changed.'}
    if args.output:
        save(Path(args.output).expanduser().resolve(), result)
    return result


def index(args):
    if not args.reviewed:
        raise Stop('review the catalog metadata, then pass --reviewed')
    manifest, _ = registry()
    if any(i['severity'] == 'error' for i in contracts(manifest)):
        raise Stop('catalog contracts have errors; run audit and reconcile first')
    categories = load(ROOT / 'skills/categories.json', {})
    lines = ['# 个人服务技能索引', '',
             '本库维护个人背景、服务环境与操作能力。按任务选择入口和参考；具体主机、路径与领域背景保留在所属包中。',
             '来源、服务领域与目标支持分别登记。下列端表示期望分发绑定，不等于业务操作已全部验收；用 audit / doctor 核对本机文件、依赖与原生可见性。',
             '内置/插件及来源待定的旧命令由原客户端管理，不自动入库。', '']
    for category in list(categories) + ['uncategorized']:
        members = [r for r in manifest['packages'].values() if r.get('category', 'uncategorized') == category]
        if not members:
            continue
        label = categories.get(category, {}).get('label', category)
        lines.extend(['## ' + label, '', '| 技能 | 服务用途 | 来源 | 登记目标 | 条件依赖 |',
                      '| --- | --- | --- | --- | --- |'])
        for record in sorted(members, key=lambda p: p['name']):
            path = record['path'].removeprefix('skills/') + '/SKILL.md'
            summary = record.get('summary') or entry_fields(ROOT / record['path'] / 'SKILL.md')['description']
            deps = '; '.join(d['skill'] + ('（' + d['when'] + '）' if d.get('when') else '')
                             for d in record.get('dependencies', [])) or '—'
            agents = ', '.join(b['agent'] for b in record['bindings']) or '未分发'
            lines.append('| [' + record['name'] + '](' + path + ') | ' + summary.replace('|', '\\|') +
                         ' | ' + record['origin']['kind'] + ' | ' + agents + ' | ' + deps + ' |')
        lines.append('')
    mcp_rows = [(r, s) for r in manifest['packages'].values() for s in skill_mcp.specs(r)]
    if mcp_rows:
        lines.extend(['## MCP 工具依赖', '',
                      '下表只声明能力；本机连接与凭据不随技能分发。普通 audit 不连接 MCP；显式 `mcp check --live` 或 `doctor --mcp` 验证。', '',
                      '| 技能 | 服务 / 模式 | 接入 | 上游工具 | 范围 |', '| --- | --- | --- | --- | --- |'])
        for r, s in sorted(mcp_rows, key=lambda pair: (pair[0]['name'], pair[1]['service'], pair[1]['mode'])):
            lines.append('| ' + r['name'] + ' | ' + s['service'] + ' / ' + s['mode'] + ' | ' + s['via'] +
                         ' | ' + ', '.join(s['tools']) + ' | ' + ('可选模式' if s['optional'] else '必需') + ' |')
        lines.append('')
    lines.extend(['## 定位与核验', '',
                  '`aak resolve --skill <名称> --target <agent>` 返回真实路径与当前副本状态。',
                  '`audit` 检查登记、文件、引用、依赖和未纳管入口；`doctor --native` 进一步读取本机已启用 Agent 的原生目录，不创建模型回合。',
                  '迁移与兼容映射见 migrations/；原生脚本与 cron 的旧路径保留条件由相应迁移记录说明。', ''])
    aliases = [(a, r['name']) for r in manifest['packages'].values() for a in r.get('legacy_names', [])]
    if aliases:
        lines.extend(['## 历史调用名称', '', '| 历史名称 | 现维护入口 |', '| --- | --- |'])
        lines.extend('| ' + old + ' | ' + new + ' |' for old, new in sorted(aliases))
        lines.extend(['', '历史名称只用于 resolve 定位；不恢复已合并的旧包。', ''])
    path = ROOT / 'skills/README.md'
    atomic(path, ('\n'.join(lines)).encode(), 0o644)
    return {'indexed': len(manifest['packages']), 'path': str(path)}


def resolve(args):
    manifest, _ = registry()
    matches = [(i, r) for i, r in manifest['packages'].items()
               if i == args.skill or r['name'] == args.skill or args.skill in r.get('legacy_names', [])]
    if len(matches) != 1:
        raise Stop('skill must resolve to exactly one registered identity')
    ident, record = matches[0]
    config = cfg()
    bindings = [b for logical in record['bindings'] if logical['agent'] == args.target
                for b in agent_adapters.expand(config, logical)
                if not getattr(args, 'instance', None) or b['instance'] == args.instance]
    paths = []
    for binding in bindings:
        _, path = native_path(config, binding)
        digest = tree_hash(read_native(config, binding, record)) if path.exists() else None
        selected, _, _ = effective(ident, record, ROOT)
        paths.append({'path': str(path), 'instance': binding['instance'],
                      'matches_registered': digest == (selected or record)['sha256'] and not path.is_symlink()})
    return {'skill_id': ident, 'name': record['name'], 'matched_alias': args.skill in record.get('legacy_names', []),
            'canonical_path': str(ROOT / record['path']),
            'target': args.target, 'native_paths': paths}
