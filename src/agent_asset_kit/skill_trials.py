"""Explicit, reversible Agent trials of already reviewed immutable revisions."""

import json
from pathlib import Path
import time
import uuid

from . import agent_adapters
from .assetlib import (ROOT, PRIMARY, LOCAL, Stop, baseline, cfg, deploy, file_modes,
                      files_in, key, load, native_path, read_native, registry,
                      relative, rename_new, save, scan, sha, tree_hash, ACTIVE_TRIAL_STATES, library_path)
from .skill_versions import snapshot


def trial_path(ident):
    return LOCAL / 'trials' / relative(ident) / 'trial.json'


def start(args):
    if not args.reviewed:
        raise Stop('review trial version and targets, then pass --reviewed')
    if args.apply and not args.plan:
        raise Stop('trial requires a preview plan; preview first, then apply with --plan PLAN_ID')
    manifest, owners = registry(PRIMARY)
    current = manifest['packages'][args.skill_id]
    record, files, commit = snapshot(args.ref, args.skill_id, PRIMARY)
    if record['sha256'] != current['sha256']:
        raise Stop('trial must target the current reviewed catalog revision')
    config = cfg()
    logical = []
    for kind in dict.fromkeys(args.target):
        existing = [b for b in current['bindings'] if b['agent'] == kind]
        logical.extend([{'agent': kind, 'path': args.path}] if args.path else
                       existing or [{'agent': kind, 'path': current['name']}])
    bindings = []
    for binding in logical:
        owner = owners.get(key(binding))
        if owner and owner != args.skill_id:
            raise Stop('trial target belongs to another registered skill')
        selected = agent_adapters.expand(config, binding)
        if args.instance:
            selected = [b for b in selected if b['instance'] in args.instance]
        if not selected:
            raise Stop('trial target is not enabled on this computer: ' + binding['agent'])
        bindings.extend(selected)
    for path in (LOCAL / 'trials').glob('*/trial.json'):
        prior = load(path)
        if prior.get('state') in ACTIVE_TRIAL_STATES:
            if {key(b) for b in prior.get('bindings', [])} & {key(b) for b in bindings}:
                raise Stop('another trial already owns this target; finish or roll it back first')
    override = {args.skill_id: (record, files, commit)}
    selected = {args.skill_id: logical}
    preview = deploy(False, skill_ids=[args.skill_id], instances=args.instance,
                     overrides=override, bindings_override=selected, operation='trial',
                     plan_id=args.plan if args.apply else None)
    if not args.apply:
        return dict(preview, trial_version=commit, next='repeat trial start --apply with this --plan')
    if any(action['local_runtime'] for action in preview['actions']) and not args.preserve_runtime:
        raise Stop('trial targets have local runtimes; review --preserve-runtime before applying')
    # Scan only selected immutable bytes before writing native copies.
    stage = LOCAL / 'trial-scans' / uuid.uuid4().hex
    library_path(stage)
    from .assetlib import write_tree
    import shutil
    try:
        write_tree(stage, files);scan(stage)
        for kind in {binding['agent'] for binding in bindings}:
            agent_adapters.validate(kind, stage)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    ident = uuid.uuid4().hex
    journal_id = uuid.uuid4().hex
    receipt = {'id': ident, 'skill_id': args.skill_id, 'commit': commit, 'sha256': record['sha256'],
               'bindings': bindings, 'state': 'preparing', 'actor': args.agent,
               'phase': 'deploy', 'deployment_journal': str(LOCAL / 'deployments' / (journal_id + '.json')),
               'trial_file_modes': file_modes(files),
               'before_file_modes': {key(b): file_modes(read_native(config, b, current))
                                     if native_path(config, b)[1].is_dir() else {} for b in bindings},
               'before_copies': {key(b): tree_hash(read_native(config, b, current))
                                 if native_path(config, b)[1].is_dir() else None for b in bindings},
               'before_baselines': {key(b): load(LOCAL / 'baselines.json', {}).get(key(b)) for b in bindings},
               'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    save(trial_path(ident), receipt)
    try:
        result = deploy(True, preserve_runtime=args.preserve_runtime, skill_ids=[args.skill_id],
                        instances=args.instance, overrides=override, bindings_override=selected,
                        operation='trial', plan_id=args.plan, journal_id=journal_id)
        receipt.update(state='active', deployment_journal=result['journal'])
        save(trial_path(ident), receipt)
    except BaseException:
        receipt['state'] = 'interrupted';save(trial_path(ident), receipt)
        raise
    return {'trial_id': ident, 'state': 'active', 'commit': commit, 'sha256': record['sha256'],
            'actions': result['actions'], 'next': 'verify actual task, then trial finish with evidence'}


def unchanged(receipt):
    config = cfg()
    for binding in receipt['bindings']:
        _, target = native_path(config, binding)
        if not target.is_dir() or tree_hash(read_native(config, binding)) != receipt['sha256']:
            raise Stop('trial copy changed or disappeared; collect and preserve edits before finishing/rollback')


def finish(args):
    receipt = load(trial_path(args.trial_id))
    if not receipt or receipt['state'] not in ('active', 'passed', 'failed'):
        raise Stop('trial is not active')
    if not args.reviewed or not args.reason:
        raise Stop('review trial evidence and provide --reviewed and --reason')
    unchanged(receipt)
    evidence = Path(args.evidence).expanduser().resolve()
    value = json.loads(evidence.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('skill_id') != receipt['skill_id'] or value.get('sha256') != receipt['sha256']:
        raise Stop('evidence must identify the exact trial skill and content hash')
    if (not isinstance(value, dict) or not isinstance(value.get('checks'), list) or not value['checks']
            or any(not isinstance(check, (str, dict)) or not check for check in value['checks'])
            or value.get('status') not in ('PASS', 'FAIL')):
        raise Stop('evidence needs actual checks and a PASS/FAIL status')
    if args.result == 'passed' and value['status'] != 'PASS':
        raise Stop('a failed verification cannot produce a passing trial')
    receipt.update(state=args.result, result=args.result, reason=args.reason,
                   evidence={'path': str(evidence), 'sha256': sha(evidence.read_bytes()),
                             'status': value['status'], 'checks_count': len(value['checks'])})
    save(trial_path(args.trial_id), receipt)
    return {'trial_id': receipt['id'], 'state': receipt['state'], 'evidence': receipt['evidence']}


def rollback(args):
    receipt = load(trial_path(args.trial_id))
    if not receipt or receipt['state'] not in ('active', 'passed', 'failed'):
        raise Stop('trial is not eligible for automatic rollback; inspect interrupted journals explicitly')
    if not args.reviewed:
        raise Stop('review rollback targets and pass --reviewed')
    unchanged(receipt)
    journal = load(Path(receipt['deployment_journal']))
    if not journal or journal['state'] != 'COMPLETE':
        raise Stop('deployment journal is incomplete; preserve and recheck it first')
    config = cfg()
    # Preflight every target and backup before changing any of them.
    for change in journal['changes']:
        binding = change['bindings'][0]
        _, expected = native_path(config, binding)
        if str(expected) != change['target']:
            raise Stop('local Agent paths changed after the trial; rollback requires review')
        if change['backup']:
            previous = change.get('before_baselines', {}).get(key(binding)) or {}
            files = files_in(Path(change['backup']).resolve(), change.get('before_file_modes') or previous.get('file_modes'))
            if tree_hash(files) != change['before_sha256']:
                raise Stop('trial backup changed; original and current copy retained')
    receipt.update(state='rolling_back', phase='rollback');save(trial_path(args.trial_id), receipt)
    try:
        for change in reversed(journal['changes']):
            binding = change['bindings'][0];root, target = native_path(config, binding)
            # A last read protects edits after preflight. Move, never discard, the trial tree.
            if tree_hash(read_native(config, binding)) != receipt['sha256']:
                raise Stop('trial changed during rollback; no overwrite performed')
            retained = root.parent / '.agent-asset-kit-backups' / (receipt['id'] + '-rollback') / binding['instance'] / binding['path']
            retained.parent.mkdir(parents=True, exist_ok=True)
            rename_new(target, retained)
            change['retained_trial'] = str(retained)
            save(Path(receipt['deployment_journal']), journal)
            if change['backup']:
                rename_new(Path(change['backup']), target)
        state = load(LOCAL / 'baselines.json', {})
        for binding in receipt['bindings']:
            previous = receipt['before_baselines'].get(key(binding))
            if previous is None:
                state.pop(key(binding), None)
            else:
                state[key(binding)] = previous
        save(LOCAL / 'baselines.json', state)
        receipt['state'] = 'rolled_back';save(trial_path(args.trial_id), receipt)
    except BaseException:
        receipt['state'] = 'interrupted';save(trial_path(args.trial_id), receipt)
        raise
    return {'trial_id': receipt['id'], 'state': receipt['state'], 'trial_copy_retained': True}


def recover(args):
    """Recognize an explicitly restored or fully installed state; never replay writes."""
    receipt = load(trial_path(args.trial_id))
    if not args.reviewed or not receipt or receipt['state'] not in ('preparing', 'rolling_back', 'interrupted'):
        raise Stop('review an interrupted trial and pass --reviewed')
    config, checks = cfg(), []
    journal_path = Path(receipt['deployment_journal'])
    journal = load(journal_path, {})
    for binding in receipt['bindings']:
        _, target = native_path(config, binding)
        before = receipt.get('before_copies', {}).get(key(binding))
        previous = receipt['before_baselines'].get(key(binding)) or {}
        current_files = files_in(target, receipt.get('before_file_modes', {}).get(key(binding))
                                or previous.get('file_modes')) if target.is_dir() else None
        restored_hash = tree_hash(current_files) if current_files is not None else None
        trial_files = files_in(target, receipt.get('trial_file_modes')) if target.is_dir() else None
        trial_hash = tree_hash(trial_files) if trial_files is not None else None
        checks.append({'binding': key(binding), 'original': restored_hash == before,
                       'trial': trial_hash == receipt['sha256']})
    all_original = all(c['original'] for c in checks)
    all_trial = all(c['trial'] for c in checks)
    if all_original and (receipt.get('phase') == 'rollback' or not all_trial):
        state = load(LOCAL / 'baselines.json', {})
        for binding in receipt['bindings']:
            previous = receipt['before_baselines'].get(key(binding))
            if previous is None:
                state.pop(key(binding), None)
            else:
                state[key(binding)] = previous
        save(LOCAL / 'baselines.json', state)
        receipt['state'] = 'rolled_back'
        if journal:
            journal['state'] = 'RECHECKED'
    elif all_trial and journal.get('state') == 'COMPLETE' and receipt.get('phase') != 'rollback':
        receipt['state'] = 'active'
    else:
        return {'status': 'BLOCKED', 'checks': checks,
                'reason': 'preserved copies need manual reconciliation before metadata recovery', 'native_files_changed': False}
    if journal:
        save(journal_path, journal)
    save(trial_path(args.trial_id), receipt)
    return {'trial_id': receipt['id'], 'state': receipt['state'], 'checks': checks, 'native_files_changed': False}


def status(args):
    if args.trial_id:
        receipt = load(trial_path(args.trial_id))
        if not receipt:
            raise Stop('unknown trial')
        return receipt
    return {'trials': [load(p) for p in sorted((LOCAL / 'trials').glob('*/trial.json'))]}
