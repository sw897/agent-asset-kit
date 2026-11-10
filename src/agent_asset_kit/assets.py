#!/usr/bin/env python3
"""AgentAssetKit CLI: reviewed copies, isolated workspaces and project templates."""

import argparse
import difflib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from .assetlib import (ROOT, PRIMARY, LOCAL, Stop, atomic, cfg, collect, deploy, files_in,
                      git, key, load, locked, native_path, package, registry, relative,
                      rename_new, run, save, scan, sha, slug, tree_hash, write_tree,
                      file_modes, git_modes, read_native, portable_paths, validate_binding, cli_argv, cli_display,
                      owned_path, canonical_path, library_path)
from . import skill_audit
from . import agent_adapters
from . import skill_sources
from . import skill_trials
from . import skill_merge
from . import skill_versions
from . import skill_mcp

PUBLISH_FILES = {'AGENTS.md', 'README.md', 'USER.md', 'MEMORY.md', '.gitignore', '.gitattributes', '.agent-asset-kit.json', 'LICENSE'}
PUBLISH_DIRS = {'agents', 'memory', 'skills', 'templates', 'scripts', 'tests', 'licenses', '.github'}


def reviewed(args):
    if not args.reviewed:
        raise Stop('review candidate/preview, then pass --reviewed')


def configure(args, on_request=False):
    if (LOCAL / 'config.json').exists():
        raise Stop('local configuration already exists; edit it explicitly')
    home = Path(args.home).expanduser().resolve()
    selected = args.agents.split(',') if getattr(args, 'agents', None) else None
    if selected and set(selected) - set(agent_adapters.KINDS) - {'shared'}:
        raise Stop('unknown Agent adapter in --agents')
    agents = agent_adapters.initial(home, selected)
    if on_request:
        for kind in agent_adapters.KINDS:
            if not agents[kind]['enabled']:
                agents[kind]['on_request'] = True
    value = {'home': str(home), 'publication_enabled': False,
             'github_repository': args.github_repository,
             'gitleaks': str(Path(args.gitleaks).resolve()) if args.gitleaks else shutil.which('gitleaks'),
             'skill_roots': agent_adapters.defaults(home), 'agents': agents,
             'instructions': {kind: row['instructions'] for kind, row in agents.items() if row.get('instructions')}}
    save(LOCAL / 'config.json', value)
    return {'configured': True, 'publication_enabled': False,
            'enabled_agents': [row['id'] for row in agent_adapters.configured(value)]}


def record(name, kind, files, bindings, agent, origin=None):
    return {'name': name, 'path': f'skills/{kind}/{name}', 'sha256': tree_hash(files),
            'files': len(files), 'bytes': sum(len(v[0]) for v in files.values()),
            'bindings': bindings, 'origin': origin or {'kind': kind, 'url': None, 'version': None},
            'reviewed_by': agent, 'reviewed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'native_loading': 'UNVERIFIED', 'file_modes': file_modes(files)}


def accept(args):
    reviewed(args)
    folder = LOCAL / 'candidates' / relative(args.candidate)
    item = load(folder / 'candidate.json')
    if not item:
        raise Stop('candidate does not exist')
    files = files_in(folder / 'package', item.get('file_modes'))
    if 'SKILL.md' not in files or tree_hash(files) != item['sha256']:
        raise Stop('candidate changed; collect a new snapshot')
    scan(folder / 'package')
    skill_audit.entry_fields(folder / 'package/SKILL.md')
    evaluation = load(folder / 'evaluation.json')
    if item.get('candidate_type') == 'external':
        if not evaluation or evaluation['sha256'] != item['sha256'] or evaluation['decision'] not in ('trial', 'adopt'):
            raise Stop('external candidate requires a matching semantic evaluation before acceptance')
        if evaluation.get('identity_sha256') != skill_sources.review_identity(item):
            raise Stop('candidate source or review metadata changed; evaluate the current snapshot again')
        if item.get('requires_merge') and not item.get('reconciled'):
            raise Stop('existing upstream skill requires merge to preserve local increments')
    manifest, owners = registry()
    ident = item['skill_id']
    if ident:
        existing = manifest['packages'].get(ident)
        if not existing:
            raise Stop('candidate asset is absent from this workspace; refresh/reconcile first')
        package(ROOT, existing)
        bases = set(item['base'].values())
        if existing['sha256'] not in bases and existing['sha256'] != item['sha256']:
            raise Stop('repository and native copy both changed; reconcile in a workspace')
        name, kind = existing['name'], existing['origin']['kind']
        bindings = list(existing['bindings'])
        origin = existing['origin']
        if item.get('candidate_type') == 'external':
            if skill_sources.source_key(skill_sources.known_source(existing)) != skill_sources.source_key(item['origin']):
                raise Stop('upstream source identity changed; review migration explicitly')
            origin = dict(item['origin'], kind=kind, previous=existing['origin'])
    else:
        name = slug(args.name or item.get('name') or Path(item['source']).name)
        kind = args.kind or item['origin']['kind']
        if kind not in ('custom', 'third-party'):
            raise Stop('declare --kind custom or third-party after checking provenance')
        ident = uuid.uuid4().hex
        bindings = []
        origin = dict(item['origin'], kind=kind)
        if any(v['name'] == name for v in manifest['packages'].values()):
            raise Stop('package name already exists; choose a distinct name or reconcile identity')
    for binding in item['bindings']:
        native_path(cfg(), binding)
        logical = {'agent': binding['agent'], 'path': binding['path']}
        if key(logical) in owners and owners[key(logical)] != ident:
            raise Stop('binding belongs to another asset')
        if logical not in bindings:
            bindings.append(logical)
    if item.get('reconciled') and item.get('bindings') and item.get('candidate_type') != 'external':
        for binding in item['bindings']:
            if tree_hash(read_native(cfg(), binding, manifest['packages'].get(ident))) != item['original_sha256']:
                raise Stop('native source changed after reconciliation; collect a fresh snapshot')
    previous = manifest['packages'].get(ident)
    updated = dict(previous or {})
    updated.update(record(name, kind, files, bindings, args.agent, origin))
    if item.get('license_file'):
        updated['license_file'] = item['license_file']
    manifest['packages'][ident] = updated
    pending = getattr(args, 'trial', False) or (evaluation or {}).get('decision') == 'trial' or (previous or {}).get('lifecycle') == 'reviewed'
    releases = skill_versions.release_change(manifest, ident, previous, pending, persist=False)
    manifest['version'] = 2
    target = canonical_path(ROOT, updated)
    if target.exists() and not item['skill_id']:
        raise Stop('unregistered canonical directory exists; register/review it first')
    stage = target.parent / '.asset-staging' / uuid.uuid4().hex
    library_path(stage)
    stage.mkdir(parents=True)
    write_tree(stage, files)
    backup = target.parent / '.asset-backups' / uuid.uuid4().hex
    library_path(backup)
    journal_path = LOCAL / 'acceptances' / (uuid.uuid4().hex + '.json')
    journal = {'state': 'PREPARED', 'target': str(target), 'backup': str(backup),
               'candidate': item['id'], 'skill_id': ident, 'previous': previous}
    save(journal_path, journal)
    if previous:
        package(ROOT, previous)
    if target.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        rename_new(target, backup)
    try:
        rename_new(stage, target)
    except BaseException:
        if backup.exists() and not target.exists():
            rename_new(backup, target)
        raise
    # Reviewed classification, dependencies and provenance survive native backflow.
    if releases['skills'] or (ROOT / 'skills/releases.json').exists():
        save(ROOT / 'skills/releases.json', releases)
    save(ROOT / 'skills/manifest.json', manifest)
    if item.get('candidate_type') == 'external':
        decisions = load(ROOT / 'skills/decisions.json', {'version': 1, 'entries': []})
        decisions['entries'].append(dict(evaluation, source_key=skill_sources.source_key(item['origin']),
                                         name=updated['name'], skill_id=ident))
        save(ROOT / 'skills/decisions.json', decisions)
    if item.get('reconciled') and item.get('bindings') and item.get('candidate_type') != 'external':
        approvals = load(LOCAL / 'accepted-copies.json', {})
        for binding in item['bindings']:
            approvals[key(binding)] = {'observed_sha256': item['original_sha256'],
                                       'replacement_sha256': item['sha256'], 'candidate': item['id']}
        save(LOCAL / 'accepted-copies.json', approvals)
    journal['state'] = 'COMPLETE'
    save(journal_path, journal)
    return {'accepted': item['id'], 'skill_id': ident, 'path': target.relative_to(ROOT).as_posix()}


def register(args):
    reviewed(args)
    path = owned_path(args.path)
    rel = path.relative_to(ROOT).parts
    if len(rel) != 3 or rel[:2] not in (('skills', 'custom'), ('skills', 'third-party')):
        raise Stop('register requires skills/custom/<name> or skills/third-party/<name>')
    files = files_in(path)
    if 'SKILL.md' not in files:
        raise Stop('SKILL.md is required')
    skill_audit.entry_fields(path / 'SKILL.md')
    scan(path)
    manifest, owners = registry()
    if any(v['name'] == rel[2] for v in manifest['packages'].values()):
        raise Stop('already registered; use revision for a reviewed edit')
    bindings = [{'agent': a, 'path': rel[2]} for a in dict.fromkeys(args.targets.split(',')) if a]
    for binding in bindings:
        validate_binding(cfg(), binding)
        if key(binding) in owners:
            raise Stop('binding already belongs to another asset')
    ident = uuid.uuid4().hex
    manifest['packages'][ident] = record(rel[2], rel[1], files, bindings, args.agent)
    skill_versions.release_change(manifest, ident, None, getattr(args, 'trial', False))
    manifest['version'] = 2
    save(ROOT / 'skills/manifest.json', manifest)
    return {'skill_id': ident, 'registered': args.path}


def revision(args):
    reviewed(args)
    manifest, _ = registry()
    old = manifest['packages'][args.skill_id]
    path = canonical_path(ROOT, old)
    skill_audit.entry_fields(path / 'SKILL.md')
    files = files_in(path, old.get('file_modes') or git_modes(ROOT, old['path']))
    for name in getattr(args, 'executable', []) or []:
        relative(name)
        if name not in files:
            raise Stop('executable intent names an absent file')
        files[name] = (files[name][0], 0o755)
        (path / name).chmod(0o755)
    for name in getattr(args, 'not_executable', []) or []:
        relative(name)
        if name not in files:
            raise Stop('non-executable intent names an absent file')
        files[name] = (files[name][0], 0o644)
        (path / name).chmod(0o644)
    scan(path)
    if 'SKILL.md' not in files:
        raise Stop('SKILL.md is required')
    updated = dict(old)
    updated.update(record(old['name'], old['origin']['kind'], files,
                          old['bindings'], args.agent, old['origin']))
    manifest['packages'][args.skill_id] = updated
    skill_versions.release_change(manifest, args.skill_id, old,
        getattr(args, 'trial', False) or old.get('lifecycle') == 'reviewed')
    manifest['version'] = 2
    if old.get('license_file'):
        manifest['packages'][args.skill_id]['license_file'] = old['license_file']
    save(ROOT / 'skills/manifest.json', manifest)
    return {'revised': args.skill_id, 'sha256': tree_hash(files)}


def enable(args):
    reviewed(args)
    manifest, owners = registry()
    item = manifest['packages'][args.skill_id]
    binding = {'agent': args.target, 'path': args.path or item['name']}
    validate_binding(cfg(), binding)
    owner = owners.get(key(binding))
    if owner and owner != args.skill_id:
        raise Stop('target name collision; choose another --path')
    if binding not in item['bindings']:
        item['bindings'].append(binding)
    manifest['version'] = 2
    save(ROOT / 'skills/manifest.json', manifest)
    return {'enabled': binding, 'skill_id': args.skill_id}


def note(args):
    if args.scope != 'global':
        raise Stop('only global Agent preferences and shared rules belong in shared memory; '
                   'project-scoped note storage is outside AgentAssetKit, including --inbox; '
                   'maintain execution context in the owning skill references')
    body = Path(args.file).read_text(encoding='utf-8').strip()
    if not body:
        raise Stop('empty note')
    front = {'title': args.title, 'agent': args.agent, 'scope': args.scope, 'source': args.source,
             'created': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    data = ('---\n' + '\n'.join(f'{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in front.items())
            + '\n---\n\n' + body + '\n').encode()
    with tempfile.TemporaryDirectory(dir=LOCAL) as tmp:
        (Path(tmp) / 'note.md').write_bytes(data)
        scan(tmp)
    directory = owned_path('memory/' + ('inbox/' + slug(args.agent) if args.inbox else 'shared'))
    path = directory / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:10] + '.md')
    atomic(path, data, 0o644)
    return {'note': path.relative_to(ROOT).as_posix(), 'sha256': sha(data)}


def clean(root):
    if git('status', '--porcelain', cwd=root).stdout.strip():
        raise Stop(f'working tree has changes: {root.name}; preserve and publish/review them first')


def private_remote():
    repo = cfg().get('github_repository', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise Stop('configure a GitHub owner/repository')
    expected = {f'https://github.com/{repo}.git', f'git@github.com:{repo}.git'}
    for checkout in dict.fromkeys((PRIMARY, ROOT)):
        for push in (False, True):
            args = ['remote', 'get-url', '--all'] + (['--push'] if push else []) + ['origin']
            urls = git(*args, cwd=checkout).stdout.splitlines()
            if len(urls) != 1 or urls[0] not in expected:
                raise Stop('origin must resolve only to the configured GitHub repository in every worktree')
    if run(['gh', 'api', f'repos/{repo}', '--jq', '.private']).stdout.strip() != 'true':
        raise Stop('GitHub repository is not confirmed PRIVATE; publication stopped')


def export_scan(root):
    files = git('ls-files', '-z', cwd=root).stdout.split('\0')
    with tempfile.TemporaryDirectory(prefix='export-', dir=LOCAL) as tmp:
        for name in filter(None, files):
            path = relative(name)
            if path.parts[0] not in PUBLISH_DIRS and name not in PUBLISH_FILES:
                raise Stop(f'unrecognized publish path: {name}')
            source = owned_path(name, root)
            if source.is_symlink():
                raise Stop('repository assets must be regular files')
            if source.exists():
                target = Path(tmp) / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        scan(tmp)


def refresh():
    if git('branch', '--show-current', cwd=PRIMARY).stdout.strip() != 'main':
        raise Stop('primary checkout must remain on main')
    clean(PRIMARY)
    if git('remote', 'get-url', 'origin', cwd=PRIMARY, check=False).returncode:
        return {'refreshed': False, 'reason': 'no remote configured'}
    private_remote()
    git('fetch', 'origin', 'main', cwd=PRIMARY)
    git('merge', '--ff-only', 'FETCH_HEAD', cwd=PRIMARY)
    export_scan(PRIMARY)
    return {'refreshed': True, 'head': git('rev-parse', 'HEAD', cwd=PRIMARY).stdout.strip()}


def workspace(args):
    clean(PRIMARY)
    ident = slug(args.agent) + '-' + uuid.uuid4().hex[:10]
    path = LOCAL / 'worktrees' / ident
    path.parent.mkdir(exist_ok=True)
    git('worktree', 'add', '-b', 'assets/' + ident, path, 'main', cwd=PRIMARY)
    return {'workspace': str(path), 'branch': 'assets/' + ident,
            'command': cli_display(path) + ' status'}


def sync(args):
    if args.local_only and args.branch_only:
        raise Stop('--branch-only cannot be combined with --local-only')
    if not args.local_only and not cfg()['publication_enabled']:
        raise Stop('publication is disabled; enable only after user authorization')
    if git('branch', '--show-current', cwd=PRIMARY).stdout.strip() != 'main':
        raise Stop('primary checkout must remain on main')
    branch = git('branch', '--show-current').stdout.strip()
    if not branch:
        raise Stop('detached HEAD cannot publish')
    if git('diff', '--cached', '--quiet', check=False).returncode:
        raise Stop('unknown staged changes; inspect the index first')
    for marker in ('MERGE_HEAD', 'rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD'):
        if (ROOT / git('rev-parse', '--git-path', marker).stdout.strip()).exists():
            raise Stop('unfinished Git operation; resolve it before publishing')
    if ROOT != PRIMARY:
        clean(PRIMARY)
    if not args.local_only:
        private_remote()
    manifest, _ = registry()
    for item in manifest['packages'].values():
        package(ROOT, item)
    publish_names = list(filter(None, git('ls-files', '--cached', '--others', '--exclude-standard', '-z').stdout.split('\0')))
    portable_paths(publish_names)
    for name in publish_names:
        path = relative(name)
        if path.parts[0] not in PUBLISH_DIRS and name not in PUBLISH_FILES:
            raise Stop(f'unrecognized publish path: {name}')
        owned_path(name)
    git('add', '--all', '--', '.')
    # Upstream .gitignore rules must not silently drop a package's required assets.
    required = []
    for item in manifest['packages'].values():
        path, files = package(ROOT, item)
        required.extend((path / name).relative_to(ROOT).as_posix() for name in files)
    for offset in range(0, len(required), 100):
        git('add', '--force', '--', *required[offset:offset + 100])
    # Git for Windows does not infer executable mode from chmod. Publish reviewed
    # logical modes so immutable revisions have the same identity on both systems.
    modes = {mode: [] for mode in (0o644, 0o755)}
    for item in manifest['packages'].values():
        path, files = package(ROOT, item)
        for name, (_, mode) in files.items():
            modes[mode].append((path / name).relative_to(ROOT).as_posix())
    for mode, names in modes.items():
        for offset in range(0, len(names), 100):
            git('update-index', '--chmod=' + ('+x' if mode == 0o755 else '-x'), '--', *names[offset:offset + 100])
    if git('diff', '--cached', '--quiet', check=False).returncode:
        with tempfile.TemporaryDirectory(prefix='index-', dir=LOCAL) as tmp:
            git('checkout-index', '--all', '--prefix', str(Path(tmp)) + os.sep)
            scan(tmp)
            for item in registry(Path(tmp))[0]['packages'].values():
                package(Path(tmp), item)
        for item in registry()[0]['packages'].values():
            package(ROOT, item)
        if (git('diff', '--quiet', check=False).returncode or
                git('ls-files', '--others', '--exclude-standard', '-z').stdout):
            raise Stop('files changed during scan; staged content retained for review')
        git('commit', '-m', f'{args.agent}: {args.message}')
    if getattr(args, 'branch_only', False):
        if args.local_only or ROOT == PRIMARY or not branch.startswith(('codex/', 'assets/')):
            raise Stop('--branch-only needs an isolated managed branch and authenticated publication')
        export_scan(ROOT)
        git('push', 'origin', 'HEAD:refs/heads/' + branch)
        remote = git('ls-remote', 'origin', 'refs/heads/' + branch).stdout.split()[0]
        if remote != git('rev-parse', 'HEAD').stdout.strip():
            raise Stop('remote branch advanced; review retained state')
        return {'head': remote, 'pushed': True, 'branch': branch, 'main_merged': False}
    if not args.local_only:
        git('fetch', 'origin', cwd=PRIMARY)
        if git('show-ref', '--verify', '--quiet', 'refs/remotes/origin/main', cwd=PRIMARY, check=False).returncode == 0:
            git('merge', '--no-edit', 'origin/main', cwd=PRIMARY)
    if ROOT != PRIMARY:
        git('merge', '--no-edit', branch, cwd=PRIMARY)
    clean(PRIMARY)
    for item in registry(PRIMARY)[0]['packages'].values():
        package(PRIMARY, item)
    export_scan(PRIMARY)
    if not args.local_only:
        private_remote()
        git('push', 'origin', 'HEAD:refs/heads/main', cwd=PRIMARY)
        remote = git('ls-remote', 'origin', 'refs/heads/main', cwd=PRIMARY).stdout.split()[0]
        if remote != git('rev-parse', 'HEAD', cwd=PRIMARY).stdout.strip():
            raise Stop('remote advanced again; recheck synchronization')
    return {'head': git('rev-parse', 'HEAD', cwd=PRIMARY).stdout.strip(), 'pushed': not args.local_only,
            'workspace_retained': str(ROOT) if ROOT != PRIMARY else None}


def attach(args):
    """Preview or add a library pointer, preserving all unowned native text."""
    blocks = load(owned_path('agents/instruction-blocks.json', PRIMARY), {})
    if not isinstance(blocks, dict):
        raise Stop('instruction blocks must map markers to Markdown templates')
    templates = {}
    for marker, name in blocks.items():
        if not re.fullmatch(r'[A-Z][A-Z0-9_]+', marker) or not isinstance(name, str):
            raise Stop('invalid instruction block declaration')
        path = owned_path(name, PRIMARY)
        if not path.is_relative_to(PRIMARY / 'agents') or path.suffix != '.md':
            raise Stop('instruction templates must be Markdown files under agents/')
        templates[marker] = path.read_text(encoding='utf-8').strip()
    changes = []
    for endpoint in agent_adapters.configured(cfg()):
        instruction = endpoint.get('instructions')
        if not instruction:
            continue
        path = Path(instruction)
        if path.is_symlink():
            raise Stop('instruction files must not be symlinks')
        original = path.read_bytes() if path.exists() else b''
        old = original.decode('utf-8')
        command = cli_display(PRIMARY)
        default = (f'## AgentAssetKit library\n\nFor asset management in {endpoint["kind"]}, '
                   f'run `aak guide` and follow AGENTS.md in `{PRIMARY}`. '
                   f'Select this library with `{command}`.')
        new = old
        values = {'root': str(PRIMARY), 'aak': command, 'agent': endpoint['kind'],
                  'skills_root': str(endpoint['skills_root']), 'local': str(LOCAL)}
        for marker, template in (templates or {'AGENT_ASSET_KIT': default}).items():
            begin, end = f'<!-- {marker}_START -->', f'<!-- {marker}_END -->'
            if (new.count(begin) != new.count(end) or new.count(begin) > 1 or
                    (begin in new and new.index(begin) > new.index(end))):
                raise Stop('instruction marker conflict: ' + marker)
            body = re.sub(r'\{\{(\w+)\}\}', lambda match: values[match[1]], template)
            if re.search(r'<!-- [A-Z0-9_]+_(START|END) -->', body):
                raise Stop('instruction templates cannot contain managed block markers')
            newline = '\r\n' if '\r\n' in old else '\n'
            text = newline.join([begin, *body.splitlines(), end])
            if begin in new:
                left, _, tail = new.partition(begin)
                managed, _, right = tail.partition(end)
                if re.search(r'<!-- [A-Z0-9_]+_(START|END) -->', managed):
                    raise Stop('nested instruction markers: ' + marker)
                new = left + text + right
            else:
                new = new + (newline if new.endswith(newline) else newline * 2) + text + newline
        if old != new:
            duplicate = next((c for c in changes if Path(c['path']).resolve() == path.resolve()), None)
            if duplicate:
                if duplicate['content'] != new:
                    raise Stop('Agent instances share an instruction path but require different content')
                continue
            changes.append({'agent': endpoint['kind'], 'path': str(path), 'before_sha256': sha(old.encode()),
                            'content': new, 'existed': path.exists()})
    if not args.apply:
        return {'applied': False, 'changes': changes}
    # Verify every destination before changing any file.
    for change in changes:
        path = Path(change['path'])
        if path.is_symlink() or path.exists() != change['existed']:
            raise Stop('native instructions changed after preview')
        data = path.read_bytes() if path.exists() else b''
        if sha(data) != change['before_sha256']:
            raise Stop('native instructions changed during attachment')
    receipts = []
    for change in changes:
        path = Path(change['path'])
        if (path.is_symlink() or path.exists() != change['existed'] or
                sha(path.read_bytes() if path.exists() else b'') != change['before_sha256']):
            raise Stop('native instructions changed during attachment; prior backups retained')
        backup = LOCAL / 'instruction-backups' / uuid.uuid4().hex / path.name
        if path.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        atomic(path, change['content'].encode(), 0o644)
        receipts.append({'agent': change['agent'], 'path': str(path),
                         'backup': str(backup) if change['existed'] else None,
                         'before_sha256': change['before_sha256'], 'after_sha256': sha(path.read_bytes())})
    result = {'applied': True, 'changes': receipts}
    save(LOCAL / ('attach-' + uuid.uuid4().hex + '.json'), result)
    return result


def init_project(args):
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise Stop('project directory does not exist')
    target = project / 'AGENTS.md'
    if target.is_symlink():
        raise Stop('project AGENTS.md is a symlink; inspect manually')
    old = target.read_text(encoding='utf-8') if target.exists() else ''
    if any(start in old for start in ('<!-- AGENT_ASSET_KIT_TEMPLATE_START -->', '<!-- AGENT_ASSETS_TEMPLATE_START -->')):
        raise Stop('project already has a template section; review changes directly, no automatic overwrite')
    stack = []
    for name, label in [('package.json', 'JavaScript/TypeScript'), ('Cargo.toml', 'Rust'),
                        ('go.mod', 'Go'), ('pyproject.toml', 'Python')]:
        if (project / name).is_file():
            stack.append(label)
    commands = '\n'.join('- `' + c.replace('`', '') + '`（初始化时登记，执行结果另行核验）' for c in args.command)
    if not commands:
        commands = '尚未核实运行/验证命令。先检查现有指南和工程入口；确认真实命令后记录，不编造验证结果。'
    base = (ROOT / 'templates/agents/base.md').read_text(encoding='utf-8')
    module = '' if args.template == 'base' else (ROOT / f'templates/agents/modules/{args.template}.md').read_text(encoding='utf-8')
    template_hash = sha((base + '\n' + module).encode())
    fields = {'PROJECT': project.name, 'PURPOSE': args.purpose,
              'STACK': '、'.join(stack) or '以项目实际文件和说明为准', 'COMMANDS': commands}
    for name, value in fields.items():
        base = base.replace('{{' + name + '}}', value)
    if '{{' in base:
        raise Stop('unresolved template variables')
    version = git('rev-parse', '--short', 'HEAD', check=False).stdout.strip() or 'uncommitted'
    section = ('<!-- AGENT_ASSET_KIT_TEMPLATE_START -->\n'
               f'<!-- template: agents/{args.template}; version: {version}; sha256: {template_hash} -->\n'
               + base + '\n' + module + '\n<!-- AGENT_ASSET_KIT_TEMPLATE_END -->\n')
    proposed = (old.rstrip() + '\n\n' if old else '') + section
    ident = uuid.uuid4().hex[:20]
    directory = LOCAL / 'project-plans' / ident
    library_path(directory)
    directory.mkdir(parents=True)
    atomic(directory / 'AGENTS.md', proposed.encode())
    save(directory / 'plan.json', {'id': ident, 'project': str(project), 'old_sha256': sha(old.encode()),
                                   'template': args.template, 'existed': target.exists(),
                                   'old_text': old, 'template_version': version})
    diff = ''.join(difflib.unified_diff(old.splitlines(True), proposed.splitlines(True),
                                       fromfile='AGENTS.md (current)', tofile='AGENTS.md (proposed)'))
    atomic(directory / 'preview.diff', diff.encode())
    return {'plan': ident, 'proposed_file': str(directory / 'AGENTS.md'),
            'diff': str(directory / 'preview.diff'), 'applied': False}


def apply_project(args):
    reviewed(args)
    directory = LOCAL / 'project-plans' / relative(args.plan)
    plan = load(directory / 'plan.json')
    if not plan:
        raise Stop('project plan does not exist')
    target = Path(plan['project']) / 'AGENTS.md'
    if target.is_symlink():
        raise Stop('project target changed into a link')
    old = target.read_text(encoding='utf-8') if target.exists() else ''
    if sha(old.encode()) != plan['old_sha256'] or target.exists() != plan['existed']:
        raise Stop('project instructions changed after preview; generate a new plan')
    data = (directory / 'AGENTS.md').read_bytes()
    if plan['old_text'] and plan['old_text'].rstrip() not in data.decode():
        raise Stop('existing project instructions must be preserved in this additive initialization')
    with tempfile.TemporaryDirectory(dir=LOCAL) as tmp:
        (Path(tmp) / 'AGENTS.md').write_bytes(data)
        scan(tmp)
    if target.exists():
        shutil.copy2(target, directory / 'original-AGENTS.md')
    atomic(target, data, 0o644)
    plan['applied_sha256'] = sha(target.read_bytes())
    save(directory / 'result.json', plan)
    return {'applied': True, 'file': str(target), 'sha256': sha(data)}


def tick():
    result = {'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    try:
        capture = collect()
        result['collection'] = {'pending': len(capture['candidates']), 'blocked': capture['blocked']}
        if capture['candidates'] or capture['blocked']:
            raise Stop('local changes captured; review candidates before refreshing/deploying')
        result['refresh'] = refresh()
        preview = deploy()
        result['deployment'] = deploy(True, plan_id=preview['plan_id'])
        result['deployment']['plan_id'] = preview['plan_id']
        result['status'] = 'PASS' if not capture['blocked'] else 'BLOCKED'
    except (Stop, OSError, ValueError) as error:
        result.update(status='BLOCKED', reason=str(error))
    save(LOCAL / 'last-tick.json', result)
    return result


def scheduler(args):
    if sys.platform != 'darwin':
        raise Stop('only the macOS scheduler is implemented; other platforms are UNVERIFIED')
    label = 'io.agentassetkit.collect.' + sha(str(PRIMARY).encode())[:12]
    destination = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
    executable = shutil.which('aak')
    if not executable:
        raise Stop('install aak on PATH before configuring the collector')
    value = {'Label': label, 'ProgramArguments': [os.path.abspath(executable), '--root', str(PRIMARY), 'collect'],
             'StartInterval': args.interval, 'RunAtLoad': True,
             'WorkingDirectory': str(PRIMARY), 'StandardOutPath': '/dev/null', 'StandardErrorPath': '/dev/null'}
    if destination.exists() and plistlib.loads(destination.read_bytes()) != value:
        raise Stop('existing scheduler differs; inspect before replacing')
    if args.apply:
        atomic(destination, plistlib.dumps(value))
        service = f'gui/{os.getuid()}/{label}'
        if run(['launchctl', 'print', service], check=False).returncode:
            run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', destination])
    return {'applied': args.apply, 'plist': str(destination), 'interval_seconds': args.interval,
            'mode': 'local-collection; remote publication stays in authenticated Agent sessions'}


def recheck(args):
    reviewed(args)
    path = LOCAL / 'deployments' / (relative(args.deployment).as_posix() + '.json')
    journal = load(path)
    if not journal:
        raise Stop('deployment journal does not exist')
    checks = []
    for change in journal['changes']:
        target = Path(change['target'])
        current = tree_hash(files_in(target.resolve(), change.get('file_modes'))) if target.exists() else None
        before = tree_hash(files_in(target.resolve(), change.get('before_file_modes'))) if target.exists() else None
        recognized = current == change['after_sha256'] or (before is not None and before == change['before_sha256'])
        checks.append({'target': str(target), 'recognized': recognized, 'sha256': current})
    if not all(c['recognized'] for c in checks):
        return {'status': 'BLOCKED', 'checks': checks, 'reason': 'restore/reconcile preserved originals before rechecking'}
    journal['state'] = 'RECHECKED'
    save(path, journal)
    return {'rechecked': args.deployment, 'checks': checks, 'replayed': False}


def main(argv=None, initialize_default=False):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(prog='aak', description=__doc__)
    parser.add_argument('--root', help='select a personal asset library (also accepted after the subcommand)')
    sub = parser.add_subparsers(dest='command_name', required=True)
    sub.add_parser('guide', help='read the bundled agent guide from any directory')
    sub.add_parser('init', help='create an empty asset library; see aak init --help')
    from . import quick_skills
    quick_skills.commands(sub)
    p = sub.add_parser('configure')
    p.add_argument('--home', default=str(Path.home()))
    p.add_argument('--gitleaks')
    p.add_argument('--github-repository', default='', help='optional private asset repository in OWNER/REPO form')
    p.add_argument('--agents', help='explicit comma-separated local Agent kinds; otherwise detect installed homes/CLIs')
    for command in ('collect', 'refresh', 'status', 'catalog', 'tick'):
        sub.add_parser(command)
    for command in ('audit', 'doctor'):
        p = sub.add_parser(command)
        p.add_argument('--target', choices=['all', *agent_adapters.KINDS], default='all')
        p.add_argument('--instance')
        p.add_argument('--output')
        p.add_argument('--mcp', action='store_true', help='read-only live mcplink dependency checks; no native writes')
        if command == 'doctor':
            p.add_argument('--native', action='store_true', help='initialize clients to read catalogs; no model turn')
    p = sub.add_parser('index')
    p.add_argument('--agent', required=True)
    p.add_argument('--reviewed', action='store_true')
    p = sub.add_parser('resolve')
    p.add_argument('--skill', required=True)
    p.add_argument('--target', choices=agent_adapters.KINDS, required=True)
    p.add_argument('--instance')
    p = sub.add_parser('mcp', help='declare/check MCP dependencies and explicitly connect through mcplink')
    mcp = p.add_subparsers(dest='action', required=True)
    p = mcp.add_parser('check')
    p.add_argument('--skill')
    p.add_argument('--target', choices=['all', *agent_adapters.KINDS], default='all')
    p.add_argument('--instance')
    p.add_argument('--mode', help='require readiness for this declared mode, including optional modes')
    p.add_argument('--live', action='store_true', help='query mcplink status/tools; never call business tools')
    p = mcp.add_parser('bind')
    p.add_argument('--service', required=True)
    p.add_argument('--connection', required=True)
    p.add_argument('--source', action='append', help='explicit INSTANCE=DISCOVERED_SOURCE_ID; repeatable')
    p.add_argument('--native-name', action='append', help='select an existing alias: INSTANCE=NATIVE_NAME; repeatable')
    p.add_argument('--reviewed', action='store_true')
    p = mcp.add_parser('connect')
    p.add_argument('--skill', required=True)
    p.add_argument('--service', required=True)
    p.add_argument('--instance', action='append', required=True)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--plan')
    p.add_argument('--reviewed', action='store_true')
    for command in ('deploy', 'attach'):
        p = sub.add_parser(command)
        p.add_argument('--apply', action='store_true')
        if command == 'deploy':
            p.add_argument('--restore-missing', action='store_true')
            p.add_argument('--preserve-runtime', action='store_true')
            p.add_argument('--skill-id', action='append',
                           help='deploy only this registered skill (repeatable)')
            p.add_argument('--target', action='append', choices=agent_adapters.KINDS)
            p.add_argument('--instance', action='append')
            p.add_argument('--plan', help='required with --apply; preview must still match current state')
    p = sub.add_parser('workspace')
    p.add_argument('--agent', required=True)
    for command in ('accept', 'register', 'revision', 'enable', 'apply-project', 'recheck'):
        p = sub.add_parser(command)
        p.add_argument('--reviewed', action='store_true')
        if command not in ('apply-project', 'recheck'):
            p.add_argument('--agent', required=True)
        if command == 'accept':
            p.add_argument('candidate')
            p.add_argument('--kind', choices=['custom', 'third-party'])
            p.add_argument('--name')
            p.add_argument('--trial', action='store_true')
        elif command == 'register':
            p.add_argument('--path', required=True)
            p.add_argument('--targets', default='')
            p.add_argument('--trial', action='store_true')
        elif command in ('revision', 'enable'):
            p.add_argument('--skill-id', required=True)
            if command == 'enable':
                p.add_argument('--target', choices=[*agent_adapters.KINDS, 'shared'], required=True)
                p.add_argument('--path')
            else:
                p.add_argument('--trial', action='store_true')
                p.add_argument('--executable', action='append')
                p.add_argument('--not-executable', action='append')
        elif command == 'apply-project':
            p.add_argument('plan')
        else:
            p.add_argument('deployment')
    p = sub.add_parser('note')
    for flag in ('agent', 'scope', 'title', 'source', 'file'):
        p.add_argument('--' + flag, required=True)
    p.add_argument('--inbox', action='store_true', help='pending global Agent preferences or shared rules only')
    p = sub.add_parser('sync')
    p.add_argument('--agent', required=True)
    p.add_argument('--message', required=True)
    p.add_argument('--local-only', action='store_true')
    p.add_argument('--branch-only', action='store_true', help='publish an isolated codex/ branch for CI without merging main')
    p = sub.add_parser('init-project')
    p.add_argument('project')
    p.add_argument('--purpose', required=True)
    p.add_argument('--template', choices=['base', 'software', 'research', 'documents'], default='software')
    p.add_argument('--command', action='append', default=[])
    p = sub.add_parser('scheduler')
    p.add_argument('--interval', type=int, default=60)
    p.add_argument('--apply', action='store_true')
    p = sub.add_parser('discover')
    p.add_argument('query')
    p.add_argument('--limit', type=int, choices=range(1, 26), default=5)
    for name in ('inspect', 'fetch'):
        p = sub.add_parser(name)
        p.add_argument('source')
        p.add_argument('--skill')
        p.add_argument('--path')
        p.add_argument('--ref')
        p.add_argument('--local-source', action='store_true', help='explicit local Git fixture/source, never inferred from a link')
        p.add_argument('--reconsider', action='store_true')
        p.add_argument('--reviewed', action='store_true')
    p = sub.add_parser('evaluate')
    p.add_argument('candidate')
    p.add_argument('--decision', choices=['trial', 'adopt', 'reject', 'reference'])
    p.add_argument('--reason')
    p.add_argument('--use-case')
    p.add_argument('--agent')
    p.add_argument('--reviewed', action='store_true')
    p.add_argument('--reconsider', action='store_true')
    p = sub.add_parser('decision')
    identity = p.add_mutually_exclusive_group(required=True)
    identity.add_argument('--skill-id')
    identity.add_argument('--source')
    p.add_argument('--name')
    p.add_argument('--path')
    p.add_argument('--decision', choices=['retired', 'reject', 'allow'], required=True)
    p.add_argument('--reason', required=True)
    p.add_argument('--agent', required=True)
    p.add_argument('--reviewed', action='store_true')
    p = sub.add_parser('merge')
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--candidate')
    group.add_argument('--finalize')
    p.add_argument('--base-candidate')
    p.add_argument('--reviewed', action='store_true')
    p = sub.add_parser('promote')
    p.add_argument('--skill-id', required=True)
    p.add_argument('--ref', default='HEAD')
    p.add_argument('--agent', required=True)
    p.add_argument('--reason', required=True)
    p.add_argument('--without-trial', action='store_true', help='explicit reviewed adoption without claiming a business trial')
    p.add_argument('--reviewed', action='store_true')
    p = sub.add_parser('trial')
    trial = p.add_subparsers(dest='action', required=True)
    p = trial.add_parser('start')
    p.add_argument('--skill-id', required=True)
    p.add_argument('--ref', default='HEAD')
    p.add_argument('--target', choices=agent_adapters.KINDS, action='append', required=True)
    p.add_argument('--instance', action='append')
    p.add_argument('--path')
    p.add_argument('--agent', required=True)
    p.add_argument('--reviewed', action='store_true')
    p.add_argument('--apply', action='store_true')
    p.add_argument('--plan')
    p.add_argument('--preserve-runtime', action='store_true')
    p = trial.add_parser('finish')
    p.add_argument('trial_id')
    p.add_argument('--result', choices=['passed', 'failed'], required=True)
    p.add_argument('--evidence', required=True)
    p.add_argument('--reason', required=True)
    p.add_argument('--reviewed', action='store_true')
    p = trial.add_parser('rollback')
    p.add_argument('trial_id')
    p.add_argument('--reviewed', action='store_true')
    p = trial.add_parser('recover')
    p.add_argument('trial_id')
    p.add_argument('--reviewed', action='store_true')
    p = trial.add_parser('status')
    p.add_argument('trial_id', nargs='?')
    p = sub.add_parser('agent')
    agents = p.add_subparsers(dest='action', required=True)
    agents.add_parser('list')
    p = agents.add_parser('bundled', help='list Hermes bundled skill directory names for one instance')
    p.add_argument('--instance', required=True)
    p = agents.add_parser('add')
    p.add_argument('--kind', choices=agent_adapters.KINDS, required=True)
    p.add_argument('--name')
    p.add_argument('--skills-root')
    p.add_argument('--instructions')
    p.add_argument('--command-json')
    p.add_argument('--probe-command-json')
    p.add_argument('--runtime', help='reviewed native runtime root, including an app ASAR virtual path')
    p.add_argument('--interpreter', help='reviewed native executable used for read-only catalog discovery')
    p.add_argument('--reviewed', action='store_true')
    for name in ('enable', 'disable'):
        p = agents.add_parser(name)
        p.add_argument('--name', required=True)
        p.set_defaults(kind=None)
        p.add_argument('--reviewed', action='store_true')
    args = parser.parse_args(argv)
    try:
        if initialize_default:
            from .cli import initialize
            initialize([str(ROOT), '--git'])
        with locked():
            command = args.command_name
            if command in ('try', 'save', 'share'):
                if command in ('try', 'share') and not load(LOCAL / 'config.json'):
                    configure(argparse.Namespace(home=Path.home(), agents=None, gitleaks=None,
                                                 github_repository=''), on_request=True)
                result = quick_skills.perform(args)
            elif command == 'configure':
                result = configure(args)
            elif command == 'collect':
                result = collect()
            elif command == 'deploy':
                result = deploy(args.apply, args.restore_missing, args.preserve_runtime, args.skill_id,
                                args.target, args.instance, plan_id=args.plan)
            elif command == 'refresh':
                result = refresh()
            elif command == 'tick':
                result = tick()
            elif command == 'catalog':
                result = registry()[0]
            elif command in ('audit', 'doctor'):
                result = skill_audit.audit(args, native=getattr(args, 'native', False))
            elif command == 'index':
                result = skill_audit.index(args)
            elif command == 'resolve':
                result = skill_audit.resolve(args)
            elif command == 'mcp':
                result = skill_mcp.command(args)
            elif command == 'agent':
                result = agent_adapters.manage(args)
            elif command == 'discover':
                result = skill_sources.discover(args)
            elif command in ('inspect', 'fetch'):
                result = skill_sources.inspect(args)
            elif command == 'evaluate':
                result = skill_sources.evaluate(args)
            elif command == 'decision':
                result = skill_sources.decision(args)
            elif command == 'merge':
                result = skill_merge.merge(args)
            elif command == 'promote':
                result = skill_versions.promote(args)
            elif command == 'trial':
                result = {'start': skill_trials.start, 'finish': skill_trials.finish,
                          'rollback': skill_trials.rollback, 'recover': skill_trials.recover,
                          'status': skill_trials.status}[args.action](args)
            elif command == 'status':
                result = {'workspace': str(ROOT), 'primary': str(PRIMARY),
                          'publication_enabled': cfg()['publication_enabled'],
                          'git': git('status', '--short', '--branch').stdout,
                          'pending_candidates': len(load(LOCAL / 'last-collection.json', {}).get('candidates', [])),
                          'last_tick': load(LOCAL / 'last-tick.json', {})}
            else:
                result = {'accept': accept, 'register': register, 'revision': revision,
                          'enable': enable, 'workspace': workspace, 'note': note, 'sync': sync,
                          'attach': attach, 'init-project': init_project,
                          'apply-project': apply_project, 'scheduler': scheduler, 'recheck': recheck}[command](args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get('blocked') or result.get('status') in ('BLOCKED', 'UNVERIFIED') else 0
    except (Stop, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'status': 'STOPPED', 'reason': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
