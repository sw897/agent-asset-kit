"""File and native-directory primitives. No Agent credentials are read here."""

from contextlib import contextmanager
from contextvars import ContextVar
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import unicodedata
from . import agent_adapters

ROOT = Path(os.environ.get('AGENT_ASSET_KIT_ROOT', Path.cwd())).expanduser().resolve()
PRIMARY = ROOT
if (ROOT / '.git').is_file():
    common = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--git-common-dir'],
                            capture_output=True, text=True, encoding='utf-8', check=True).stdout.strip()
    PRIMARY = (ROOT / common).resolve().parent
LOCAL = PRIMARY / '.local'
TRANSIENT = {'.git', '.gitattributes', '.lfsconfig', '.venv', 'venv', 'node_modules', '__pycache__', '.DS_Store', '.pytest_cache'}
DENIED = {'.env', '.env.local', 'auth.json', 'credentials.json', 'id_rsa', 'id_ed25519'}
ACTIVE_TRIAL_STATES = {'preparing', 'active', 'passed', 'failed', 'rolling_back', 'interrupted'}


class Stop(Exception):
    pass


def run(args, cwd=ROOT, check=True):
    result = subprocess.run([str(x) for x in args], cwd=cwd, capture_output=True,
                            text=True, encoding='utf-8', timeout=60)
    if check and result.returncode:
        raise Stop(f'{Path(str(args[0])).name} {args[1] if len(args)>1 else ""} '
                   f'failed ({result.returncode}); inspect local state, content retained')
    return result


def cli_argv(root=ROOT):
    return ['aak', '--root', str(root)]


def cli_display(root=ROOT):
    if os.name == 'nt':
        return '& ' + ' '.join("'" + part.replace("'", "''") + "'" for part in cli_argv(root))
    import shlex
    return shlex.join(cli_argv(root))


def git(*args, cwd=ROOT, check=True):
    return run(['git', *args], cwd=cwd, check=check)


def load(path, default=None):
    library_path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


def sha(data):
    return hashlib.sha256(data).hexdigest()


def slug(value):
    return re.sub(r'[^a-z0-9-]+', '-', value.lower()).strip('-')[:80] or 'asset'


def relative(value):
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or not path.parts or '\\' in value or ':' in value:
        raise Stop('unsafe relative path')
    return path


def owned_path(value, root=ROOT):
    """Canonical library content must never traverse a filesystem link."""
    part = relative(value)
    current = root
    for component in part.parts:
        current = current / component
        if link_like(current):
            raise Stop('asset library path traverses a link; content retained for review')
    return root / part


def canonical_path(root, record):
    part = relative(record['path'])
    if (len(part.parts) != 3 or part.parts[:2] not in (('skills', 'custom'), ('skills', 'third-party')) or
            record.get('name') != part.name or record.get('origin', {}).get('kind') != part.parts[1]):
        raise Stop('invalid canonical package identity or path')
    portable_paths([part.as_posix()])
    return owned_path(part.as_posix(), root)


def library_path(path):
    """Protect library/state reads and writes while allowing explicit native paths."""
    for root in dict.fromkeys((ROOT, PRIMARY)):
        if path.is_relative_to(root):
            owned_path(path.relative_to(root).as_posix(), root)
    return path


def portable_paths(names):
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {p + str(i) for p in ('COM', 'LPT') for i in range(1, 10)}
    seen = {}
    for name in names:
        relative(name)
        parts = name.split('/')
        for index, part in enumerate(parts):
            if (part.rstrip(' .') != part or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                    or part.split('.')[0].upper() in reserved or len(part.encode('utf-16-le')) // 2 > 255):
                raise Stop('non-portable package path: ' + name)
            prefix = '/'.join(parts[:index + 1])
            folded = unicodedata.normalize('NFC', prefix).casefold()
            if folded in seen and seen[folded] != prefix:
                raise Stop('case/normalization collision: ' + name)
            seen[folded] = prefix


def atomic(path, data, mode=0o600):
    library_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        temp.chmod(mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save(path, value):
    atomic(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode())


_selected_config = ContextVar('selected_config', default=None)


@contextmanager
def using_config(config):
    token = _selected_config.set(config)
    try:
        yield
    finally:
        _selected_config.reset(token)


def cfg():
    if (selected := _selected_config.get()) is not None:
        return selected
    result = load(LOCAL / 'config.json')
    if not result:
        raise Stop('run configure first')
    return result


@contextmanager
def locked():
    owned_path('.local', PRIMARY)
    LOCAL.mkdir(exist_ok=True)
    with (LOCAL / 'assets.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.write(b'0')
            lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise Stop('another assets operation is running; retry later')
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Stop('another assets operation is running; retry later')
        yield


def scan(folder):
    scanner = cfg().get('gitleaks') or shutil.which('gitleaks')
    if not scanner or not Path(scanner).is_file():
        raise Stop('Gitleaks is required. Install gitleaks on PATH, then retry (check with gitleaks version).')
    rules = LOCAL / 'scanner.toml'
    rules.write_text('[extend]\nuseDefault = true\n', encoding='utf-8')
    report = LOCAL / 'last-scan.json'
    report.unlink(missing_ok=True)
    result = run([scanner, 'dir', folder, '--config', rules, '--ignore-gitleaks-allow',
                  '--gitleaks-ignore-path', LOCAL / 'no-ignore-file', '--redact', '--no-banner',
                  '--no-color', '--log-level', 'error', '--max-archive-depth', '2',
                  '--report-format', 'json', '--report-path', report], check=False)
    raw = load(report, []) or []
    if result.returncode != 0 and (result.returncode != 1 or not raw):
        raise Stop(f'Gitleaks failed ({result.returncode}); import/publication stopped')
    # Persist only identifying metadata, never matched text, credential values or snippets.
    findings = []
    folder = Path(folder).resolve()
    for item in raw:
        path = Path(item['File'])
        try:
            name = path.resolve().relative_to(folder).as_posix()
        except ValueError:
            name = path.as_posix()
        findings.append({'file': name, 'line': item['StartLine'], 'rule': item['RuleID']})
    save(report, findings)
    if findings:
        rejection = LOCAL / 'scan-rejections' / (uuid.uuid4().hex + '.json')
        save(rejection, findings)
        raise Stop('secret scanner rejected content; see ' + str(rejection.relative_to(PRIMARY)))


def files_in(folder, modes=None):
    if not folder.is_dir():
        raise Stop('package directory is missing')
    result = {}
    total = 0
    for current, dirs, names in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in TRANSIENT)
        if any(link_like(Path(current) / d) for d in dirs):
            raise Stop('internal directory link requires manual review')
        for name in sorted(names):
            if name in TRANSIENT or name.endswith(('.pyc', '.log')):
                continue
            path = Path(current) / name
            content_path = path.resolve()
            if link_like(path) and not content_path.is_relative_to(folder.resolve()):
                raise Stop('external file link requires manual review')
            if not content_path.is_file():
                raise Stop('special file requires manual review')
            env_secret = name.startswith('.env.') and name not in {'.env.example', '.env.sample', '.env.template'}
            database = bool(re.search(r'\.(?:sqlite\d*|db)(?:-(?:wal|shm|journal))?$', name, re.I))
            if name in DENIED or env_secret or database or path.suffix.lower() in {'.key', '.pem', '.p12', '.pfx'}:
                raise Stop('credential/runtime file requires manual review')
            size = content_path.stat().st_size
            total += size
            if size > 40 * 1024**2 or total > 180 * 1024**2 or len(result) >= 3000:
                raise Stop('package exceeds bounded collection limits')
            name = path.relative_to(folder).as_posix()
            # NTFS chmod cannot represent POSIX executable intent. Retain the
            # reviewed source/baseline modes instead of inventing platform deltas.
            mode = (modes or {}).get(name, 0o644) if os.name == 'nt' else (
                0o755 if content_path.stat().st_mode & 0o111 else 0o644)
            if mode not in (0o644, 0o755):
                raise Stop('invalid logical file mode')
            result[name] = (content_path.read_bytes(), mode)
    portable_paths(result)
    return result


def link_like(path):
    if path.is_symlink():
        return True
    try:
        return bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
    except FileNotFoundError:
        return False


def file_modes(files):
    return {name: value[1] for name, value in files.items()}


def git_modes(root, subpath):
    result = git('ls-files', '--stage', '-z', '--', subpath, cwd=root, check=False)
    modes = {}
    if result.returncode == 0:
        prefix = subpath.rstrip('/') + '/'
        for row in result.stdout.split('\0'):
            if '\t' in row:
                meta, name = row.split('\t', 1)
                if name.startswith(prefix):
                    modes[name[len(prefix):]] = 0o755 if meta.startswith('100755 ') else 0o644
    return modes


def read_native(config, binding, record=None, state=None, path=None):
    row = (state or load(LOCAL / 'baselines.json', {})).get(key(binding), {})
    modes = row.get('file_modes') or (record or {}).get('file_modes')
    if not modes and record and os.name == 'nt':
        modes = git_modes(PRIMARY, record['path'])
    path = path or native_path(config, binding)[1]
    return files_in(path.resolve(), modes)


def baseline(ident, record, commit=None, files=None):
    return {'skill_id': ident, 'sha256': record['sha256'], 'commit': commit,
            'path': record['path'], 'file_modes': file_modes(files) if files else record.get('file_modes', {})}


def tree_hash(files):
    return sha(json.dumps({k: [sha(v[0]), v[1]] for k, v in sorted(files.items())}, sort_keys=True).encode())


def write_tree(folder, files):
    for name, (data, mode) in files.items():
        atomic(folder / relative(name), data, mode)


def package(root, record):
    path = canonical_path(root, record)
    modes = record.get('file_modes') or git_modes(root, record['path']) or git_modes(ROOT, record['path'])
    files = files_in(path, modes)
    if 'SKILL.md' not in files or tree_hash(files) != record['sha256']:
        raise Stop(f'package differs from manifest: {record["name"]}; record a reviewed revision first')
    return path, files


def native_path(config, binding):
    endpoint = agent_adapters.endpoint(config, binding)
    root = Path(endpoint['skills_root'])
    if link_like(root):
        raise Stop('native root itself must be a directory, not a link')
    part = relative(binding['path'])
    if any(p.startswith('.') for p in part.parts):
        raise Stop('hidden/native-managed binding paths cannot be owned')
    if binding['agent'] == 'hermes':
        if part.name in hermes_bundled(endpoint):
            raise Stop('Hermes bundled skills cannot be owned')
    target = root / part
    parent = target.parent
    while parent != root:
        if link_like(parent):
            raise Stop('native binding traverses a directory link')
        parent = parent.parent
    return root, target


def validate_binding(config, binding):
    if binding['agent'] not in (*agent_adapters.KINDS, 'shared'):
        raise Stop('unknown Agent adapter')
    part = relative(binding['path'])
    if any(p.startswith('.') for p in part.parts):
        raise Stop('hidden/native-managed binding paths cannot be owned')
    if binding['agent'] == 'zcode' and len(part.parts) != 1:
        raise Stop('ZCode user skills require one directory level')
    # A shared policy is independent of local aliases. Validate each active
    # endpoint explicitly; disabled or absent instances do not own the policy.
    for local in agent_adapters.expand(config, binding):
        native_path(config, local)


def key(binding):
    return binding.get('instance', binding['agent']) + ':' + binding['path']


def hermes_bundled(endpoint):
    root = Path(endpoint['skills_root'])
    manifest = root / '.bundled_manifest'
    names = {line.partition(':')[0] for line in manifest.read_text(encoding='utf-8').splitlines()} if manifest.is_file() else set()
    runtime = Path(endpoint.get('runtime') or root.parent / 'hermes-agent') / 'skills'
    if runtime.is_dir():
        names.update(p.parent.name for p in runtime.rglob('SKILL.md'))
    return names


def inventory(config):
    result = {}
    excluded = []
    for endpoint in agent_adapters.configured(config):
        agent, root = endpoint['kind'], Path(endpoint['skills_root'])
        if not root.is_dir():
            continue
        if link_like(root):
            raise Stop('native root itself must be a directory, not a link')
        bundled = hermes_bundled(endpoint) if agent == 'hermes' else set()
        pending = sorted(root.iterdir())
        while pending:
            path = pending.pop(0)
            if path.name.startswith('.') or not path.is_dir():
                continue
            if (path / 'SKILL.md').is_file():
                source = path.resolve()
                binding = {'agent': agent, 'path': path.relative_to(root).as_posix(), 'instance': endpoint['id']}
                if key(binding) in config.get('excluded_bindings', {}):
                    continue
                if path.name in bundled or '.system' in source.parts or 'openai-curated-remote' in source.parts:
                    excluded.append(key(binding))
                    continue
                item = result.setdefault(str(source), {'source': str(source), 'bindings': []})
                item['bindings'].append(binding)
            elif not path.is_symlink():
                pending.extend(sorted(path.iterdir()))
    return list(result.values()), excluded


def origin_for(source, config):
    shared = Path(config['home']) / '.config/skillshare/skills'
    try:
        name = source.relative_to(shared).parts[0]
    except ValueError:
        return {'kind': 'unclassified', 'url': None, 'version': None}
    record = load(shared / '.metadata.json', {}).get('entries', {}).get(name, {})
    url = record.get('repo_url', record.get('source'))
    if url and (not url.startswith('https://github.com/') or '@' in url or '?' in url):
        url = None
    return {'kind': 'third-party' if url else 'unclassified', 'url': url, 'version': record.get('version')}


def registry(root=ROOT):
    result = load(owned_path('skills/manifest.json', root), {'version': 1, 'packages': {}})
    if (not isinstance(result, dict) or type(result.get('version')) is not int or
            result['version'] not in (1, 2) or not isinstance(result.get('packages'), dict)):
        raise Stop('unsupported manifest version; update tooling before continuing')
    owners, names = {}, set()
    for ident, item in result['packages'].items():
        if (not isinstance(item, dict) or not isinstance(item.get('origin'), dict) or
                not isinstance(item.get('path'), str) or not isinstance(item.get('name'), str) or
                not isinstance(item.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']) or
                not isinstance(item.get('bindings'), list)):
            raise Stop('invalid portable package record')
        canonical_path(root, item)
        modes = item.get('file_modes', {})
        if (not isinstance(modes, dict) or
                any(not isinstance(k, str) or type(v) is not int or v not in (0o644, 0o755) for k, v in modes.items())):
            raise Stop('file_modes must map relative filenames to reviewed regular-file modes')
        portable_paths(modes)
        if not isinstance(item.get('requirements', {}), dict) or not isinstance(item.get('dependencies', []), list):
            raise Stop('invalid requirements or dependencies metadata')
        if item['name'] in names:
            raise Stop('duplicate catalog name; choose distinct names without merging asset identities')
        names.add(item['name'])
        for binding in item['bindings']:
            if (not isinstance(binding, dict) or set(binding) != {'agent', 'path'} or
                    binding['agent'] not in (*agent_adapters.KINDS, 'shared') or not isinstance(binding['path'], str)):
                raise Stop('portable bindings contain only agent and path; local instances stay in .local')
            part = relative(binding['path'])
            portable_paths([binding['path']])
            if part.as_posix() != binding['path'] or any(p.startswith('.') for p in part.parts):
                raise Stop('invalid portable binding path')
            binding_key = key(binding)
            if binding_key in owners:
                raise Stop('duplicate native binding in manifest')
            owners[binding_key] = ident
    return result, owners


def pending_quick_trials():
    pending = set()
    for path in (LOCAL / 'quick-trials').glob('*.json'):
        receipt = load(path)
        if not receipt.get('skill_id'):
            pending.add(Path(receipt['target']))
    return pending


def collect():
    config = cfg()
    manifest, owners = registry(PRIMARY)
    state = load(LOCAL / 'baselines.json', {})
    sources, excluded = inventory(config)
    quick_trials = pending_quick_trials()
    candidates, blocked = [], []
    for item in sources:
        source = Path(item['source'])
        if source in quick_trials:
            continue
        try:
            bindings = item['bindings']
            ids = {owners[b['agent'] + ':' + b['path']] for b in bindings
                   if b['agent'] + ':' + b['path'] in owners}
            if len(ids) > 1:
                raise Stop('one native physical source belongs to multiple registered assets')
            ident = next(iter(ids), None)
            files = read_native(config, bindings[0], manifest['packages'].get(ident), state, source)
            fingerprint = tree_hash(files)
            if ident and all(state.get(key(b), {}).get('sha256') == fingerprint for b in bindings):
                continue
            if ident and manifest['packages'][ident]['sha256'] == fingerprint:
                for b in bindings:
                    state[key(b)] = baseline(ident, manifest['packages'][ident],
                        git('rev-parse', 'HEAD', cwd=PRIMARY).stdout.strip(), files)
                continue
            bases = {key(b): state.get(key(b), {}).get('sha256') for b in bindings}
            candidate_id = sha(json.dumps({'source': str(source), 'sha256': fingerprint,
                'skill_id': ident, 'bindings': bindings, 'base': bases}, sort_keys=True).encode())[:20]
            dest = LOCAL / 'candidates' / candidate_id
            library_path(dest)
            if not (dest / 'candidate.json').exists():
                with tempfile.TemporaryDirectory(prefix='candidate-', dir=LOCAL) as temporary:
                    stage = Path(temporary)
                    write_tree(stage / 'package', files)
                    scan(stage / 'package')
                    if tree_hash(read_native(config, bindings[0], manifest['packages'].get(ident), state, source)) != fingerprint:
                        raise Stop('source changed while being captured; retry after saving completes')
                    record = {'id': candidate_id, 'source': str(source), 'sha256': fingerprint,
                              'skill_id': ident, 'bindings': bindings,
                              'base': bases,
                              'base_versions': {key(b): state.get(key(b), {}) for b in bindings},
                              'file_modes': file_modes(files),
                              'origin': manifest['packages'][ident]['origin'] if ident else origin_for(source, config)}
                    save(stage / 'candidate.json', record)
                    dest.parent.mkdir(exist_ok=True)
                    stage.rename(dest)
            candidates.append(load(dest / 'candidate.json'))
        except Stop as error:
            blocked.append({'source': source.name, 'reason': str(error)})
    save(LOCAL / 'baselines.json', state)
    result = {'candidates': candidates, 'builtin_excluded': excluded, 'blocked': blocked,
              'local_only': config.get('excluded_bindings', {}),
              'collected_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    save(LOCAL / 'last-collection.json', result)
    return result


def rename_new(source, dest):
    """Same-filesystem no-clobber switch; never remove an unexpected new target."""
    if sys.platform == 'darwin':
        libc = ctypes.CDLL(None, use_errno=True)
        rc = libc.renamex_np(os.fsencode(source), os.fsencode(dest), 0x4)
    elif sys.platform.startswith('linux'):
        libc = ctypes.CDLL(None, use_errno=True)
        rc = libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(dest), 1)
    elif os.name == 'nt':
        os.rename(source, dest)
        return
    else:
        raise Stop('no-clobber directory switching is not implemented on this OS')
    if rc:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno), str(dest))


def deploy(apply=False, restore_missing=False, preserve_runtime=False, skill_ids=None,
           targets=None, instances=None, overrides=None, bindings_override=None,
           operation='stable', plan_id=None, journal_id=None):
    from .skill_versions import effective
    if apply and not plan_id:
        raise Stop('deployment requires a preview plan; preview first, then apply with --plan PLAN_ID')
    config = cfg()
    if apply and (operation != 'share' or (PRIMARY / '.git').exists()):
        if git('branch', '--show-current', cwd=PRIMARY).stdout.strip() != 'main':
            raise Stop('deployment source must be the primary main checkout')
        if operation != 'share' and git('status', '--porcelain', cwd=PRIMARY).stdout.strip():
            raise Stop('commit/review formal assets before deployment; primary has changes')
    manifest, _ = registry(PRIMARY)
    if skill_ids:
        selected = set(skill_ids)
        if selected - manifest['packages'].keys():
            raise Stop('unknown selected skill ID; no deployment performed')
        manifest = dict(manifest, packages={ident: item for ident, item in manifest['packages'].items()
                                           if ident in selected})
    state = load(LOCAL / 'baselines.json', {})
    approvals = load(LOCAL / 'accepted-copies.json', {})
    for journal_path in (LOCAL / 'deployments').glob('*.json'):
        if load(journal_path)['state'] in ('PREPARING', 'SWITCHING', 'INTERRUPTED'):
            raise Stop(f'unfinished deployment {journal_path.stem}; recheck before a new deployment')
    actions, plans, skipped, seen, locations = [], [], [], {}, {}
    selections = []
    quick_trials = pending_quick_trials()
    active_trials = {}
    active_locations = {}
    if operation in ('stable', 'share'):
        for path in (LOCAL / 'trials').glob('*/trial.json'):
            trial = load(path)
            if trial.get('state') in ACTIVE_TRIAL_STATES:
                active_trials.update({key(b): trial['id'] for b in trial.get('bindings', [])})
                for binding in trial.get('bindings', []):
                    location = native_path(config, binding)[1].resolve()
                    active_locations[unicodedata.normalize('NFC', str(location)).casefold()] = trial['id']
    for ident, record in manifest['packages'].items():
        if overrides and ident in overrides:
            selected, files, commit = overrides[ident]
            record = dict(record, sha256=selected['sha256'], file_modes=file_modes(files))
        else:
            selected, files, commit = effective(ident, record)
            if selected is None:
                skipped.append({'skill_id': ident, 'reason': 'no stable release or retired by an explicit decision'})
                continue
            record = selected
            if operation == 'share' and commit:
                changed = git('diff', '--quiet', commit, '--', record['path'], 'skills/manifest.json',
                              cwd=PRIMARY, check=False)
                if changed.returncode:
                    commit = None
        selections.append({'skill_id': ident, 'sha256': record['sha256'], 'commit': commit})
        logical_bindings = (bindings_override or {}).get(ident, record['bindings'])
        for logical in logical_bindings:
            if targets and logical['agent'] not in targets:
                continue
            expanded = agent_adapters.expand(config, logical)
            if not expanded:
                skipped.append({'skill_id': ident, 'agent': logical['agent'], 'reason': 'Agent not enabled on this computer'})
            for binding in expanded:
                if instances and binding['instance'] not in instances:
                    continue
                if key(binding) in active_trials:
                    skipped.append({'skill_id': ident, 'agent': binding['agent'],
                                    'reason': 'active trial', 'trial_id': active_trials[key(binding)]})
                    continue
                root, target = native_path(config, binding)
                if target.resolve() in quick_trials:
                    raise Stop('this target has a local trial; use aak save before deployment')
                location = unicodedata.normalize('NFC', str(target.resolve())).casefold()
                if operation in ('stable', 'share') and location in active_locations:
                    skipped.append({'skill_id': ident, 'agent': binding['agent'], 'reason': 'physical target has active trial',
                                    'trial_id': active_locations[location]})
                    continue
                for endpoint in agent_adapters.configured(config):
                    other = Path(endpoint['skills_root']).resolve()
                    if root.resolve() != other and (root.resolve().is_relative_to(other) or other.is_relative_to(root.resolve())):
                        raise Stop('nested Agent skill roots require disjoint paths or one exact shared root')
                    if operation in ('trial', 'share') and endpoint['kind'] != 'shared' and root.resolve() == other:
                        wanted = {b['instance'] for logical in logical_bindings for b in agent_adapters.expand(config, logical)
                                  if not instances or b['instance'] in instances}
                        if endpoint['id'] not in wanted:
                            raise Stop('selected target shares a physical root with an unselected Agent; select every alias or use an isolated root')
                if binding['agent'] == 'zcode' and len(relative(binding['path']).parts) != 1:
                    raise Stop('ZCode user skills require one directory level')
                identity = unicodedata.normalize('NFC', str(root.resolve() / relative(binding['path']))).casefold()
                for previous, owner in locations.items():
                    if previous == identity:
                        if owner != (ident, record['sha256']):
                            raise Stop('local Agent roots overlap with incompatible skill versions')
                    elif Path(identity).is_relative_to(Path(previous)) or Path(previous).is_relative_to(Path(identity)):
                        raise Stop('native skill directories overlap; a package cannot contain another managed package')
                locations[identity] = (ident, record['sha256'])
                if identity in seen:
                    prior = seen[identity]
                    if prior['id'] != ident or prior['record']['sha256'] != record['sha256']:
                        raise Stop('local Agent roots overlap with incompatible skill versions')
                    prior['bindings'].append(binding)
                    continue
                if target.is_symlink() and not target.exists():
                    raise Stop(f'broken native link: {key(binding)}')
                exists = target.exists()
                local_hash = tree_hash(read_native(config, binding, record, state)) if exists else None
                base = state.get(key(binding), {}).get('sha256')
                consent = approvals.get(key(binding), {})
                reconciled = consent.get('observed_sha256') == local_hash and consent.get('replacement_sha256') == record['sha256']
                if exists and local_hash not in (base, record['sha256']) and not reconciled:
                    raise Stop(f'native/repository conflict: {key(binding)}; collect and reconcile first')
                if not exists and base and not restore_missing:
                    raise Stop(f'native copy missing: {key(binding)}; removal does not delete the asset')
                if local_hash == record['sha256'] and not target.is_symlink():
                    if apply:
                        state[key(binding)] = baseline(ident, record, commit, files)
                    continue
                runtime = [name for name in ('.venv', 'venv', 'node_modules') if (target / name).is_dir()]
                if apply and runtime and not preserve_runtime:
                    raise Stop(f'native runtime exists: {key(binding)}; review then use --preserve-runtime to retain it')
                plan = {'id': ident, 'record': record, 'bindings': [binding], 'root': root,
                        'target': target, 'files': files, 'observed': local_hash, 'runtime': runtime,
                        'resolved_target': str(target.resolve()),
                        'before_file_modes': file_modes(read_native(config, binding, record, state)) if exists else {},
                        'commit': commit}
                seen[identity] = plan
                plans.append(plan)
                actions.append({'skill_id': ident, 'agent': binding['agent'], 'instance': binding['instance'],
                                'path': binding['path'], 'source_commit': commit, 'sha256': record['sha256'],
                                'before_sha256': local_hash, 'target': str(target),
                                'resolved_target': str(target.resolve()),
                                'action': 'replace-legacy-link' if target.is_symlink() else 'update' if exists else 'create',
                                'local_runtime': runtime})
    signature = sha(json.dumps({'actions': actions, 'versions': selections,
                               'config': config.get('agents', config.get('skill_roots', {})),
                               'operation': operation}, sort_keys=True).encode())
    if plan_id:
        previous = load(LOCAL / 'deployment-plans' / (str(relative(plan_id)) + '.json'))
        if not previous or previous['signature'] != signature:
            raise Stop('deployment plan expired because source, configuration or native files changed')
    if not apply:
        ident = uuid.uuid4().hex
        save(LOCAL / 'deployment-plans' / (ident + '.json'), {'signature': signature, 'actions': actions})
        return {'applied': False, 'plan_id': ident, 'actions': actions, 'skipped': skipped}
    prepared = []
    journal = {'id': journal_id or uuid.uuid4().hex, 'state': 'PREPARING', 'changes': []}
    journal_path = LOCAL / 'deployments' / (journal['id'] + '.json')
    save(journal_path, journal)
    try:
        # All copies are prepared outside scan roots, on each target filesystem.
        for plan in plans:
            ident, record, root, target, files, runtime = (plan[k] for k in ('id', 'record', 'root', 'target', 'files', 'runtime'))
            parent = root.parent / '.agent-asset-kit-staging'
            parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix='package-', dir=parent)).resolve()
            prepared.append(stage)
            write_tree(stage, files)
            for binding in plan['bindings']:
                agent_adapters.validate(binding['agent'], stage)
            scan(stage)
            for name in runtime:
                # Local environment stays local. Preserve its native interpreter links; never upload it.
                shutil.copytree(target / name, stage / name, symlinks=True)
            if tree_hash(files_in(stage, file_modes(files))) != record['sha256']:
                raise Stop('prepared copy readback failed')
        journal['state'] = 'SWITCHING'
        save(journal_path, journal)
        for plan, stage in zip(plans, prepared):
            ident, record, root, target, files, observed, runtime = (
                plan[k] for k in ('id', 'record', 'root', 'target', 'files', 'observed', 'runtime'))
            binding = plan['bindings'][0]
            if str(target.resolve()) != plan['resolved_target']:
                raise Stop('physical native target changed during preparation; no target switch performed')
            current = tree_hash(read_native(config, binding, record, state)) if target.exists() else None
            if current != observed:
                raise Stop(f'native copy changed during preparation: {key(binding)}')
            backup = root.parent / '.agent-asset-kit-backups' / journal['id'] / binding['instance'] / binding['path']
            backup.parent.mkdir(parents=True, exist_ok=True)
            change = {'target': str(target), 'backup': None, 'staging': str(stage), 'state': 'PREPARED',
                      'before_sha256': observed, 'after_sha256': record['sha256'],
                      'before_file_modes': plan['before_file_modes'],
                      'bindings': plan['bindings'], 'skill_id': ident, 'commit': plan['commit'],
                      'before_baselines': {key(b): state.get(key(b)) for b in plan['bindings']},
                      'file_modes': file_modes(files),
                      'local_runtime': runtime, 'runtime_relocation': 'NOT_VERIFIED' if runtime else 'NOT_APPLICABLE'}
            journal['changes'].append(change)
            save(journal_path, journal)
            if target.exists() or target.is_symlink():
                rename_new(target, backup)
                change['backup'] = str(backup)
                change['state'] = 'BACKED_UP'
                save(journal_path, journal)
                if tree_hash(read_native(config, binding, record, state, backup.resolve())) != observed:
                    rename_new(backup, target)
                    raise Stop('native copy changed while being backed up; original restored')
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                rename_new(stage, target)
            except BaseException:
                if change['backup'] and not target.exists() and not target.is_symlink():
                    rename_new(backup, target)
                raise
            if target.is_symlink() or tree_hash(files_in(target, file_modes(files))) != record['sha256']:
                raise Stop('deployed copy failed readback; original retained in backup')
            change['state'] = 'VERIFIED'
            for binding in plan['bindings']:
                state[key(binding)] = baseline(ident, record, plan['commit'], files)
                approvals.pop(key(binding), None)
            save(LOCAL / 'baselines.json', state)
            save(journal_path, journal)
        journal['state'] = 'COMPLETE'
        save(journal_path, journal)
        save(LOCAL / 'baselines.json', state)
        save(LOCAL / 'accepted-copies.json', approvals)
    except BaseException:
        journal['state'] = 'INTERRUPTED' if journal['changes'] else 'ABORTED'
        save(journal_path, journal)
        raise
    finally:
        for stage in prepared:
            if stage.exists():
                shutil.rmtree(stage)
    return {'applied': True, 'actions': actions, 'skipped': skipped, 'journal': str(journal_path)}
