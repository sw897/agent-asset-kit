"""Recoverable package acceptance; preserve unknown edits and every prior copy."""

from pathlib import Path
import json
import re
import uuid

from .assetlib import (ROOT, PRIMARY, LOCAL, Stop, atomic, canonical_path, cli_display,
                      file_modes, files_in, library_path, load, package, rename_new, save,
                      sha, tree_hash, write_tree)


METADATA = {'manifest': 'skills/manifest.json', 'releases': 'skills/releases.json',
            'decisions': 'skills/decisions.json', 'approvals': None}
FINISHED = {'COMPLETE', 'ROLLED_BACK'}


def metadata_path(name):
    if name not in METADATA:
        raise Stop('unknown acceptance metadata file')
    return library_path(ROOT / METADATA[name] if METADATA[name] else LOCAL / 'accepted-copies.json')


def read_bytes(path):
    library_path(path)
    if path.exists() and not path.is_file():
        raise Stop('acceptance metadata must be a regular file')
    return path.read_bytes() if path.exists() else None


def digest(data):
    return sha(data) if data is not None else None


def capture():
    return {name: {'data': read_bytes(path), 'mode': path.stat().st_mode & 0o777 if path.exists() else 0o600}
            for name in METADATA for path in [metadata_path(name)]}


def pending():
    library_path(LOCAL / 'acceptances')
    return [{'id': path.stem, 'root': receipt.get('root', str(ROOT)), 'state': receipt['state']}
            for path in sorted((LOCAL / 'acceptances').glob('*.json'))
            for receipt in [load(path)] if receipt['state'] not in FINISHED]


def guard():
    for receipt in pending():
        command = cli_display(Path(receipt['root']))
        raise Stop(f'unfinished acceptance {receipt["id"]}; inspect preserved copies, then run '
                   f'{command} accept --recover {receipt["id"]} --agent AGENT --reviewed')


def paths(receipt):
    if (receipt.get('version') != 1 or not re.fullmatch(r'[a-f0-9]{32}', receipt.get('id', '')) or
            receipt.get('root') != str(ROOT) or receipt.get('primary') != str(PRIMARY)):
        raise Stop('acceptance receipt needs its original workspace and supported recovery format; preserve it for review')
    part = Path(receipt['path'])
    if len(part.parts) != 3:
        raise Stop('invalid acceptance package path')
    target = canonical_path(ROOT, {'path': receipt['path'], 'name': part.name, 'origin': {'kind': part.parts[1]}})
    ident = receipt['id']
    return tuple(library_path(path) for path in (
        target, target.parent / '.asset-staging' / ident,
        target.parent / '.asset-backups' / ident,
        target.parent / '.asset-backups' / (ident + '-interrupted'),
        LOCAL / 'acceptances' / ident, LOCAL / 'acceptances' / (ident + '.json')))


def matches(path, record):
    library_path(path)
    return bool(record and path.exists() and tree_hash(files_in(path, record['file_modes'])) == record['sha256'])


def snapshot(directory, name, phase, expected):
    if expected is None:
        return None
    data = read_bytes(directory / (name + '.' + phase))
    if digest(data) != expected:
        raise Stop('acceptance metadata snapshot changed; originals retained for review')
    return data


def rollback(receipt):
    target, stage, backup, retained, directory, journal = paths(receipt)
    before, after = receipt['before'], receipt['after']
    original = matches(target, before)
    incoming = matches(target, after)
    if target.exists() and not (original or incoming):
        raise Stop('canonical package changed after acceptance; preserve edits before recovery')
    if backup.exists() and not matches(backup, before):
        raise Stop('acceptance backup changed; originals retained for review')
    if before and not original and not backup.exists():
        raise Stop('previous package backup is missing; restore it before recovery')
    if target.exists() and not original and retained.exists():
        raise Stop('retained acceptance copy already exists; preserve and reconcile both copies')
    # Validate every file before restoring any directory or metadata. A retry
    # accepts only the exact pre-transaction or prepared bytes, never later edits.
    snapshots = {}
    for name, row in receipt['metadata'].items():
        current = digest(read_bytes(metadata_path(name)))
        if current not in (row['before'], row['after']):
            raise Stop('acceptance metadata changed after interruption; preserve edits before recovery')
        snapshots[name] = snapshot(directory, name, 'before', row['before'])
        snapshot(directory, name, 'after', row['after'])
    receipt['state'] = 'ROLLING_BACK'
    save(journal, receipt)
    if target.exists() and not original:
        if not matches(target, after):
            raise Stop('canonical package changed during recovery')
        retained.parent.mkdir(parents=True, exist_ok=True)
        rename_new(target, retained)
        if not matches(retained, after):
            raise Stop('retained package changed during recovery; all copies preserved')
    if before and not original:
        if not matches(backup, before):
            raise Stop('previous package backup changed during recovery')
        rename_new(backup, target)
    for name, row in receipt['metadata'].items():
        path = metadata_path(name)
        current = digest(read_bytes(path))
        if current == row['before']:
            continue
        if current != row['after']:
            raise Stop('acceptance metadata changed during recovery')
        if snapshots[name] is None:
            retained_metadata = library_path(path.parent / '.asset-backups' / (receipt['id'] + '-' + name + '.json'))
            retained_metadata.parent.mkdir(parents=True, exist_ok=True)
            rename_new(path, retained_metadata)
        else:
            atomic(path, snapshots[name], row['mode'])
    receipt['state'] = 'ROLLED_BACK'
    save(journal, receipt)
    return {'recovered': receipt['id'], 'state': receipt['state'],
            'retained_package': str(retained) if retained.exists() else None}


def recover(ident):
    if not re.fullmatch(r'[a-f0-9]{32}', ident):
        raise Stop('invalid acceptance recovery ID')
    receipt = load(LOCAL / 'acceptances' / (ident + '.json'))
    if not receipt or receipt.get('id') != ident:
        raise Stop('acceptance recovery receipt does not exist or needs manual reconciliation')
    paths(receipt)
    if receipt['state'] in FINISHED:
        return {'recovered': ident, 'state': receipt['state'], 'already_finished': True}
    return rollback(receipt)


def apply(candidate, ident, previous, updated, files, updates, before):
    transaction = uuid.uuid4().hex
    previous_modes = file_modes(package(ROOT, previous)[1]) if previous else {}
    receipt = {'version': 1, 'id': transaction, 'root': str(ROOT), 'primary': str(PRIMARY),
               'path': updated['path'], 'candidate': candidate, 'skill_id': ident,
               'state': 'PREPARED', 'metadata': {},
               'before': {'sha256': previous['sha256'], 'file_modes': previous_modes} if previous else None,
               'after': {'sha256': updated['sha256'], 'file_modes': file_modes(files)}}
    target, stage, backup, retained, directory, journal = paths(receipt)
    stage.mkdir(parents=True)
    write_tree(stage, files)
    # Write and flush all snapshots before changing canonical state. This catches
    # storage/size errors early and leaves complete recovery inputs after a crash.
    for name, value in updates.items():
        data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()
        old = before[name]
        if read_bytes(metadata_path(name)) != old['data']:
            raise Stop('asset metadata changed while preparing acceptance; collect/review again')
        if old['data'] is not None:
            atomic(directory / (name + '.before'), old['data'])
        atomic(directory / (name + '.after'), data)
        receipt['metadata'][name] = {'before': digest(old['data']), 'after': sha(data), 'mode': old['mode']}
    save(journal, receipt)
    try:
        if (target.exists() and not matches(target, receipt['before'])) or (previous and not target.exists()):
            raise Stop('canonical package changed while preparing acceptance')
        if not matches(stage, receipt['after']):
            raise Stop('prepared acceptance package changed')
        for name in updates:
            if read_bytes(metadata_path(name)) != before[name]['data']:
                raise Stop('asset metadata changed while preparing acceptance')
        receipt['state'] = 'SWITCHING'
        save(journal, receipt)
        if previous:
            backup.parent.mkdir(parents=True, exist_ok=True)
            rename_new(target, backup)
            if not matches(backup, receipt['before']):
                raise Stop('original package changed during acceptance; backup retained')
        rename_new(stage, target)
        if not matches(target, receipt['after']):
            raise Stop('accepted package failed readback; originals retained')
        for name, row in receipt['metadata'].items():
            if read_bytes(metadata_path(name)) != before[name]['data']:
                raise Stop('asset metadata changed during acceptance')
            data = snapshot(directory, name, 'after', row['after'])
            atomic(metadata_path(name), data)
        receipt['state'] = 'COMPLETE'
        save(journal, receipt)
    except BaseException as error:
        try:
            rollback(receipt)
        except (Stop, OSError, ValueError, KeyError):
            command = cli_display(ROOT)
            raise Stop(f'acceptance {transaction} interrupted; preserve and inspect copies, then run '
                       f'{command} accept --recover {transaction} --agent AGENT --reviewed') from error
        raise Stop('acceptance failed; previous package and metadata restored, candidate retained for retry: ' + str(error)) from error
    return transaction
