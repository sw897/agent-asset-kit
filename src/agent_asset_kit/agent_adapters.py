"""Local Agent endpoints for one shared skill catalog. No credentials are loaded."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.parse
import urllib.request


ADAPTERS = {
    'codex': {'root': '.codex/skills', 'instructions': '.codex/AGENTS.md', 'command': 'codex',
              'docs': 'https://developers.openai.com/codex/skills/'},
    'claude': {'root': '.claude/skills', 'instructions': '.claude/CLAUDE.md', 'command': 'claude',
               'docs': 'https://code.claude.com/docs/en/skills'},
    'hermes': {'root': '.hermes/skills', 'instructions': '.hermes/SOUL.md', 'command': 'hermes',
               'docs': 'https://hermes-agent.nousresearch.com/docs/skills/'},
    'opencode': {'root': '.config/opencode/skills', 'instructions': '.config/opencode/AGENTS.md',
                 'command': 'opencode', 'docs': 'https://opencode.ai/docs/skills/'},
    'zcode': {'root': '.zcode/skills', 'instructions': '.zcode/AGENTS.md', 'command': 'zcode',
              'docs': 'https://zcode.z.ai/en/docs/skill'},
    'deepseekharness': {'root': '.dsh/skills', 'instructions': '.dsh/AGENTS.md', 'command': 'dsh',
                       'docs': 'docs/adapters.md#deepseek-harness'},
}
KINDS = tuple(ADAPTERS)


def defaults(home):
    home = Path(home)
    return {kind: str(home / spec['root']) for kind, spec in ADAPTERS.items()} | {
        'shared': str(home / '.agents/skills')}


def configured(config, enabled_only=True):
    """Explicit endpoint IDs are local; shared bindings continue to use Agent kinds."""
    result = []
    if 'agents' in config:
        for ident, value in config['agents'].items():
            value = dict(value)
            value.setdefault('kind', ident)
            value.setdefault('enabled', True)
            value['id'] = ident
            if not enabled_only or value['enabled']:
                result.append(value)
        return result
    # Compatibility: an existing native home is evidence, an absent default is not.
    for kind, root in config.get('skill_roots', {}).items():
        path = Path(root)
        enabled = path.is_dir() or path.parent.is_dir()
        if enabled_only and not enabled:
            continue
        result.append({'id': kind, 'kind': kind, 'skills_root': str(path), 'enabled': enabled,
                       'instructions': config.get('instructions', {}).get(kind)})
    return result


def endpoints(config, binding, enabled_only=True):
    rows = configured(config, enabled_only)
    return [row for row in rows if row['kind'] == binding['agent'] and
            (not binding.get('instance') or row['id'] == binding['instance'])]


def expand(config, binding):
    return [dict(binding, instance=row['id']) for row in endpoints(config, binding)]


def endpoint(config, binding):
    from .assetlib import Stop
    rows = endpoints(config, binding, enabled_only=not bool(binding.get('instance')))
    if len(rows) > 1:
        raise Stop('multiple local Agent profiles; select an explicit --instance')
    if rows:
        return rows[0]
    if binding.get('instance'):
        raise Stop('local Agent instance is no longer registered; review its path before continuing')
    # Logical enable is allowed before that Agent is installed; it performs no write.
    root = config.get('skill_roots', {}).get(binding['agent'])
    root = root or defaults(config['home']).get(binding['agent'])
    if not root:
        raise Stop('unknown Agent adapter: ' + binding['agent'])
    return {'id': binding['agent'], 'kind': binding['agent'], 'skills_root': root, 'enabled': False}


def initial(home, selected=None):
    home = Path(home).resolve()
    roots = defaults(home)
    selected = set(selected) if selected is not None else None
    result = {}
    for kind, root in roots.items():
        spec = ADAPTERS.get(kind, {})
        present = Path(root).is_dir() or Path(root).parent.is_dir()
        if home == Path.home().resolve() and spec.get('command'):
            present = present or shutil.which(spec['command']) is not None
        result[kind] = {'kind': kind, 'skills_root': root,
                        'enabled': kind in selected if selected is not None else present}
        if spec.get('instructions'):
            result[kind]['instructions'] = str(home / spec['instructions'])
    return result


def manage(args):
    from .assetlib import LOCAL, Stop, cfg, save
    config = cfg()
    if args.action == 'bundled':
        from .assetlib import hermes_bundled
        row = next((r for r in configured(config) if r['id'] == args.instance and r['kind'] == 'hermes'), None)
        if row is None:
            raise Stop('select an enabled Hermes instance')
        return {'instance': row['id'], 'bundled': sorted(hermes_bundled(row))}
    if args.action == 'list':
        return {'adapters': ADAPTERS, 'agents': configured(config, False)}
    if not args.reviewed:
        raise Stop('review local Agent paths, then pass --reviewed')
    ident = args.name or args.kind
    if not ident or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in ident):
        raise Stop('local Agent ID must use lowercase letters, numbers, hyphens or underscores')
    if 'agents' not in config:
        config['agents'] = {v['id']: {k: x for k, x in v.items() if k != 'id'}
                            for v in configured(config, False)}
    if args.action in ('enable', 'disable'):
        if ident not in config['agents']:
            raise Stop('Agent is not registered locally')
        config['agents'][ident].pop('on_request', None)
        config['agents'][ident]['enabled'] = args.action == 'enable'
    else:
        if args.kind not in ADAPTERS:
            raise Stop('unknown Agent kind')
        row = dict(config['agents'].get(ident, {}), kind=args.kind, enabled=True)
        row.pop('on_request', None)
        row['skills_root'] = str(Path(args.skills_root or defaults(config['home'])[args.kind]).expanduser().absolute())
        row['instructions'] = str(Path(args.instructions).expanduser().absolute()) if args.instructions else str(
            Path(config['home']) / ADAPTERS[args.kind]['instructions'])
        for name in ('command', 'probe_command'):
            value = getattr(args, name + '_json', None)
            if value:
                argv = json.loads(value)
                if not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv):
                    raise Stop('Agent commands must be nonempty JSON arrays of strings')
                row[name] = argv
        for name in ('runtime', 'interpreter'):
            value = getattr(args, name, None)
            if value:
                row[name] = str(Path(value).expanduser().absolute())
        config['agents'][ident] = row
        config.setdefault('skill_roots', {})[args.kind] = row['skills_root']
        config.setdefault('instructions', {})[args.kind] = row['instructions']
    save(LOCAL / 'config.json', config)
    return {'agent': ident, 'configuration': config['agents'][ident], 'native_files_changed': False}


def validate(kind, folder):
    from .assetlib import Stop
    from .skill_audit import entry_fields
    fields = entry_fields(Path(folder) / 'SKILL.md')
    if kind == 'deepseekharness' and not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', fields['name']):
        raise Stop('DeepSeek Harness skill names must use lowercase kebab-case')
    if kind == 'zcode' and len(fields['description'].encode('utf-16-le')) // 2 > 1024:
        raise Stop('ZCode ignores descriptions over 1024 characters')
    if kind == 'zcode' and len((Path(folder) / 'SKILL.md').read_bytes()) > 100_000:
        raise Stop('ZCode truncates this skill body; review before enabling')
    return fields


def command(config, kind, suffix=(), instance=None):
    from .assetlib import Stop
    rows = endpoints(config, {'agent': kind, 'instance': instance} if instance else {'agent': kind})
    if len(rows) != 1:
        raise Stop('native discovery requires one enabled local Agent instance')
    row = rows[0]
    argv = row.get('command') or [ADAPTERS[kind]['command']]
    executable = shutil.which(argv[0])
    if not executable:
        raise Stop(kind + ' native CLI is unavailable')
    return [executable, *argv[1:], *suffix]


def json_catalog(config, kind, instance=None):
    """A catalog probe must be a non-model native operation, never a user prompt."""
    from .assetlib import ROOT, Stop
    rows = endpoints(config, {'agent': kind, 'instance': instance} if instance else {'agent': kind})
    if len(rows) != 1:
        raise Stop('select one enabled local Agent instance for discovery')
    row = rows[0]
    override = row.get('probe_command')
    environment = None
    method = 'native JSON skill catalog'
    if override:
        argv = override
    elif kind == 'opencode':
        argv = command(config, kind, ('debug', 'skill'), instance)
    elif kind == 'zcode':
        argv = command(config, kind, ('skills', 'list', '--json'), instance)
    elif kind == 'deepseekharness':
        if not row.get('runtime') or not row.get('interpreter'):
            raise Stop('DeepSeek Harness native discovery requires its reviewed bundled runtime and interpreter')
        interpreter = shutil.which(row['interpreter'])
        if not interpreter:
            raise Stop('DeepSeek Harness bundled interpreter is unavailable')
        argv = [interpreter, '--expose-internals',
                str(Path(__file__).with_name('native_deepseek_catalog.mjs')),
                '--runtime', row['runtime'], '--home', str(Path(row['skills_root']).parent),
                '--cwd', str(ROOT)]
        environment = dict(os.environ, ELECTRON_RUN_AS_NODE='1')
        method = 'bundled DeepSeek Harness FileSystemSkillProvider.list'
    else:
        raise Stop('no native JSON catalog is defined for this Agent')
    process = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', timeout=30,
                             env=environment)
    if process.returncode:
        raise Stop(kind + ' catalog command failed; no model turn or fallback was started')
    value = json.loads(process.stdout)
    parse_errors = 0
    if isinstance(value, dict):
        if value.get('complete') is False:
            raise Stop(kind + ' native catalog observation was incomplete')
        parse_errors = value.get('native_parse_errors', 0)
        value = value.get('skills', value.get('data'))
    if not isinstance(value, list):
        raise Stop('native skill catalog did not return a list')
    result = []
    for item in value:
        if not isinstance(item, dict) or not (item.get('name') or item.get('id')):
            raise Stop('native catalog entry is invalid')
        path = item.get('path') or item.get('location') or item.get('file')
        if path is not None and not isinstance(path, str):
            raise Stop('native catalog path is not a string')
        resource_uri = None
        if path and re.match(r'^[a-z][a-z0-9+.-]*://', path, re.I):
            if path.lower().startswith('file://'):
                parsed = urllib.parse.urlsplit(path)
                path = urllib.request.url2pathname(('//' + parsed.netloc if parsed.netloc else '') + parsed.path)
            else:
                resource_uri, path = path, None
        result.append({'name': item.get('name') or item['id'], 'path': path,
                       'resource_uri': resource_uri,
                       'visible': item.get('visible', item.get('enabled', True))})
    return {'method': method, 'skills': result,
            'native_parse_errors': parse_errors, 'model_turns_sent': 0}
