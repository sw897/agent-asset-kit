"""Declared MCP capabilities and explicit mcplink transactions.

No imports, login, tool calls or native writes occur during skill deployment.
Connection IDs, source IDs and transaction receipts live only under LOCAL.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from .assetlib import ROOT, LOCAL, Stop, cfg, load, relative, registry, save, sha
from . import agent_adapters

LOCAL_BINDINGS = LOCAL / 'mcp-bindings.json'
FIELDS = {'service', 'mode', 'via', 'tools', 'optional', 'reference'}
KINDS = {'deepseekharness': 'deepseek'}


def identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}', value))


def alias_name(value):
    # Native config keys are not CLI identifiers; preserve Unicode aliases.
    return isinstance(value, str) and 0 < len(value) <= 200 and all(ord(c) >= 32 and ord(c) != 127 for c in value)


def specs(record):
    values = record.get('requirements', {}).get('mcp', [])
    if not isinstance(values, list):
        raise Stop('requirements.mcp must be a list')
    seen = set()
    for spec in values:
        if not isinstance(spec, dict) or set(spec) != FIELDS:
            raise Stop('MCP declarations require only service, mode, via, tools, optional and reference')
        if (not identifier(spec['service']) or not identifier(spec['mode']) or
                spec['via'] not in ('native', 'cli') or type(spec['optional']) is not bool):
            raise Stop('invalid MCP service, mode, via or optional flag')
        tools = spec['tools']
        if not isinstance(tools, list) or not tools or any(not identifier(t) for t in tools) or len(tools) != len(set(tools)):
            raise Stop('MCP tools must be a nonempty list of distinct exact upstream names')
        identity = (spec['service'], spec['mode'])
        if identity in seen:
            raise Stop('duplicate MCP service/mode declaration')
        seen.add(identity)
        if not isinstance(spec['reference'], str):
            raise Stop('MCP reference must be a relative file path')
        ref = relative(spec['reference'])
        path = ROOT / record['path'] / ref
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to((ROOT / record['path']).resolve()):
            raise Stop('MCP reference must be an existing regular file inside its skill')
    return values


def contracts(manifest):
    issues = []
    for record in manifest['packages'].values():
        try:
            specs(record)
        except (Stop, TypeError, ValueError):
            issues.append({'skill': record['name'], 'code': 'INVALID_MCP_DECLARATION', 'severity': 'error'})
    return issues


def bindings():
    if LOCAL_BINDINGS.is_symlink():
        raise Stop('MCP binding file cannot be a link')
    value = load(LOCAL_BINDINGS, {'version': 1, 'services': {}})
    if (not isinstance(value, dict) or set(value) != {'version', 'services'} or
            type(value['version']) is not int or value['version'] != 1 or not isinstance(value['services'], dict)):
        raise Stop('invalid local MCP binding file')
    for name, row in value['services'].items():
        if (not identifier(name) or not isinstance(row, dict) or not {'connection_id', 'sources'} <= set(row) or
                set(row) - {'connection_id', 'sources', 'native_names'} or
                not identifier(row['connection_id']) or not isinstance(row['sources'], dict) or
                any(not identifier(k) or not identifier(v) for k, v in row['sources'].items()) or
                not isinstance(row.get('native_names', {}), dict) or
                any(k not in row['sources'] or not alias_name(v) for k, v in row.get('native_names', {}).items())):
            raise Stop('local MCP bindings accept only connection_id, sources and optional native_names')
    return value


class Client:
    def __init__(self):
        self.binary = os.environ.get('MCPLINK_BIN') or shutil.which('mcplink')
        if not self.binary and sys.platform == 'darwin':
            self.binary = '/Applications/mcplink.app/Contents/MacOS/mcplink'
        if not self.binary or not Path(self.binary).is_file():
            raise Stop('mcplink CLI is missing; install it or set trusted MCPLINK_BIN')
        schema = self.require('schema')
        commands = {c.get('name') for c in schema.get('command', {}).get('commands', [])}
        if schema.get('schemaVersion') != 1 or not {'status', 'connections', 'changes'} <= commands:
            raise Stop('unsupported mcplink schema; review the installed CLI contract first')

    def request(self, *args, payload=None):
        # Never echo stderr, error.message, endpoints, definitions or upstream text.
        try:
            p = subprocess.run([self.binary, *args], capture_output=True, text=True,
                               encoding='utf-8', timeout=60,
                               input=json.dumps(payload) if payload is not None else None)
            data = json.loads(p.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            raise Stop('mcplink transport/result unavailable; inspect local state before retrying') from error
        ok = p.returncode == 0 and isinstance(data, dict) and isinstance(data.get('ok'), dict)
        result = (data.get('ok') if isinstance(data.get('ok'), dict) else data.get('result', {})) if isinstance(data, dict) else {}
        return ok, result if isinstance(result, dict) else {}

    def require(self, *args, payload=None):
        ok, result = self.request(*args, payload=payload)
        if not ok:
            raise Stop('mcplink operation failed; raw diagnostics suppressed, inspect local state')
        return result


def selected(manifest, skill=None):
    if not skill:
        return list(manifest['packages'].items())
    rows = [(i, r) for i, r in manifest['packages'].items()
            if skill == i or skill == r['name'] or skill in r.get('legacy_names', [])]
    if len(rows) != 1:
        raise Stop('skill must resolve to exactly one registered identity')
    return rows


def source_for(status, config, instance, source_id):
    endpoints = [r for r in agent_adapters.configured(config) if r['id'] == instance]
    sources = [r for r in status.get('sources', []) if r.get('id') == source_id]
    if len(endpoints) != 1 or len(sources) != 1:
        raise Stop('select an enabled local instance and an exact discovered MCP source')
    endpoint, source = endpoints[0], sources[0]
    kind = KINDS.get(endpoint['kind'], endpoint['kind'])
    agents = [r for r in status.get('agents', []) if r.get('id') == kind]
    if (source.get('agent') != kind or source.get('readOnly') or source.get('error') or
            source.get('scope') not in (None, 'user') or source.get('project') is not None or
            not source.get('exists', False) or len(agents) != 1 or
            not agents[0].get('installed') or not agents[0].get('configurable')):
        raise Stop('MCP source must be an installed client writable user source; plugin/project sources stay native')
    # Profile paths must be explicitly registered, never inferred from an alias.
    if instance != endpoint['kind']:
        raise Stop('nondefault Agent profiles need a reviewed source adapter; default-source guessing is forbidden')
    profile = agents[0].get('profilePath')
    path = source.get('path')
    if not isinstance(profile, str) or not profile or not isinstance(path, str) or not path:
        raise Stop('native user profile/source path is unavailable; source scope alone is insufficient')
    profile, path = Path(profile).resolve(), Path(path).resolve()
    if profile != Path(endpoint['skills_root']).resolve().parent:
        raise Stop('mcplink user profile differs from the registered Agent skill profile')
    expected = {
        'codex': [profile / 'config.toml'], 'hermes': [profile / 'config.yaml'],
        'claude': [profile / '.claude.json'],
        'opencode': [profile / 'opencode.json', profile / 'opencode.jsonc'],
        'zcode': [profile / 'cli/config.json'],
    }.get(kind, [])
    if kind == 'claude' and profile == (Path(config['home']) / '.claude').resolve():
        expected.append(Path(config['home']) / '.claude.json')
    if kind == 'deepseek' and path.parent.parent.parent == profile and path.name == 'cordis.patch.yml' and path.parent.parent.name == 'profiles':
        expected.append(path)
    if path not in [p.resolve() for p in expected] or (
            source.get('scope') is None and source_id != kind + ':user'):
        raise Stop('MCP source is not the verified native user configuration; project/manual null-scope files are refused')
    return source


def native_binding(status, binding, instance, source):
    rows = [b for b in status.get('bindings', []) if b.get('sourceId') == source
            and b.get('connectionId') == binding['connection_id']]
    name = binding.get('native_names', {}).get(instance)
    if name:
        rows = [b for b in rows if b.get('name') == name]
    if len(rows) > 1:
        return None, 'NATIVE_AMBIGUOUS'
    if not rows or not alias_name(rows[0].get('name')):
        return None, 'NATIVE_NOT_BOUND'
    return rows[0], None


def managed(status, ident):
    rows = [r for r in status.get('connections', []) if r.get('id') == ident and not r.get('detected')]
    if len(rows) != 1:
        raise Stop('select an exact existing managed connection; no import or account guessing is performed')
    return rows[0]


def bind(args):
    if not args.reviewed:
        raise Stop('review the service, connection/account and source mapping, then pass --reviewed')
    manifest, _ = registry()
    if args.service not in {s['service'] for _, r in manifest['packages'].items() for s in specs(r)}:
        raise Stop('service is not declared by a managed skill')
    if not identifier(args.connection):
        raise Stop('invalid connection identifier')
    status = Client().require('status')
    managed(status, args.connection)
    value = bindings()
    previous = value['services'].get(args.service, {})
    sources = dict(previous.get('sources', {}))
    names = dict(previous.get('native_names', {})) if previous.get('connection_id') == args.connection else {}
    for pair in args.source or []:
        instance, sep, source_id = pair.partition('=')
        if not sep:
            raise Stop('--source must be INSTANCE=EXACT_DISCOVERED_SOURCE_ID')
        source_for(status, cfg(), instance, source_id)
        sources[instance] = source_id
    value['services'][args.service] = {'connection_id': args.connection, 'sources': sources}
    for pair in getattr(args, 'native_name', None) or []:
        instance, sep, name = pair.partition('=')
        if not sep or instance not in sources or not alias_name(name):
            raise Stop('--native-name must select INSTANCE=EXISTING_NATIVE_NAME with a source mapping')
        source_for(status, cfg(), instance, sources[instance])
        names[instance] = name
    if names:
        value['services'][args.service]['native_names'] = names
        for instance in names:
            row, error = native_binding(status, value['services'][args.service], instance, sources[instance])
            if error or row.get('missing'):
                raise Stop('native-name must identify one existing binding for this exact source and managed connection')
    save(LOCAL_BINDINGS, value)
    LOCAL_BINDINGS.chmod(0o600)
    return {'bound': args.service, 'instances': sorted(sources), 'local_only': True,
            'native_configuration_changed': False}


def check(skill=None, target='all', instance=None, mode=None, live=False):
    manifest, _ = registry()
    config, local = cfg(), bindings()
    rows, cache = [], {}
    status = client = None
    for ident, record in selected(manifest, skill):
        for spec in specs(record):
            if mode and spec['mode'] != mode:
                continue
            for logical in record['bindings']:
                for endpoint in agent_adapters.expand(config, logical):
                    if (target != 'all' and endpoint['agent'] != target) or (instance and endpoint['instance'] != instance):
                        continue
                    row = {'skill_id': ident, 'skill': record['name'], 'service': spec['service'],
                           'mode': spec['mode'], 'via': spec['via'], 'tools': spec['tools'],
                           'instance': endpoint['instance'], 'optional': spec['optional'] and not bool(mode),
                           'status': 'UNBOUND', 'ready': False}
                    rows.append(row)
                    binding = local['services'].get(spec['service'])
                    if not binding:
                        continue
                    row['status'] = 'BOUND_UNVERIFIED'
                    if not live:
                        continue
                    if client is None:
                        client = Client()
                        status = client.require('status')
                    paused = status.get('gateway', {}).get('paused')
                    if type(paused) is not bool or paused:
                        row['status'] = 'GATEWAY_PAUSED' if paused is True else 'GATEWAY_UNVERIFIED'
                        continue
                    connection = binding['connection_id']
                    try:
                        conn = managed(status, connection)
                    except Stop:
                        row['status'] = 'CONNECTION_MISSING'
                        continue
                    if conn.get('active') is False:
                        row['status'] = 'CONNECTION_DISABLED'
                        continue
                    if connection not in cache:
                        ok, data = client.request('connections', 'tools', connection)
                        tools = data.get('tools')
                        valid = isinstance(tools, list) and all(isinstance(t, dict) and isinstance(t.get('name'), str) for t in tools)
                        fresh = data.get('connection', {})
                        probe_status = fresh.get('status') if isinstance(fresh, dict) and fresh.get('id') == connection else None
                        if not ok and probe_status is None:
                            # Older/non-ready responses may omit connection metadata.
                            refreshed = client.require('status')
                            current = [c for c in refreshed.get('connections', []) if c.get('id') == connection]
                            probe_status = current[0].get('status') if len(current) == 1 else None
                        cache[connection] = (ok and valid, {t['name'] for t in tools} if valid else set(), probe_status)
                    ok, tools, probe_status = cache[connection]
                    if not ok:
                        row['status'] = 'NEEDS_AUTH' if probe_status == 'needs_auth' else 'PROBE_FAILED'
                        continue
                    missing = sorted(set(spec['tools']) - tools)
                    if missing:
                        row.update(status='TOOLS_MISSING', missing_tools=missing)
                        continue
                    if spec['via'] == 'native':
                        source = binding['sources'].get(endpoint['instance'])
                        try:
                            source_for(status, config, endpoint['instance'], source)
                        except Stop:
                            row['status'] = 'SOURCE_UNMAPPED_OR_UNAVAILABLE'
                            continue
                        native, error = native_binding(status, binding, endpoint['instance'], source)
                        if error:
                            row['status'] = error
                            continue
                        row['native_name'] = native['name']
                        if native.get('missing') or not native.get('enabled'):
                            row['status'] = 'NATIVE_NOT_BOUND'
                            continue
                        if native.get('changed') or native.get('needsUpdate') or native.get('error'):
                            row['status'] = 'NATIVE_CHANGED_OR_STALE'
                            continue
                    row.update(status='READY', ready=True)
    if (mode or instance or target != 'all') and not rows:
        raise Stop('no declared MCP dependency matches the selected mode and enabled target')
    failed = [r for r in rows if not r['ready']]
    state = ('BLOCKED' if any(not r['optional'] for r in failed) else
             'UNVERIFIED' if any(r['status'] == 'BOUND_UNVERIFIED' for r in failed) else
             'WARN' if failed else 'PASS')
    return {'status': state, 'ready': not failed, 'live': live, 'checks': rows,
            'level': 'mcplink tool discovery and native binding inventory' if live else 'local declaration/binding only',
            'limits': 'No business tool calls, login, imports or native writes. Native model adoption is not tested.'}


def connect(args):
    manifest, _ = registry()
    ident, record = selected(manifest, args.skill)[0]
    matches = [s for s in specs(record) if s['service'] == args.service and s['via'] == 'native']
    if not matches:
        raise Stop('no native MCP requirement for this skill/service; CLI access needs only a local binding')
    if not args.instance or len(set(args.instance)) != len(args.instance):
        raise Stop('select distinct local --instance targets explicitly')
    value, config = bindings(), cfg()
    binding = value['services'].get(args.service)
    if not binding:
        raise Stop('bind the reviewed service to a local managed connection first')
    allowed = {b['instance'] for logical in record['bindings'] for b in agent_adapters.expand(config, logical)}
    if set(args.instance) - allowed:
        raise Stop('selected instance is not enabled for this skill')
    signature = sha(json.dumps({'skill': ident, 'specs': matches, 'binding': binding,
                               'instances': args.instance, 'agents': config.get('agents')}, sort_keys=True).encode())
    if args.plan and not re.fullmatch(r'[a-f0-9]{64}', args.plan):
        raise Stop('invalid MCP preview plan ID')
    plan_path = LOCAL / 'mcp-plans' / (args.plan + '.json') if args.plan else None
    plan = load(plan_path) if plan_path else None
    if args.apply:
        if not args.reviewed or not plan or plan.get('signature') != signature or plan.get('state') != 'PREVIEW':
            raise Stop('apply requires --reviewed and the unchanged explicit preview plan')
        if sha(json.dumps(plan, sort_keys=True).encode()) != args.plan:
            raise Stop('MCP preview receipt changed; create a new preview')
        if time.time() > plan['expires_at']:
            raise Stop('MCP preview expired; create a new preview')
    elif args.plan:
        raise Stop('--plan is only used with --apply')
    client = Client()
    status = client.require('status')
    if status.get('gateway', {}).get('paused') is not False:
        raise Stop('gateway is paused or unverified; no native configuration written')
    conn = managed(status, binding['connection_id'])
    if conn.get('active') is False:
        raise Stop('managed connection is disabled')
    sources, expected_names, binding_target = [], {}, None
    for instance in args.instance:
        source = source_for(status, config, instance, binding['sources'].get(instance))
        native, error = native_binding(status, binding, instance, source['id'])
        if error == 'NATIVE_AMBIGUOUS':
            raise Stop('multiple native aliases; select one using mcp bind --native-name INSTANCE=NAME')
        if error and instance in binding.get('native_names', {}):
            raise Stop('selected native alias is no longer bound; review the local mapping')
        name = native['name'] if native else conn['name']
        expected_names[source['id']] = name
        aliases = [b for b in status.get('bindings', []) if b.get('sourceId') == source['id'] and b.get('connectionId') == binding['connection_id']]
        if len(aliases) > 1:
            if len(args.instance) != 1:
                raise Stop('connect an explicitly selected alias with one --instance at a time')
            binding_target = {'sourceId': source['id'], 'name': name}
        conflicts = [b for b in status.get('bindings', []) if b.get('sourceId') == source['id'] and b.get('name') == name]
        if any(b.get('changed') or b.get('missing') or b.get('connectionId') != binding['connection_id'] for b in conflicts):
            raise Stop('native entry changed, is missing or belongs elsewhere; inspect it in mcplink before replacing')
        sources.append(source['id'])
    tools = client.require('connections', 'tools', binding['connection_id']).get('tools', [])
    if (not isinstance(tools, list) or any(not isinstance(t, dict) or not isinstance(t.get('name'), str) for t in tools) or
            set(t for s in matches for t in s['tools']) - {t['name'] for t in tools}):
        raise Stop('required MCP tools are unavailable; no native configuration written')
    if args.apply:
        if {r['sourceId']: r['name'] for r in plan['changes']} != expected_names:
            raise Stop('native alias selection changed after preview; create a new preview')
        plan['state'] = 'APPLYING'
        save(plan_path, plan)
        try:
            ok, result = client.request('changes', 'apply', plan['mcplink_plan'])
            results = [{k: row.get(k) for k in ('sourceId', 'status', 'operationId')}
                       for row in result.get('results', [])]
            complete = (ok and len(results) == len(sources) and
                        {r['sourceId'] for r in results} == set(sources) and
                        all(r['status'] == 'applied' for r in results))
            plan.update(state='APPLIED' if complete else 'PARTIAL_OR_UNCERTAIN', results=results)
            save(plan_path, plan)
        except Stop:
            plan['state'] = 'UNCERTAIN'
            save(plan_path, plan)
            raise
        return {'status': 'PASS' if complete else 'BLOCKED', 'applied': complete,
                'results': results, 'reload_required': complete,
                'next': 'Reload MCP or start a new session, then run mcp check --live; do not replay partial operations.'}
    argv = ['changes', 'preview', '--connection', binding['connection_id'], '--mode', 'shared']
    for source in sources:
        argv.extend(['--target', source])
    result = client.require('rpc', '--stdin', payload={'method': 'changes.preview', 'params': {
        'connectionIds': [binding['connection_id']], 'targetSourceIds': sources,
        'mode': 'shared', 'action': 'upsert', 'bindingTarget': binding_target,
    }}) if binding_target else client.require(*argv)
    changes = [{k: row.get(k) for k in ('sourceId', 'agent', 'path', 'name', 'mode', 'action')}
               for row in result.get('changes', [])]
    if binding_target and any(r['name'] != expected_names.get(r['sourceId']) for r in changes):
        raise Stop('mcplink build did not honor the selected native alias; no apply performed')
    if (not identifier(result.get('planId')) or len(changes) != len(sources) or
            {r['sourceId'] for r in changes} != set(sources) or
            any(r['mode'] != 'shared' or r['action'] != 'upsert' or r['name'] != expected_names.get(r['sourceId']) for r in changes)):
        raise Stop('unexpected MCP preview scope; no apply performed')
    plan = {'signature': signature, 'state': 'PREVIEW', 'mcplink_plan': result['planId'],
            'expires_at': min(float(result['expiresAt']), time.time() + 600), 'changes': changes}
    plan_id = sha(json.dumps(plan, sort_keys=True).encode())
    save(LOCAL / 'mcp-plans' / (plan_id + '.json'), plan)
    return {'applied': False, 'plan_id': plan_id, 'changes': changes, 'mode': 'shared',
            'local_only': True, 'credential_values': 'never copied into assets or this plan'}


def command(args):
    if args.action == 'bind':
        return bind(args)
    if args.action == 'connect':
        return connect(args)
    return check(args.skill, args.target, args.instance, args.mode, args.live)
