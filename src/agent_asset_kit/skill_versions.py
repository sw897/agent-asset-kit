"""Immutable Git package snapshots and stable/trial release selection."""

import json
import re
import subprocess

from .assetlib import (ROOT, PRIMARY, LOCAL, Stop, file_modes, files_in, git, load, package,
                      portable_paths, relative, save, tree_hash)


def commit_id(ref, root=ROOT):
    if not ref or ref.startswith('-') or any(c.isspace() for c in ref):
        raise Stop('invalid Git revision')
    result = git('rev-parse', '--verify', ref + '^{commit}', cwd=root).stdout.strip()
    if not re.fullmatch(r'[0-9a-f]{40,64}', result):
        raise Stop('revision did not resolve to a commit')
    return result


def snapshot(ref, ident, root=ROOT):
    commit = commit_id(ref, root)
    manifest = json.loads(git('show', commit + ':skills/manifest.json', cwd=root).stdout)
    record = manifest.get('packages', {}).get(ident)
    if not record:
        raise Stop('skill is absent from the selected committed revision')
    prefix = relative(record['path']).as_posix().rstrip('/') + '/'
    if not prefix.startswith(('skills/custom/', 'skills/third-party/')):
        raise Stop('invalid versioned package location')
    entries = git('ls-tree', '-r', '-l', '-z', commit, '--', record['path'], cwd=root).stdout
    objects, total = [], 0
    for entry in filter(None, entries.split('\0')):
        meta, path = entry.split('\t', 1)
        mode, kind, oid, length = meta.split()
        if kind != 'blob' or mode not in ('100644', '100755') or not path.startswith(prefix):
            raise Stop('versioned package contains a non-regular file')
        length = int(length)
        total += length
        if length > 40 * 1024**2 or total > 180 * 1024**2 or len(objects) >= 3000:
            raise Stop('versioned package exceeds collection limits')
        objects.append((path[len(prefix):], oid, 0o755 if mode == '100755' else 0o644))
    portable_paths(name for name, _, _ in objects)
    if not objects:
        raise Stop('versioned package is empty')
    request = ''.join(oid + '\n' for _, oid, _ in objects).encode()
    result = subprocess.run(['git', 'cat-file', '--batch'], input=request, cwd=root,
                            capture_output=True, timeout=60)
    if result.returncode:
        raise Stop('unable to read immutable package objects')
    position, files = 0, {}
    for name, oid, mode in objects:
        end = result.stdout.find(b'\n', position)
        header = result.stdout[position:end].decode('ascii').split()
        if len(header) != 3 or header[:2] != [oid, 'blob']:
            raise Stop('unexpected Git object response')
        size = int(header[2]); position = end + 1
        files[name] = (result.stdout[position:position + size], mode)
        position += size + 1
    if 'SKILL.md' not in files or tree_hash(files) != record['sha256']:
        raise Stop('committed package does not match its reviewed manifest')
    return dict(record, file_modes=file_modes(files)), files, commit


def effective(ident, record, root=PRIMARY):
    from .skill_sources import known_source, source_key
    origin = known_source(record, root)
    identity = source_key(origin)
    entries = load(root / 'skills/decisions.json', {}).get('entries', [])
    decisions = [e for e in entries if e.get('skill_id') == ident or
                 (identity and e.get('source_key') == identity and e.get('source_scope', 'exact') == 'exact') or
                 (e.get('source_scope') == 'repository-and-name' and e.get('name') == record['name'] and
                  e.get('repository', '').removesuffix('.git').lower() == origin.get('url', '').removesuffix('.git').lower())]
    if decisions and decisions[-1]['decision'] == 'retired':
        return None, None, None
    release = load(root / 'skills/releases.json', {}).get('skills', {}).get(ident)
    if release and release.get('stable'):
        if not (root / '.git').exists():
            raise Stop('this stable release refers to a Git commit; use a clone with the library history')
        stable = release['stable']
        selected, files, commit = snapshot(stable['commit'], ident, root)
        if selected['sha256'] != stable['sha256']:
            raise Stop('stable release hash differs from its immutable revision')
        # Bindings are catalog policy; executable requirements belong to the
        # selected version, not a newer revision still awaiting trial.
        return dict(selected, bindings=record['bindings']), files, commit
    if record.get('lifecycle') == 'reviewed':
        return None, None, None
    _, files = package(root, record)
    if not (root / '.git').exists():
        return record, files, None
    head = git('rev-parse', '--verify', 'HEAD', cwd=root, check=False)
    return record, files, head.stdout.strip() if not head.returncode else None


def release_change(manifest, ident, old, pending, persist=True):
    path = ROOT / 'skills/releases.json'
    releases = load(path, {'version': 1, 'skills': {}})
    if pending:
        entry = releases['skills'].get(ident)
        if entry is None:
            stable = None
            if old and old.get('lifecycle') != 'reviewed':
                previous, _, commit = snapshot('HEAD', ident)
                if previous['sha256'] != old['sha256']:
                    raise Stop('commit the reviewed baseline before staging a trial revision')
                stable = {'commit': commit, 'sha256': old['sha256']}
            entry = {'stable': stable}
        entry['pending_sha256'] = manifest['packages'][ident]['sha256']
        releases['skills'][ident] = entry
        manifest['packages'][ident]['lifecycle'] = 'reviewed'
        manifest['version'] = 2  # Old deployers must stop rather than install a trial globally.
    else:
        releases['skills'].pop(ident, None)
        manifest['packages'][ident].pop('lifecycle', None)
    if persist and (releases['skills'] or path.exists()):
        save(path, releases)
    return releases


def promote(args):
    from .assetlib import LOCAL, cfg, registry
    if not args.reviewed or not args.reason:
        raise Stop('review verification evidence and provide --reviewed and --reason')
    manifest, _ = registry()
    record = manifest['packages'][args.skill_id]
    selected, _, commit = snapshot(args.ref, args.skill_id)
    if selected['sha256'] != record['sha256']:
        raise Stop('the catalog advanced after this trial; review and test the current revision')
    if not args.without_trial:
        trials = [load(p) for p in (LOCAL / 'trials').glob('*/trial.json')]
        passed = [t for t in trials if t.get('skill_id') == args.skill_id and
                  t.get('sha256') == record['sha256'] and t.get('result') == 'passed']
        if not passed:
            raise Stop('record a passing trial first, or explicitly review --without-trial evidence')
    releases = load(ROOT / 'skills/releases.json', {'version': 1, 'skills': {}})
    releases['skills'][args.skill_id] = {
        'stable': {'commit': commit, 'sha256': record['sha256']},
        'reason': args.reason, 'reviewed_by': args.agent}
    record.pop('lifecycle', None)
    manifest['version'] = 2
    save(ROOT / 'skills/releases.json', releases)
    save(ROOT / 'skills/manifest.json', manifest)
    for path in (LOCAL / 'trials').glob('*/trial.json'):
        trial = load(path)
        if trial.get('skill_id') == args.skill_id and trial.get('sha256') == record['sha256'] and trial.get('state') == 'passed':
            trial['state'] = 'adopted';save(path, trial)
    return {'promoted': args.skill_id, 'commit': commit, 'sha256': record['sha256'],
            'native_deployment': 'preview and deploy on each computer'}


def find_base(ident, digest, preferred=None):
    # Quick saves can be shared and edited before the first Git commit.
    saved = load(LOCAL / 'saved-versions' / relative(ident) / relative(digest + '.json'))
    if saved:
        folder = LOCAL / 'candidates' / relative(saved['candidate'])
        item = load(folder / 'candidate.json')
        if item and item['sha256'] == digest:
            files = files_in(folder / 'package', item.get('file_modes'))
            if tree_hash(files) == digest:
                return files, 'candidate:' + item['id']
    refs = [preferred] if preferred else []
    refs += git('log', '-n', '300', '--format=%H', '--', 'skills/manifest.json').stdout.splitlines()
    for ref in dict.fromkeys(filter(None, refs)):
        try:
            record, files, commit = snapshot(ref, ident)
            if record['sha256'] == digest:
                return files, commit
        except (Stop, ValueError, KeyError):
            continue
    raise Stop('common baseline is not available in Git; preserve the candidate and locate its origin')
