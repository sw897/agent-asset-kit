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


def initialize(argv):
    parser = argparse.ArgumentParser(prog='aak init', description='Create an empty personal asset library; never copy native resources.')
    parser.add_argument('directory')
    parser.add_argument('--git', action='store_true', help='initialize a local main branch; no commit or remote is created')
    args = parser.parse_args(argv)
    requested = Path(args.directory).expanduser()
    if requested.is_symlink():
        raise ValueError('library destination must not be a symlink')
    target = requested.resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError('library destination must be absent or empty; existing content is preserved')
    seed = Path(__file__).parent / 'resources/library'
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.aak-init-', dir=target.parent) as temporary:
        stage = Path(temporary) / 'library'
        shutil.copytree(seed, stage)
        for path in stage.rglob('*.md'):
            path.write_text(path.read_text(encoding='utf-8').replace('{{LIBRARY_NAME}}', target.name), encoding='utf-8')
        marker = {'format_version': 1, 'name': target.name, 'tool': 'AgentAssetKit'}
        (stage / '.agent-asset-kit.json').write_text(json.dumps(marker, indent=2) + '\n', encoding='utf-8')
        if target.exists():
            target.rmdir()  # Fails if another process added content; never replace it.
        stage.rename(target)
    if args.git:
        result = subprocess.run(['git', 'init', '-b', 'main', str(target)], capture_output=True, text=True, encoding='utf-8', timeout=60)
        if result.returncode:
            raise ValueError('library files retained, but Git initialization failed: ' + result.stderr.strip())
    return {'initialized': str(target), 'git_initialized': args.git, 'packages': 0,
            'native_resources_changed': False, 'remote_configured': False,
            'next': 'configure local targets and Gitleaks, then review your first collection'}


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


def library_root(explicit, default_command=None):
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
    if default_command:
        default = Path.home() / '.agent-asset-kit'
        if default.is_symlink():
            raise ValueError('default library must not be a symlink')
        if default_command == 'try' and not default.exists():
            return default
        if default.exists():
            return validate_library(default)
    raise ValueError('no asset library found; use aak init DIRECTORY or select one with --root DIRECTORY')


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
            print(json.dumps(initialize(remaining[1:]), ensure_ascii=False, indent=2))
            return 0
        quick = remaining[0] if remaining and remaining[0] in ('try', 'save', 'share') else None
        default_command = remaining[0] if remaining and remaining[0] in ('agent', 'catalog', 'status') else quick
        options = remaining[:remaining.index('--')] if '--' in remaining else remaining
        if not remaining or '--help' in options or '-h' in options:
            root = Path.cwd()
        else:
            root = library_root(selected.root, default_command)
        os.environ['AGENT_ASSET_KIT_ROOT'] = str(root)
        from .assets import main as operate
        return operate(remaining or ['--help'], initialize_default=quick == 'try' and not root.exists())
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'status': 'STOPPED', 'reason': str(error)}, ensure_ascii=False))
        return 2
