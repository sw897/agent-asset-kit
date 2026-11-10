"""Three-way reconciliation retaining original, incoming and repository trees."""

from pathlib import Path
import subprocess
from types import SimpleNamespace
import urllib.parse
import urllib.request
import uuid

from .assetlib import (ROOT, LOCAL, Stop, file_modes, files_in, load, package,
                      registry, relative, save, scan, sha, tree_hash, write_tree)
from .skill_versions import find_base


def merge(args):
    if args.finalize:
        return finalize(args)
    folder = LOCAL / 'candidates' / relative(args.candidate)
    item = load(folder / 'candidate.json')
    if not item or not item.get('skill_id'):
        raise Stop('merge requires an existing stable skill identity')
    manifest, _ = registry()
    record = manifest['packages'][item['skill_id']]
    _, repository = package(ROOT, record)
    incoming = files_in(folder / 'package', item.get('file_modes'))
    if tree_hash(incoming) != item['sha256']:
        raise Stop('incoming candidate changed')
    if item.get('candidate_type') == 'external':
        from .skill_sources import inspect, source_key
        if args.base_candidate:
            base_item = load(LOCAL / 'candidates' / relative(args.base_candidate) / 'candidate.json')
        else:
            origin = item.get('base_origin', {})
            if not origin.get('commit'):
                raise Stop('upstream baseline unknown; provide a reviewed --base-candidate')
            url = urllib.parse.urlsplit(origin['url'])
            source = urllib.request.url2pathname(url.path) if url.scheme == 'file' else origin['url']
            result = inspect(SimpleNamespace(source=source, local_source=url.scheme == 'file',
                ref=origin['commit'], path=origin.get('path', ''), skill=None,
                reconsider=False, reviewed=False, command_name='fetch'))
            if not result.get('candidate'):
                raise Stop('upstream baseline cannot be located unambiguously')
            base_item = load(LOCAL / 'candidates' / result['candidate'] / 'candidate.json')
        if not base_item or source_key(base_item['origin']) != source_key(item['origin']):
            raise Stop('upstream baseline belongs to a different source')
        base = files_in(LOCAL / 'candidates' / base_item['id'] / 'package', base_item.get('file_modes'))
        if tree_hash(base) != base_item['sha256']:
            raise Stop('upstream baseline candidate changed')
        base_ref = base_item['origin']['commit']
    else:
        digests = set(filter(None, item.get('base', {}).values()))
        if len(digests) != 1:
            raise Stop('candidate must have one identifiable common baseline')
        preferred = next((v.get('commit') for v in item.get('base_versions', {}).values() if v.get('commit')), None)
        base, base_ref = find_base(item['skill_id'], next(iter(digests)), preferred)
    ident = uuid.uuid4().hex
    run = LOCAL / 'merges' / ident
    for label, files in [('base', base), ('incoming', incoming), ('repository', repository)]:
        write_tree(run / label, files)
    output, conflicts = {}, []
    labels = ['incoming:' + ident, 'base:' + ident, 'repository:' + ident]
    for name in sorted(set(base) | set(incoming) | set(repository)):
        b, l, r = base.get(name), incoming.get(name), repository.get(name)
        if l == r:
            chosen = l
        elif l == b:
            chosen = r
        elif r == b:
            chosen = l
        else:
            chosen = r or l
            if l is not None and r is not None and b is not None:
                try:
                    for value in (b, l, r):
                        if b'\0' in value[0]:
                            raise UnicodeError()
                        value[0].decode('utf-8')
                    command = ['git', 'merge-file', '-p', '--diff3']
                    for label in labels:
                        command += ['-L', label]
                    process = subprocess.run(command + [str(run / 'incoming' / name), str(run / 'base' / name),
                        str(run / 'repository' / name)], capture_output=True, timeout=30)
                    if process.returncode < 0 or process.returncode > 127:
                        raise Stop('Git text merge failed; all three versions retained')
                    mode = l[1] if r[1] == b[1] else r[1]
                    chosen = (process.stdout, mode)
                    if process.returncode:
                        conflicts.append(name)
                except UnicodeError:
                    conflicts.append(name)
            else:
                conflicts.append(name)
        if chosen is not None:
            output[name] = chosen
    write_tree(run / 'merged', output)
    receipt = {'id': ident, 'candidate': item['id'], 'item': item, 'skill_id': item['skill_id'],
               'base_ref': base_ref, 'repository_sha256': record['sha256'], 'conflicts': conflicts,
               'labels': labels, 'file_modes': file_modes(output), 'state': 'proposed'}
    save(run / 'merge.json', receipt)
    return {'merge_id': ident, 'merged_package': str(run / 'merged'), 'conflicts': conflicts,
            'versions_retained': ['base', 'incoming', 'repository'],
            'provenance_review': 'Review UPSTREAM/source metadata when integrating upstream changes.',
            'next': 'resolve files, then merge --finalize ID --reviewed'}


def finalize(args):
    if not args.reviewed:
        raise Stop('review all merged contents, then pass --reviewed')
    run = LOCAL / 'merges' / relative(args.finalize)
    receipt = load(run / 'merge.json')
    if not receipt or receipt['state'] != 'proposed':
        raise Stop('merge is not awaiting review')
    manifest, _ = registry()
    record = manifest['packages'][receipt['skill_id']]
    if record['sha256'] != receipt['repository_sha256']:
        raise Stop('repository changed after merge proposal; generate a fresh reconciliation')
    files = files_in(run / 'merged', receipt['file_modes'])
    if 'SKILL.md' not in files:
        raise Stop('merged skill entry is missing')
    for name in receipt['conflicts']:
        data = files.get(name, (b'', 0))[0]
        if any(label.encode() in data for label in receipt['labels']):
            raise Stop('unresolved conflict markers remain: ' + name)
    scan(run / 'merged')
    ident = sha((receipt['id'] + tree_hash(files)).encode())[:20]
    target = LOCAL / 'candidates' / ident
    item = dict(receipt['item'], id=ident, sha256=tree_hash(files), file_modes=file_modes(files),
                base={'repository': record['sha256']}, reconciled=True, merge_id=receipt['id'])
    item['original_source'] = receipt['item']['source']
    item['original_sha256'] = receipt['item']['sha256']
    write_tree(target / 'package', files)
    save(target / 'candidate.json', item)
    evaluation = load(LOCAL / 'candidates' / receipt['candidate'] / 'evaluation.json')
    if evaluation:
        from .skill_sources import review_identity
        save(target / 'evaluation.json', dict(evaluation, sha256=item['sha256'],
             identity_sha256=review_identity(item), merge_reviewed=True))
    receipt['state'] = 'reviewed';save(run / 'merge.json', receipt)
    return {'candidate': ident, 'sha256': item['sha256'], 'next': 'accept the reconciled candidate --reviewed'}
