"""Discover, fetch and evaluate skills without installing or executing them."""

import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import __version__
from .assetlib import (ROOT, LOCAL, Stop, TRANSIENT, file_modes, files_in, git, load, portable_paths,
                      registry, relative, save, scan, sha, tree_hash, write_tree, owned_path, library_path)
from .skill_audit import entry_fields


def source_key(origin):
    if origin.get('asset_id'):
        return 'asset:' + origin['asset_id']
    url = origin.get('url') or ''
    url = url.removesuffix('.git').rstrip('/')
    if urllib.parse.urlsplit(url).hostname == 'github.com':
        url = url.lower()
    return (url + '#'
            + (origin.get('path') or '').strip('/')) if url else ''


def known_source(record, root=ROOT):
    origin = dict(record.get('origin', {}))
    upstream = load(owned_path(record['path'] + '/UPSTREAM.json', root), {})
    origin['url'] = origin.get('url') or upstream.get('repository') or ''
    origin['path'] = origin.get('path') or upstream.get('path') or ''
    origin['commit'] = origin.get('commit') or upstream.get('ref') or origin.get('version')
    return origin


def review_identity(item):
    return sha(json.dumps({name: item.get(name) for name in (
        'origin', 'source', 'skill_id', 'sha256', 'bindings', 'base', 'candidate_type',
        'requires_merge', 'reconciled', 'original_sha256', 'license_file')}, sort_keys=True).encode())


def prior_decisions(origin, name=None):
    decisions = load(ROOT / 'skills/decisions.json', {'entries': []})['entries']
    identity = source_key(origin)
    exact = [x for x in decisions if (identity and x.get('source_key') == identity
             and x.get('source_scope', 'exact') == 'exact') or (
        x.get('source_scope') == 'repository-and-name' and x.get('name') == name and
        x.get('repository', '').removesuffix('.git').lower() == origin.get('url', '').removesuffix('.git').lower())]
    warnings = [x for x in decisions if x.get('name') == name and x not in exact]
    return exact, warnings


def decision(args):
    if not args.reviewed or not args.reason:
        raise Stop('record a current authorized decision with --reviewed and --reason')
    manifest, _ = registry()
    if args.skill_id:
        record = manifest['packages'][args.skill_id]
        origin, name = known_source(record), record['name']
        if not origin.get('url'):
            origin['asset_id'] = args.skill_id
    else:
        _, origin, _, suggested = parse_source(args.source, False)
        origin['path'] = args.path or ''
        relative(origin['path']) if origin['path'] else None
        name = args.name or suggested
        if not name:
            raise Stop('identify the skill name for this source decision')
    value = load(ROOT / 'skills/decisions.json', {'version': 1, 'entries': []})
    row = {'source_key': source_key(origin), 'name': name, 'decision': args.decision,
           'reason': args.reason, 'reviewed_by': args.agent,
           'reviewed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    if args.skill_id:
        row['skill_id'] = args.skill_id
    value['entries'].append(row)
    save(ROOT / 'skills/decisions.json', value)
    return {'decision': row, 'native_files_changed': False}


def discover(args):
    query = args.query.strip()
    if not 2 <= len(query) <= 200:
        raise Stop('use a search query between 2 and 200 characters')
    if query.startswith(('https://', 'http://')):
        return {'source': query, 'next': 'inspect or fetch the exact source; no native installation performed'}
    url = 'https://skills.sh/api/search?' + urllib.parse.urlencode({'q': query})
    request = urllib.request.Request(url, headers={'User-Agent': f'AgentAssetKit/{__version__}'})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read(4 * 1024**2 + 1)
        if len(raw) > 4 * 1024**2:
            raise Stop('skill directory response exceeds limit')
        data = json.loads(raw)
    except (urllib.error.URLError, ValueError) as error:
        raise Stop('skills.sh search unavailable; browse the directory or provide a direct source URL') from error
    manifest, _ = registry()
    results = []
    if not isinstance(data, dict) or not isinstance(data.get('skills'), list):
        raise Stop('skills.sh response changed; use its browser search and direct source URLs')
    for value in data['skills'][:args.limit]:
        if not isinstance(value, dict):
            continue
        name, source = value.get('name', ''), value.get('source', '')
        if not isinstance(name, str) or not isinstance(source, str) or not re.fullmatch(r'[\w.-]+/[\w.-]+', source):
            continue
        related = [v['name'] for v in manifest['packages'].values()
                   if v['name'] == name or known_source(v).get('url', '').endswith('/' + source)]
        exact, warnings = prior_decisions({'url': 'https://github.com/' + source, 'path': ''}, name)
        ident = value.get('id') or source + '/' + name
        if not isinstance(ident, str) or not re.fullmatch(r'[\w./-]+', ident):
            continue
        results.append({'name': name, 'source': source, 'url': 'https://skills.sh/' + ident,
                        'installed_related': related, 'previous_decisions': exact + warnings})
    return {'query': query, 'results': results, 'meaning': 'Discovery signals only; inspect complete contents before review.'}


def parse_source(value, local=False):
    if local:
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise Stop('explicit local Git source is not a directory')
        return str(path), {'kind': 'third-party', 'url': path.as_uri(), 'path': ''}, None, None
    if re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value):
        value = 'https://github.com/' + value
    url = urllib.parse.urlsplit(value)
    if url.scheme != 'https' or url.username or url.password or url.query or url.fragment:
        raise Stop('use a credential-free HTTPS source URL; authenticate through native Git')
    parts = [urllib.parse.unquote(p) for p in url.path.strip('/').split('/')]
    if url.hostname in ('skills.sh', 'www.skills.sh') and len(parts) == 3:
        owner, repo, skill = parts
        if not all(re.fullmatch(r'[A-Za-z0-9_.-]+', x) for x in parts):
            raise Stop('invalid skills.sh source identity')
        upstream = 'https://github.com/' + owner + '/' + repo
        return upstream + '.git', {'kind': 'third-party', 'url': upstream, 'path': ''}, None, skill
    if url.hostname != 'github.com' or len(parts) < 2:
        raise Stop('supported sources are skills.sh and GitHub; use --local-source only for an explicit local Git repository')
    owner, repo = parts[:2];repo = repo.removesuffix('.git')
    if not all(re.fullmatch(r'[A-Za-z0-9_.-]+', x) for x in (owner, repo)):
        raise Stop('invalid GitHub repository')
    suffix = parts[2:]
    hint = None
    if suffix:
        if suffix[0] not in ('tree', 'blob') or len(suffix) < 2:
            raise Stop('use a repository, tree or SKILL.md GitHub URL')
        hint = suffix[1:]
    upstream = 'https://github.com/' + owner + '/' + repo
    return upstream + '.git', {'kind': 'third-party', 'url': upstream, 'path': ''}, hint, None


def git_bytes(root, *argv):
    process = subprocess.run(['git', *argv], cwd=root, capture_output=True, timeout=60)
    if process.returncode:
        raise Stop('source Git operation failed; content and native installations are unchanged')
    return process.stdout


def read_blob(root, entry, limit=40 * 1024**2):
    mode, kind, oid = entry
    if kind != 'blob' or mode not in ('100644', '100755'):
        raise Stop('upstream skill contains links or special files; review its packaging before import')
    # ls-tree -l would hydrate every blob in a partial clone just to enumerate
    # the repository. Resolve sizes only for explicitly selected resources.
    size = int(git_bytes(root, 'cat-file', '-s', oid))
    if size > limit:
        raise Stop('upstream resource exceeds collection limits')
    data = git_bytes(root, 'cat-file', 'blob', oid)
    if len(data) != size:
        raise Stop('upstream resource changed while reading immutable objects')
    return data


def inspect(args):
    locator, origin, hint, suggested_name = parse_source(args.source, args.local_source)
    LOCAL.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='upstream-', dir=LOCAL) as temporary:
        repo = Path(temporary) / 'repository'
        flags = [] if args.local_source else ['--filter=blob:none']
        git_bytes(LOCAL, 'clone', '--bare', '--no-hardlinks', *flags, '--', locator, str(repo))
        ref, subpath = args.ref, args.path
        if hint:
            if ref or subpath:
                raise Stop('choose either URL revision/path or explicit --ref/--path')
            # Longest existing branch/tag prefix also handles refs containing '/'.
            for length in range(len(hint), 0, -1):
                proposal = '/'.join(hint[:length])
                p = subprocess.run(['git', 'rev-parse', '--verify', proposal + '^{commit}'], cwd=repo,
                                   capture_output=True, text=True, encoding='utf-8', timeout=30)
                if not p.returncode:
                    ref, subpath = proposal, '/'.join(hint[length:]);break
            if not ref:
                raise Stop('the URL revision could not be resolved')
        ref = ref or 'HEAD'
        if ref.startswith('-') or any(c.isspace() for c in ref):
            raise Stop('invalid upstream revision')
        commit = git_bytes(repo, 'rev-parse', '--verify', ref + '^{commit}').decode().strip()
        tree = git_bytes(repo, 'ls-tree', '-r', '-z', commit).decode('utf-8')
        entries = {}
        for row in filter(None, tree.split('\0')):
            meta, path = row.split('\t', 1);mode, kind, oid = meta.split()
            entries[path] = (mode, kind, oid)
            if len(entries) > 100_000:
                raise Stop('upstream tree exceeds bounded discovery limit')
        available = [p[:-len('/SKILL.md')] if '/' in p else '' for p in entries if p.endswith('SKILL.md')
                     and Path(p).name == 'SKILL.md' and not any(x in Path(p).parts for x in ('.system', 'node_modules', '.git'))]
        selector = args.skill or suggested_name
        if subpath and subpath.endswith('/SKILL.md'):
            subpath = subpath[:-len('/SKILL.md')]
        if subpath is not None:
            relative(subpath) if subpath else None
            selected = [p for p in available if p == subpath.rstrip('/')]
        elif selector:
            selected = []
            for path in available:
                entry = (path + '/' if path else '') + 'SKILL.md'
                body = read_blob(repo, entries[entry], limit=1024 * 1024).decode('utf-8')
                match = re.search(r'^name:\s*[\'"]?([^\r\n\'"]+)', body, re.M)
                if Path(path).name == selector or (match and match[1].strip() == selector):
                    selected.append(path)
        else:
            selected = available
        if len(selected) != 1:
            return {'status': 'SELECTION_REQUIRED', 'source': origin['url'], 'commit': commit,
                    'available_paths': available, 'matches': selected,
                    'next': 'select one exact --skill or --path'}
        subpath = selected[0];origin.update(path=subpath, version=commit, commit=commit)
        prefix = subpath + '/' if subpath else ''
        files, total = {}, 0
        for path, (mode, kind, oid) in entries.items():
            if not path.startswith(prefix):
                continue
            name = path[len(prefix):]
            if any(x in TRANSIENT for x in Path(name).parts) or name.endswith(('.pyc', '.log')):
                continue
            if kind != 'blob' or mode not in ('100644', '100755'):
                raise Stop('upstream skill contains links or special files; review its packaging before import')
            if len(files) >= 3000:
                raise Stop('upstream skill exceeds collection limits')
            data = read_blob(repo, (mode, kind, oid), min(40 * 1024**2, 180 * 1024**2 - total))
            total += len(data)
            files[name] = (data, 0o755 if mode == '100755' else 0o644)
        portable_paths(files)
        license_name = next((p for p in files if re.fullmatch(r'(LICENSE|COPYING)(\.[\w-]+)?', p, re.I)), None)
        if not license_name:
            ancestors = [Path(subpath), *Path(subpath).parents] if subpath else [Path('.')]
            for parent in ancestors:
                candidates = [p for p in entries if Path(p).parent == parent and re.fullmatch(r'(LICENSE|COPYING)(\.[\w-]+)?', Path(p).name, re.I)]
                if candidates:
                    path = candidates[0];mode, kind, oid = entries[path]
                    if kind == 'blob' and mode in ('100644', '100755'):
                        license_name = Path(path).name
                        files[license_name] = (read_blob(repo, entries[path], limit=1024 * 1024), 0o644)
                        origin['local_changes'] = 'Included upstream license from ' + path
                    break
        stage = Path(temporary) / 'package';write_tree(stage, files)
        fields = entry_fields(stage / 'SKILL.md')
        # Apply the same bounded filename/credential policy as native collection.
        if tree_hash(files_in(stage, file_modes(files))) != tree_hash(files):
            raise Stop('upstream contains runtime/ignored files; review an explicitly packaged skill')
        scan(stage)
        exact, warnings = prior_decisions(origin, fields['name'])
        blocked = bool(exact and exact[-1].get('decision') in ('reject', 'retired')) or (not exact and any(
            x.get('source_scope') == 'identity-unresolved' and x.get('decision') == 'retired' for x in warnings)
        )
        if blocked and not (args.reconsider and args.reviewed):
            raise Stop('source was previously rejected/retired; explicit --reconsider --reviewed is required')
        manifest, _ = registry()
        duplicate = [i for i, value in manifest['packages'].items() if source_key(known_source(value)) == source_key(origin)]
        result = {'name': fields['name'], 'description': fields['description'], 'origin': origin,
                  'sha256': tree_hash(files), 'file_modes': file_modes(files), 'files': len(files),
                  'license_file': license_name, 'existing_skill_ids': duplicate, 'previous_decisions': exact + warnings,
                  'native_installation': 'not performed'}
        if args.command_name != 'fetch':
            return result
        ident = sha((source_key(origin) + commit + tree_hash(files)).encode())[:20]
        destination = LOCAL / 'candidates' / ident
        library_path(destination)
        if destination.exists():
            previous = load(destination / 'candidate.json')
            if previous['sha256'] != result['sha256']:
                raise Stop('existing candidate identity differs; content retained')
            return dict(result, candidate=ident, package=str(destination / 'package'))
        destination.mkdir(parents=True)
        write_tree(destination / 'package', files)
        value = dict(result, id=ident, source=origin['url'], skill_id=duplicate[0] if len(duplicate) == 1 else None,
                     bindings=[], base={}, candidate_type='external', discovered_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        if value['skill_id']:
            value['base'] = {'repository': manifest['packages'][value['skill_id']]['sha256']}
            value['base_origin'] = known_source(manifest['packages'][value['skill_id']])
            value['requires_merge'] = value['sha256'] != manifest['packages'][value['skill_id']]['sha256']
        save(destination / 'candidate.json', value)
        return dict(result, candidate=ident, package=str(destination / 'package'))


def evaluate(args):
    folder = LOCAL / 'candidates' / relative(args.candidate)
    item = load(folder / 'candidate.json')
    if not item:
        raise Stop('candidate does not exist')
    files = files_in(folder / 'package', item.get('file_modes'))
    if tree_hash(files) != item['sha256']:
        raise Stop('candidate changed; fetch/collect a new snapshot')
    fields = entry_fields(folder / 'package/SKILL.md')
    scan(folder / 'package')
    if not args.decision:
        return {'candidate': item['id'], 'fields': fields, 'origin': item['origin'], 'files': sorted(files),
                'license_file': item.get('license_file'), 'existing_skill_ids': item.get('existing_skill_ids', []),
                'previous_decisions': prior_decisions(item['origin'], fields['name']),
                'required_review': ['use case and overlap', 'complete instructions/scripts', 'license and origin',
                                    'Agent tools and dependencies', 'scope and trial verification']}
    if not args.reviewed or not args.reason or not args.use_case or not args.agent:
        raise Stop('semantic evaluation requires --reviewed, --reason and --use-case')
    identity = dict(item['origin'])
    if not identity.get('url') and item.get('skill_id'):
        identity['asset_id'] = item['skill_id']
    exact, warnings = prior_decisions(identity, fields['name'])
    blocked = bool(exact and exact[-1].get('decision') in ('reject', 'retired')) or (not exact and any(
        x.get('source_scope') == 'identity-unresolved' and x.get('decision') == 'retired' for x in warnings)
    )
    if args.decision in ('trial', 'adopt') and blocked and not args.reconsider:
        raise Stop('previous rejection requires explicit --reconsider after current user authorization')
    if args.decision in ('trial', 'adopt') and item.get('candidate_type') == 'external' and not item.get('license_file'):
        raise Stop('external license is unknown; retain a reference or establish authorization before distribution')
    review = {'sha256': item['sha256'], 'decision': args.decision, 'reason': args.reason,
              'identity_sha256': review_identity(item),
              'use_case': args.use_case, 'reviewed_by': args.agent,
              'reviewed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    save(folder / 'evaluation.json', review)
    if args.decision in ('reject', 'reference'):
        decisions = load(ROOT / 'skills/decisions.json', {'version': 1, 'entries': []})
        decisions['entries'].append(dict(review, source_key=source_key(identity), name=fields['name']))
        save(ROOT / 'skills/decisions.json', decisions)
    return {'candidate': item['id'], 'evaluation': review, 'next': 'accept --reviewed' if args.decision in ('trial', 'adopt') else 'no installation'}
