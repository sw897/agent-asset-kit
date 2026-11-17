"""Resolve a user-owned library before loading the asset operations."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from . import __version__


def default_library():
    return Path.home() / 'agent-home'


def initialize(argv, root=None):
    parser = argparse.ArgumentParser(prog='aak init', description='Create a new Home Repo, independently of the aak installation.')
    parser.add_argument('directory', nargs='?', help='new empty directory; default: ~/agent-home')
    git_options = parser.add_mutually_exclusive_group()
    git_options.add_argument('--git', dest='git', action='store_true', help='initialize Git on main (default; no commit or remote)')
    git_options.add_argument('--no-git', dest='git', action='store_false', help='create only the blank files')
    parser.set_defaults(git=True)
    parser.add_argument('--agents', help='configure only these comma-separated local Agent kinds, e.g. codex,claude')
    parser.add_argument('--gitleaks', help='optional scanner path; otherwise find gitleaks on PATH')
    args = parser.parse_args(argv)
    requested = Path(args.directory or root or default_library()).expanduser()
    if args.directory and root and requested.resolve() != Path(root).expanduser().resolve():
        raise ValueError('choose one Home Repo destination: DIRECTORY or --root, not different paths')
    if requested.is_symlink():
        raise ValueError('library destination must not be a symlink')
    target = requested.resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError('library destination must be absent or empty; existing content is preserved')
    if args.gitleaks and args.agents is None:
        raise ValueError('use --agents with --gitleaks, or configure the Home Repo afterwards')
    from . import agent_adapters
    configuration = agent_adapters.local_config(Path.home(), args.agents, args.gitleaks) if args.agents is not None else None
    seed = Path(__file__).parent / 'resources/library'
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.aak-init-', dir=target.parent) as temporary:
        stage = Path(temporary) / 'library'
        shutil.copytree(seed, stage)
        for path in stage.rglob('*.md'):
            path.write_text(path.read_text(encoding='utf-8').replace('{{LIBRARY_NAME}}', target.name), encoding='utf-8')
        marker = {'format_version': 1, 'name': target.name, 'tool': 'AgentAssetKit'}
        (stage / '.agent-asset-kit.json').write_text(json.dumps(marker, indent=2) + '\n', encoding='utf-8')
        if configuration is not None:
            local = stage / '.local'
            local.mkdir(mode=0o700)
            config_path = local / 'config.json'
            config_path.write_text(json.dumps(configuration, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
            config_path.chmod(0o600)
        if target.exists():
            target.rmdir()  # Fails if another process added content; never replace it.
        stage.rename(target)
    if args.git:
        result = subprocess.run(['git', 'init', '-b', 'main', str(target)], capture_output=True, text=True, encoding='utf-8', timeout=60)
        if result.returncode:
            raise ValueError('library files retained, but Git initialization failed: ' + result.stderr.strip())
    return {'initialized': str(target), 'git_initialized': args.git, 'packages': 0,
            'configured': configuration is not None,
            'enabled_agents': [row['id'] for row in agent_adapters.configured(configuration)] if configuration else [],
            'gitleaks': configuration['gitleaks'] if configuration else None,
            'native_resources_changed': False, 'remote_configured': False,
            'next': 'aak attach' if configuration else 'aak configure --agents AGENT', 'next_cwd': str(target)}


def validate_library(path):
    marker = path / '.agent-asset-kit.json'
    if marker.is_symlink() or not marker.is_file():
        raise ValueError('selected directory is not an initialized asset library; use aak init DIRECTORY')
    value = json.loads(marker.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or value.get('tool') != 'AgentAssetKit' or
            type(value.get('format_version')) is not int or value['format_version'] != 1):
        raise ValueError('unsupported asset library marker; inspect its format before continuing')
    if (path / '.git').is_file():
        result = subprocess.run(['git', '-C', str(path), 'rev-parse', '--git-common-dir'],
                                capture_output=True, text=True, encoding='utf-8', timeout=60)
        if result.returncode:
            raise ValueError('unable to identify the primary asset library')
        primary = (path / result.stdout.strip()).resolve().parent
        if primary != path:
            validate_library(primary)
    return path


def library_root(explicit):
    selected = explicit or os.environ.get('AGENT_ASSET_KIT_ROOT')
    if selected:
        path = Path(selected).expanduser().resolve()
        if not path.is_dir():
            raise ValueError('selected asset library does not exist')
        return validate_library(path)
    current = Path.cwd().resolve()
    for path in (current, *current.parents):
        if (path / '.agent-asset-kit.json').exists():
            return validate_library(path)
        if (path / '.git').is_file():
            git = subprocess.run(['git', '-C', str(path), 'rev-parse', '--git-common-dir'],
                                 capture_output=True, text=True, encoding='utf-8', timeout=60)
            if not git.returncode:
                primary = (path / git.stdout.strip()).resolve().parent
                if (primary / '.agent-asset-kit.json').is_file():
                    raise ValueError('this worktree predates the library marker; preserve edits and merge the current main before using aak')
    default = default_library()
    if default.is_symlink():
        raise ValueError('default Home Repo must not be a symlink')
    if default.exists():
        return validate_library(default.resolve())
    raise ValueError(f'no Home Repo found; run aak init (default: {default}) or select an initialized library with --root DIRECTORY')


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    arguments = list(sys.argv[1:] if argv is None else argv)
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument('--root')
    selected, remaining = global_parser.parse_known_args(arguments)
    try:
        if remaining == ['--version']:
            print(__version__)
            return 0
        if remaining and remaining[0] == 'guide':
            parser = argparse.ArgumentParser(prog='aak guide', description='Read the bundled agent guide without an asset library.')
            parser.parse_args(remaining[1:])
            guide = Path(__file__).parent / 'resources/agent-guide.md'
            sys.stdout.write(guide.read_text(encoding='utf-8'))
            return 0
        if remaining and remaining[0] == 'init':
            print(json.dumps(initialize(remaining[1:], selected.root or os.environ.get('AGENT_ASSET_KIT_ROOT')), ensure_ascii=False, indent=2))
            return 0
        options = remaining[:remaining.index('--')] if '--' in remaining else remaining
        if not remaining or '--help' in options or '-h' in options:
            root = Path.cwd()
        else:
            root = library_root(selected.root)
        os.environ['AGENT_ASSET_KIT_ROOT'] = str(root)
        from .assets import main as operate
        return operate(remaining or ['--help'])
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'status': 'STOPPED', 'reason': str(error)}, ensure_ascii=False))
        return 2
