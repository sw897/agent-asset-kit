"""Read-only skill contracts, native discovery and generated navigation.

Native discovery only initializes clients and reads their catalogs. It never sends
a user prompt, creates a model turn, calls a business tool or approves a request.
"""

import json
import os
from pathlib import Path
import posixpath
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import unquote

from .assetlib import (ROOT, LOCAL, Stop, atomic, cfg, files_in, inventory, key,
                      load, native_path, package, registry, relative, save, tree_hash, read_native, ACTIVE_TRIAL_STATES,
                      git, portable_paths, canonical_path)
from . import agent_adapters
from . import skill_dependencies
from .skill_versions import effective

TARGETS = agent_adapters.KINDS
SKILL_CALL = re.compile(
    r'''skill_view\(\s*(?:name\s*=\s*)?["']([^"']+)["']'''
    r'''(?:\s*,\s*(?:file_path\s*=\s*)?["']([^"']+)["'])?''')


MARKDOWN_LINK = re.compile(
    r'\[[^\]\n]*\]\([ \t]*(?:<([^<>\r\n]+)>|((?:\\[()]|[^\\\s()<>]|\([^()\r\n]*\))+))'
    r"""(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'|\([^()\n]*\)))?[ \t]*\)""")
RUNTIME_KEYS = ('commands', 'files', 'python')
PLATFORMS = ('win32', 'darwin', 'linux')


def entry_fields(path):
    return parse_entry_fields(path.read_text(encoding='utf-8'))


def parse_entry_fields(text):
    """Extract index scalars, not a substitute for a client's YAML parser."""
    lines = text.splitlines()
    if not lines or lines[0] != '---':
        raise Stop('frontmatter must start on the first line')
    end = next((i for i, line in enumerate(lines[1:], 1) if line.rstrip() == '---'), None)
    if end is None:
        raise Stop('frontmatter closing marker is missing')
    fields = {}
    for i, line in enumerate(lines[1:end], 1):
        match = re.match(r'^(name|description):\s*(.*)$', line)
        if not match:
            continue
        value = match[2].strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in '"\'':
            fields[match[1]] = value[1:-1]
            continue
        parts = [] if re.fullmatch(r'[|>][+-]?', value) or not value else [value]
        for row in lines[i + 1:end]:
            if row and not row[0].isspace():
                break
            if row.strip():
                parts.append(row.strip())
        fields[match[1]] = ' '.join(parts)
    if not fields.get('name') or not fields.get('description'):
        raise Stop('name and description are required for discovery')
    return fields


def targets(value):
    return TARGETS if value == 'all' else (value,)


def contracts(manifest):
    by_name = {r['name']: r for r in manifest['packages'].values()}
    issues = skill_dependencies.contracts(manifest)
    aliases = {}
    categories = load(ROOT / 'skills/categories.json', {})
    for r in manifest['packages'].values():
        category = r.get('category')
        if category and category not in categories:
            issues.append({'skill': r['name'], 'code': 'UNKNOWN_CATEGORY', 'severity': 'error'})
        for kind in RUNTIME_KEYS:
            _, errors = runtime_specs(r, kind)
            issues.extend({'skill': r['name'], 'requirement': kind, 'reason': error,
                           'code': 'INVALID_RUNTIME_REQUIREMENT', 'severity': 'error'} for error in errors)
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


def packaged_link_target(record, source, destination):
    target = posixpath.join(posixpath.dirname(source), destination)
    resolved = posixpath.normpath(target)
    if resolved.startswith('/'):
        return None
    if resolved != '..' and not resolved.startswith('../'):
        return resolved

    # A link can reenter its own package by name. Check that it does so
    # in the canonical directory and under every registered native alias.
    folders = [record['path'], *(binding['path'] for binding in record['bindings'])]
    paths = set()
    for folder in folders:
        folder = posixpath.normpath(folder)
        path = posixpath.normpath(posixpath.join(folder, target))
        if path == folder:
            paths.add('.')
        elif path.startswith(folder + '/'):
            paths.add(path[len(folder) + 1:])
        else:
            return None
    return paths.pop() if len(paths) == 1 else None


def reference_checks(manifest):
    packages = {record['name']: set() for record in manifest['packages'].values()}
    rows, issues, skill_calls = [], [], []
    placeholders = {'url', 'path', '链接', '相对路径', '相对路径.png'}
    for record in manifest['packages'].values():
        skill = record['name']
        try:
            files = files_in(canonical_path(ROOT, record))
        except (Stop, OSError, ValueError):
            # The canonical package check reports the problem for this skill.
            continue
        packages[skill] = set(files)
        directories = {parent.as_posix() for name in files for parent in Path(name).parents}
        for name, (raw, _) in files.items():
            if not name.endswith('.md'):
                continue
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                issues.append({'skill': skill, 'file': name, 'code': 'INVALID_MARKDOWN_ENCODING',
                               'severity': 'error'})
                continue
            # Sample output templates and fenced syntax are not live file links.
            prose = re.sub(r'^(`{3,}|~{3,}).*?^\1\s*$', '', text, flags=re.M | re.S)
            link_prose = re.sub(r'(`+)[^`\n]*?\1', '', prose)
            for match in MARKDOWN_LINK.finditer(link_prose):
                dest = (match[1] or match[2]).split('#', 1)[0]
                if (not dest or re.match(r'^[a-z][a-z0-9+.-]*:', dest, re.I) or dest.startswith(('/', '~'))
                        or any(c in dest for c in '<>*|{}…') or dest.lower() in placeholders or '...' in dest):
                    continue
                literal = re.sub(r'\\([()])', r'\1', unquote(dest))
                resolved = packaged_link_target(record, name, literal)
                row = {'skill': skill, 'file': name, 'reference': dest}
                if resolved is None:
                    status, code = 'OUT_OF_PACKAGE', 'OUT_OF_PACKAGE_REFERENCE'
                elif resolved not in files and resolved not in directories:
                    status, code = 'MISSING', 'MISSING_FILE_REFERENCE'
                else:
                    status, code = 'PASS', None
                rows.append(dict(row, status=status))
                if code:
                    issues.append(dict(row, code=code, severity='error'))
            for match in SKILL_CALL.finditer(prose):
                dep, dest = match.groups()
                if not dest or any(c in dest for c in '<>*{}'):
                    continue
                row = {'skill': skill, 'file': name, 'dependency': dep, 'reference': dest}
                try:
                    ref = relative(dest).as_posix()
                except Stop:
                    rows.append(dict(row, status='INVALID'))
                    issues.append(dict(row, code='INVALID_SKILL_REFERENCE', severity='error'))
                    continue
                rows.append(row)
                skill_calls.append((row, ref))
    for row, ref in skill_calls:
        dep = row['dependency']
        if dep not in packages:
            row['status'] = 'NATIVE_OR_EXTERNAL_UNVERIFIED'
        elif ref in packages[dep]:
            row['status'] = 'PASS'
        else:
            row['status'] = 'MISSING'
            issues.append(dict(row, code='MISSING_SKILL_REFERENCE', severity='error'))
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


def nonempty_string(value):
    return isinstance(value, str) and bool(value.strip()) and '\0' not in value


def validate_runtime_spec(kind, spec):
    if not isinstance(spec, dict):
        raise Stop('each requirement must be an object')
    if 'mode' in spec and not nonempty_string(spec['mode']):
        raise Stop('mode must be a nonempty string')
    if 'optional' in spec and type(spec['optional']) is not bool:
        raise Stop('optional must be a boolean')
    platforms = spec.get('platforms', [])
    if not isinstance(platforms, list) or any(p not in PLATFORMS for p in platforms):
        raise Stop('platforms must name supported Python platforms')
    if 'requires_agent' in spec and spec['requires_agent'] not in agent_adapters.KINDS:
        raise Stop('requires_agent must name a known Agent kind')
    if kind == 'commands':
        options = spec.get('any_of')
        if not isinstance(options, list) or not options or not all(map(nonempty_string, options)):
            raise Stop('any_of must be a nonempty list of command names')
        return
    field, mapping = ('path', 'paths_by_platform') if kind == 'files' else (
        'interpreter', 'interpreter_by_platform')
    selections = spec.get(mapping, {})
    if not isinstance(selections, dict) or any(key not in PLATFORMS for key in selections):
        raise Stop(f'{mapping} must map supported platforms to paths')
    if field not in spec and not selections:
        raise Stop(f'provide {field} or {mapping}')
    values = list(selections.values())
    if field in spec:
        values.append(spec[field])
    if kind == 'files':
        if 'env' in spec and not nonempty_string(spec['env']):
            raise Stop('env must name an environment variable')
        for value in values:
            choices = [value] if isinstance(value, str) else value
            if not isinstance(choices, list) or not choices or not all(map(nonempty_string, choices)):
                raise Stop('file paths must be a path string or a nonempty list of path strings')
    else:
        if not all(map(nonempty_string, values)):
            raise Stop('interpreter paths must be nonempty relative path strings')
        for value in values:
            relative(value)
        distributions = spec.get('distributions')
        if not isinstance(distributions, list) or not all(map(nonempty_string, distributions)):
            raise Stop('distributions must be a list of names')


def runtime_specs(record, kind):
    declared = record.get('requirements', {}).get(kind, [])
    if not isinstance(declared, list):
        return [], [f'requirements.{kind} must be a list']
    valid, errors = [], []
    for spec in declared:
        try:
            validate_runtime_spec(kind, spec)
        except Stop as error:
            errors.append(str(error))
        else:
            valid.append(spec)
    return valid, errors


def applicability(spec, config):
    platforms = spec.get('platforms', [])
    if platforms and sys.platform not in platforms:
        return {'status': 'NOT_APPLICABLE', 'reason': 'mode is limited to other platforms',
                'platforms': platforms}
    required = spec.get('requires_agent')
    if required and not any(row['kind'] == required for row in agent_adapters.configured(config)):
        return {'status': 'NOT_APPLICABLE', 'reason': 'mode requires an enabled local Agent',
                'requires_agent': required}
    return None


def requirement_row(spec):
    return {'mode': spec.get('mode', 'default'), 'optional': spec.get('optional', False)}


def command_checks(record, config):
    rows = []
    for spec in runtime_specs(record, 'commands')[0]:
        options = spec['any_of']
        row = dict(requirement_row(spec), any_of=options)
        skipped = applicability(spec, config)
        if skipped:
            rows.append(dict(row, **skipped))
            continue
        found = next(filter(None, map(shutil.which, options)), None)
        rows.append(dict(row, status='FOUND' if found else 'MISSING',
                         resolved_path=found, level='executable-location-only'))
    for spec in runtime_specs(record, 'files')[0]:
        row = requirement_row(spec)
        skipped = applicability(spec, config)
        if skipped:
            rows.append(dict(row, **skipped))
            continue
        override = os.environ.get(spec.get('env', ''))
        choices = override or spec.get('paths_by_platform', {}).get(sys.platform, spec.get('path'))
        if choices is None:
            rows.append(dict(row, status='NOT_APPLICABLE', reason='no path for this platform'))
            continue
        if isinstance(choices, str):
            choices = [choices]
        paths = [Path(os.path.expandvars(p)).expanduser() for p in choices]
        found = next((p for p in paths if p.exists()), None)
        rows.append(dict(row, file=str(found or paths[0]), status='FOUND' if found else 'MISSING',
                         level='file-existence-only'))
    return rows


def runtime_checks(path, record, config):
    rows = []
    for spec in runtime_specs(record, 'python')[0]:
        row = requirement_row(spec)
        skipped = applicability(spec, config)
        if skipped:
            rows.append(dict(row, **skipped))
            continue
        selected = spec.get('interpreter_by_platform', {}).get(sys.platform, spec.get('interpreter'))
        if selected is None:
            rows.append(dict(row, status='NOT_APPLICABLE', reason='no interpreter for this platform'))
            continue
        interpreter = path / relative(selected)
        row.update(interpreter=str(interpreter), level='installed-distribution-metadata-only')
        if not interpreter.is_file():
            row.update(status='MISSING', missing=spec['distributions'])
        else:
            script = """import importlib.metadata as m,json,sys
out={}
for name in json.loads(sys.argv[1]):
 try: out[name]=m.version(name)
 except m.PackageNotFoundError: out[name]=None
print(json.dumps(out))
"""
            try:
                result = subprocess.run([str(interpreter), '-c', script, json.dumps(spec['distributions'])],
                                        capture_output=True, text=True, timeout=10)
                versions = json.loads(result.stdout) if result.returncode == 0 else {}
                if not isinstance(versions, dict):
                    raise ValueError('invalid interpreter response')
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
        def reader():
            try:
                for line in self.process.stdout:
                    if len(line) > 16 * 1024 * 1024:
                        break
                    try:
                        self.messages.put(json.loads(line))
                    except (ValueError, UnicodeDecodeError):
                        continue
            except (OSError, ValueError):
                pass
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
    library_only = getattr(args, 'library_only', False)
    if library_only and (native or args.target != 'all' or getattr(args, 'instance', None)):
        raise Stop('a library-only audit cannot select local Agent targets or instances')
    config = {'agents': {}} if library_only else cfg()
    if library_only and (ROOT / '.git').exists():
        portable_paths(filter(None, git('ls-files', '-z').stdout.split('\0')))
    manifest, owners = registry()
    baselines = {} if library_only else load(LOCAL / 'baselines.json', {})
    issues = contracts(manifest)
    references, reference_issues = reference_checks(manifest)
    issues.extend(reference_issues)
    packages, copies = [], []
    selected = tuple(dict.fromkeys(row['kind'] for row in agent_adapters.configured(config)
                                  if row['kind'] in targets(args.target) and
                                  (not getattr(args, 'instance', None) or row['id'] == args.instance)))
    trials = {}
    for path in ([] if library_only else (LOCAL / 'trials').glob('*/trial.json')):
        trial = load(path)
        if trial.get('state') in ACTIVE_TRIAL_STATES:
            trials.update({key(b): trial for b in trial.get('bindings', [])})
            if trial.get('state') in {'preparing', 'rolling_back', 'interrupted'}:
                issues.append({'trial_id': trial['id'], 'code': 'TRIAL_RECOVERY_REQUIRED', 'severity': 'error'})
    for ident, record in sorted(manifest['packages'].items(), key=lambda p: p[1]['name']):
        item = {'skill_id': ident, 'name': record['name'], 'category': record.get('category'),
                'kind': record['origin']['kind'], 'bindings': record['bindings'],
                'requirements': [] if library_only else command_checks(record, config), 'canonical_sha256': record['sha256']}
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
        try:
            stable, _, stable_commit = effective(ident, record, ROOT)
        except (Stop, OSError, ValueError):
            if item['canonical'] == 'FAIL':
                continue
            raise
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
    sources, builtin = ([], []) if library_only else inventory(config)
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
    errors = sum(i['severity'] == 'error' for i in issues)
    incomplete = any(r['status'] != 'PASS' for r in native_results.values())
    pending = any(r['status'] in ('INSTALL_PENDING', 'UPDATE_PENDING') for r in copies)
    status = 'BLOCKED' if errors else 'UNVERIFIED' if incomplete else 'PENDING' if pending else 'PASS'
    result = {'status': status, 'scope': 'library-only' if library_only else 'local',
              'canonical_packages': len(packages),
              'packages': packages, 'copies': copies, 'issues': issues, 'references': references,
              'unregistered_native_packages': unregistered, 'native_builtin_excluded': builtin,
              'local_exclusions': [{'binding': k, 'management': 'local-only'} for k in config.get('excluded_bindings', {})],
              'claude_legacy_commands': legacy_commands(config), 'native_discovery': native_results,
              'inactive_agent_kinds': [] if library_only else sorted(set(TARGETS) - {r['kind'] for r in agent_adapters.configured(config)}),
              'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'limits': ('Tracked path portability, reviewed package identity, immutable stable releases, metadata, references and dependency declarations only. '
                         'Local configuration, runtime requirements and native clients are not checked.' if library_only else
                         'File identity, metadata, dependency declarations and optional native skill catalogs. MCP declarations are validated offline; service availability is not checked. No model/business tool is executed or native config changed.')}
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
    mcp_rows = [(r, s) for r in manifest['packages'].values() for s in skill_dependencies.mcp_specs(r)]
    if mcp_rows:
        lines.extend(['## MCP 工具依赖', '',
                      '下表声明技能所需能力；audit 只校验声明和包内引用。服务可用性由使用者在目标环境通过客户端或外部工具核验，连接与凭据由对应工具管理。', '',
                      '| 技能 | 服务 / 模式 | 使用方式 | 所需工具 | 条件 |', '| --- | --- | --- | --- | --- |'])
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
