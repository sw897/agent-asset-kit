"""Install one independent trial copy, adopt it locally, and share on demand."""

import argparse
from pathlib import Path
import os
import shlex
import tempfile
import uuid

from . import agent_adapters, skill_sources
from .skill_audit import entry_fields
from .assetlib import (ACTIVE_TRIAL_STATES, LOCAL, PRIMARY, ROOT, Stop, baseline, cfg, deploy,
                       file_modes, files_in, key, library_path, link_like, load, native_path,
                       package, portable_paths, registry, rename_new, save, scan, sha, slug,
                       tree_hash, using_config, write_tree)


def commands(sub):
    p = sub.add_parser('try', help='install a skill into one Agent to try it')
    p.add_argument('source', help='local skill directory, owner/repo, GitHub URL or skills.sh URL')
    p.add_argument('--skill', help='choose a skill by name or exact directory path in its repository')
    p.add_argument('--ref', help='select a Git revision')
    p.add_argument('--to', dest='target', choices=agent_adapters.KINDS, required=True)
    p.add_argument('--instance', help='select a locally configured Agent instance')
    for command, option, help_text in [('save', '--from', 'save the trial copy and its edits in your library'),
                                       ('share', '--to', 'copy a saved skill to one more Agent')]:
        p = sub.add_parser(command, help=help_text)
        p.add_argument('name')
        p.add_argument(option, dest='target', choices=agent_adapters.KINDS, required=command == 'share',
                       help='source Agent (inferred for a single copy)' if command == 'save' else 'destination Agent')
        p.add_argument('--instance')
        if command == 'save':
            p.add_argument('--dry-run', action='store_true', help='show changes since installation')


def destination(args, name):
    config = cfg()
    portable_paths([name])
    if args.command_name != 'share' and Path(name).name != name:
        raise Stop('select a skill by its installed directory name')
    logical = {'agent': args.target, 'path': name}
    if args.instance:
        logical['instance'] = args.instance
    matches = agent_adapters.expand(config, logical)
    if not matches:
        disabled = agent_adapters.endpoints(config, logical, enabled_only=False)
        if len(disabled) == 1 and not disabled[0]['enabled']:
            raise Stop(f"Agent {disabled[0]['id']} is disabled. To enable it in this library, run "
                       f"aak agent enable --name {disabled[0]['id']} --reviewed")
        raise Stop('no enabled instance matches this target; check aak agent list for enabled names and paths')
    if len(matches) > 1:
        raise Stop('choose --instance from: ' + ', '.join(b['instance'] for b in matches))
    binding = matches[0]
    root, target = native_path(config, binding)
    real = root.resolve()
    for library in (ROOT, PRIMARY):
        if real.is_relative_to(library) or library.is_relative_to(real):
            raise Stop('native Agent root overlaps the asset library')
    for row in agent_adapters.configured(config):
        other = Path(row['skills_root']).resolve()
        if row['id'] != binding['instance'] and (real.is_relative_to(other) or other.is_relative_to(real)):
            raise Stop('Agent roots overlap; select a separate directory to install into one Agent')
    if key(binding) in config.get('excluded_bindings', {}):
        raise Stop('this native skill is explicitly excluded')
    if link_like(target):
        raise Stop('target is a link; existing installation retained')
    for receipt in (LOCAL / 'trials').glob('*/trial.json'):
        trial = load(receipt)
        if trial.get('state') in ACTIVE_TRIAL_STATES and any(
                native_path(config, b)[1].resolve() == target.resolve() for b in trial['bindings']):
            raise Stop('target belongs to an active version trial')
    return binding, target


def receipt_path(binding):
    return LOCAL / 'quick-trials' / (sha(key(binding).encode()) + '.json')


def recorded_copies(name, instance=None):
    copies = []
    for row in agent_adapters.configured(cfg()):
        binding = {'agent': row['kind'], 'instance': row['id'], 'path': name}
        if (not instance or instance == row['id']) and load(receipt_path(binding)):
            copies.append(binding)
    return copies


def fingerprints(files):
    return {name: [sha(data), mode] for name, (data, mode) in files.items()}


def install(args):
    local = Path(args.source).expanduser()
    explicit_path = local.is_absolute() or args.source.startswith(('.', '~')) or '\\' in args.source
    if explicit_path and not local.is_dir():
        raise Stop(f'local skill directory does not exist: {local}')
    if explicit_path or local.is_dir():
        if args.ref or args.skill:
            raise Stop('for a local source, select the directory containing SKILL.md directly')
        source = local.resolve()
        files = files_in(source)
        origin = {'kind': 'custom', 'url': None, 'version': None}
        license_name = None
    else:
        subpath = args.skill.removeprefix('./') if args.skill and '/' in args.skill else None
        if args.skill == '.':
            subpath = ''
        fetched = skill_sources.inspect(argparse.Namespace(source=args.source, command_name='fetch',
            local_source=False, ref=args.ref, path=subpath, skill=args.skill, reconsider=False, reviewed=False))
        if 'candidate' not in fetched:
            available = fetched['available_paths']
            if not available:
                raise Stop('no SKILL.md found in this repository revision')
            raise Stop('choose one directory with --skill: ' + ', '.join('./' + p if p else '.' for p in available))
        source = Path(fetched['package'])
        files = files_in(source, fetched['file_modes'])
        if tree_hash(files) != fetched['sha256']:
            raise Stop('downloaded candidate changed; inspect the local candidate')
        origin, license_name = fetched['origin'], fetched['license_file']
    if 'SKILL.md' not in files:
        raise Stop('select a skill directory containing SKILL.md')
    name = slug(entry_fields(source / 'SKILL.md')['name'])
    binding, target = destination(args, name)
    saved_path = receipt_path(binding)
    digest = tree_hash(files)
    receipt = load(saved_path)
    save_args = ['aak']
    cwd = Path.cwd().resolve()
    inferred = next((p for p in (cwd, *cwd.parents) if (p / '.agent-asset-kit.json').is_file()),
                    (Path.home() / '.agent-asset-kit').resolve())
    if ROOT != inferred:
        save_args.extend(['--root', str(ROOT)])
    save_args.extend(['save', name])
    if any(b != binding for b in recorded_copies(name)):
        save_args.extend(['--from', args.target])
    if args.instance:
        save_args.extend(['--instance', args.instance])
    next_step = (shlex.join(save_args) if os.name != 'nt' else
                 '& ' + ' '.join("'" + part.replace("'", "''") + "'" for part in save_args))
    if target.exists():
        if receipt and (receipt['target'] != str(target.resolve()) or receipt['initial_hash'] != digest or
                        receipt['origin'] != origin):
            raise Stop('another trial is recorded for this destination; existing files retained')
        if receipt and (target / 'SKILL.md').is_file() and (receipt['state'] == 'installed' or
                        tree_hash(files_in(target, file_modes(files))) == digest):
            if receipt['state'] == 'preparing':
                receipt['state'] = 'installed'
                save(saved_path, receipt)
            return {'installed': name, 'target': str(target), 'already_installed': True, 'next': next_step}
        raise Stop(f'a skill already exists at {target}; existing files retained. Choose another Agent with --to.')
    if key({'agent': args.target, 'path': name}) in registry()[1]:
        raise Stop('the library already owns this destination; use share or trial start')
    physical = target.resolve()
    # Stage next to the native skills root so the final no-clobber rename is atomic.
    staging_parent = physical.parent.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=staging_parent, prefix='.aak-try-') as tmp:
        stage = Path(tmp) / 'package'
        write_tree(stage, files)
        fields = agent_adapters.validate(args.target, stage)
        if slug(fields['name']) != name:
            raise Stop('source skill name changed during capture; retry')
        scan(stage)
        if tree_hash(files_in(stage, file_modes(files))) != digest:
            raise Stop('prepared trial copy changed')
        if destination(args, name)[1].resolve() != physical:
            raise Stop('Agent path changed while preparing the installation')
        physical.parent.mkdir(parents=True, exist_ok=True)
        receipt = {'state': 'preparing', 'target': str(physical), 'origin': origin,
                   'initial_hash': digest, 'initial_files': fingerprints(files), 'license_file': license_name}
        save(saved_path, receipt)
        rename_new(stage, physical)
        receipt['state'] = 'installed'
        save(saved_path, receipt)
    return {'installed': name, 'target': str(target), 'library': str(ROOT), 'next': next_step}


def adopt(args):
    from .assets import accept
    if not args.instance or not args.target:
        portable_paths([args.name])
        matches = [b for b in recorded_copies(args.name, args.instance)
                   if not args.target or b['agent'] == args.target]
        if not matches:
            raise Stop('no recorded copy with this name; use the skill name printed by aak try or aak share')
        if len(matches) > 1:
            choices = [b['agent'] + ' (--instance ' + b['instance'] + ')' for b in matches]
            raise Stop('multiple copies; choose --from: ' + ', '.join(choices))
        args.target, args.instance = matches[0]['agent'], matches[0]['instance']
    binding, target = destination(args, args.name)
    path = receipt_path(binding)
    receipt = load(path)
    if not receipt or str(target.resolve()) != receipt['target']:
        raise Stop('no matching local trial at this Agent path')
    original = receipt['initial_files']
    files = files_in(target, {name: item[1] for name, item in original.items()})
    digest = tree_hash(files)
    if receipt['state'] == 'preparing' and digest != receipt['initial_hash']:
        raise Stop('interrupted installation; inspect the native copy before saving')
    current = fingerprints(files)
    changed = sorted(name for name in original.keys() | current.keys() if original.get(name) != current.get(name))
    if args.dry_run:
        return {'skill': args.name, 'changed_since_install': changed}
    manifest, owners = registry()
    logical = {'agent': args.target, 'path': args.name}
    owner = owners.get(key(logical))
    previous = manifest['packages'].get(owner)
    if owner and owner != receipt.get('skill_id'):
        # A crash after acceptance can leave only the final receipt write pending.
        if previous['sha256'] != digest or previous['origin'] != receipt['origin']:
            raise Stop('this binding now belongs to another asset; collect and reconcile')
    if receipt.get('skill_id') and receipt['skill_id'] != owner:
        raise Stop('trial binding changed in the library; collect and reconcile')
    if previous:
        decisions, _ = skill_sources.prior_decisions(receipt['origin'], args.name)
        decisions = [d for d in load(ROOT / 'skills/decisions.json', {}).get('entries', [])
                     if d.get('skill_id') == owner or d in decisions]
        if decisions and decisions[-1].get('decision') in ('retired', 'reject'):
            raise Stop('this skill was retired or rejected; review its catalog decision first')
        package(ROOT, previous)
        if previous['sha256'] not in (digest, receipt.get('saved_hash')):
            raise Stop('library changed since the last save; collect and reconcile the two versions')
    license_name = receipt['license_file']
    external = receipt['origin']['kind'] == 'third-party'
    if external and (not license_name or current.get(license_name) != original.get(license_name)):
        raise Stop('saving a third-party skill requires its original upstream license')
    if not previous or previous['sha256'] != digest:
        cid = uuid.uuid4().hex
        folder = library_path(LOCAL / 'candidates' / cid)
        item = {'id': cid, 'source': str(target), 'name': args.name, 'sha256': digest,
                'file_modes': file_modes(files), 'bindings': [binding], 'origin': receipt['origin'],
                'base': {key(binding): receipt.get('saved_hash')}, 'skill_id': owner, 'license_file': license_name}
        if external and not previous:
            item['candidate_type'] = 'external'
            if any(skill_sources.source_key(skill_sources.known_source(r)) == skill_sources.source_key(item['origin'])
                   for r in manifest['packages'].values()):
                raise Stop('upstream skill already exists; reconcile its changes with the registered package')
        with tempfile.TemporaryDirectory(dir=LOCAL) as tmp:
            stage = Path(tmp) / 'candidate'
            write_tree(stage / 'package', files)
            agent_adapters.validate(args.target, stage / 'package')
            scan(stage / 'package')
            save(stage / 'candidate.json', item)
            folder.parent.mkdir(exist_ok=True)
            rename_new(stage, folder)
        if item.get('candidate_type') == 'external':
            skill_sources.evaluate(argparse.Namespace(candidate=cid, decision='adopt', reason='Explicit aak save',
                use_case='Keep this native skill', agent='user', reviewed=True, reconsider=False))
        if tree_hash(files_in(target, file_modes(files))) != digest:
            raise Stop('native copy changed during capture; retry save')
        result = accept(argparse.Namespace(candidate=cid, reviewed=True, name=args.name, kind=None, agent='user'))
        owner = result['skill_id']
        previous = registry()[0]['packages'][owner]
        save(LOCAL / 'saved-versions' / owner / (digest + '.json'), {'candidate': cid})
    state = load(LOCAL / 'baselines.json', {})
    state[key(binding)] = baseline(owner, previous, files=files)
    save(LOCAL / 'baselines.json', state)
    save(path, dict(receipt, state='installed', skill_id=owner, saved_hash=digest))
    return {'saved': args.name, 'skill_id': owner, 'changed_since_install': changed, 'library': str(ROOT)}


def share(args):
    from .assets import enable
    manifest, owners = registry()
    ident = next((i for i, r in manifest['packages'].items() if r['name'] == args.name), None)
    if not ident:
        raise Stop('skill is absent from the library; save it first')
    bindings = [b for b in manifest['packages'][ident]['bindings'] if b['agent'] == args.target]
    if len(bindings) > 1:
        raise Stop('multiple paths are bound to this Agent; use deploy with a reviewed plan')
    logical = bindings[0] if bindings else {'agent': args.target, 'path': args.name}
    binding, target = destination(args, logical['path'])
    if owners.get(key(logical)) not in (None, ident):
        raise Stop('target belongs to another registered skill')
    receipt = load(receipt_path(binding))
    if receipt and receipt.get('skill_id') != ident:
        raise Stop('this target has a local trial; save or reconcile it first')
    if not bindings and target.exists():
        baseline_id = load(LOCAL / 'baselines.json', {}).get(key(binding), {}).get('skill_id')
        if baseline_id != ident:
            raise Stop(f'a skill already exists at {target}; existing files retained. Choose another Agent with --to.')
    options = dict(skill_ids=[ident], instances=[binding['instance']], bindings_override={ident: [logical]},
                   operation='share')
    plan = deploy(**options)
    if plan['skipped']:
        raise Stop(plan['skipped'][0]['reason'])
    deploy(apply=True, plan_id=plan['plan_id'], **options)
    deployed = load(LOCAL / 'baselines.json')[key(binding)]
    files = files_in(target, deployed['file_modes'])
    if tree_hash(files) != deployed['sha256']:
        raise Stop('native skill changed after deployment; inspect before sharing again')
    item = manifest['packages'][ident]
    save(receipt_path(binding), {'state': 'installed', 'target': str(target.resolve()), 'origin': item['origin'],
                                'initial_hash': deployed['sha256'], 'initial_files': fingerprints(files),
                                'skill_id': ident, 'saved_hash': deployed['sha256'], 'license_file': item.get('license_file')})
    enable(argparse.Namespace(reviewed=True, skill_id=ident, target=args.target, path=logical['path']))
    return {'shared': args.name, 'target': str(target)}


def perform(args):
    if ROOT != PRIMARY:
        raise Stop('quick commands use the primary library; select its path or use the workspace review commands')
    if args.command_name == 'save':
        return adopt(args)
    action = install if args.command_name == 'try' else share
    config = cfg()
    selected = config.get('agents', {}).get(args.instance or args.target, {})
    # Never enroll an explicitly disabled target or bypass a configured profile.
    if (not selected.get('on_request') or selected['kind'] != args.target or
            (not args.instance and agent_adapters.endpoints(config, {'agent': args.target}))):
        return action(args)
    selected.pop('on_request')
    selected['enabled'] = True
    with using_config(config):
        result = action(args)
    save(LOCAL / 'config.json', config)
    return result
