#!/usr/bin/env python3
"""Install the pinned official Gitleaks release into a task-owned test directory."""

import argparse
import hashlib
import io
from pathlib import Path
import platform
import tarfile
import urllib.request
import zipfile

VERSION = '8.30.1'
# SHA256 digests from the official v8.30.1 GitHub release assets, checked 2026-11-08.
DIGESTS = {
    'darwin_arm64.tar.gz': 'b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5',
    'darwin_x64.tar.gz': 'dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709',
    'windows_x64.zip': 'd29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e',
    'linux_arm64.tar.gz': 'e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080',
    'linux_x64.tar.gz': '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb',
}


def install(destination):
    system = platform.system().lower()
    arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'
    suffix = system + '_' + arch + ('.zip' if system == 'windows' else '.tar.gz')
    if suffix not in DIGESTS:
        raise RuntimeError('no reviewed scanner artifact for this platform')
    asset = 'gitleaks_' + VERSION + '_' + suffix
    request = urllib.request.Request('https://github.com/gitleaks/gitleaks/releases/download/v' + VERSION + '/' + asset,
                                     headers={'User-Agent': 'AgentAssetKit-E2E'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(64 * 1024**2 + 1)
    if len(data) > 64 * 1024**2 or hashlib.sha256(data).hexdigest() != DIGESTS[suffix]:
        raise RuntimeError('official scanner checksum mismatch; installation stopped')
    binary = 'gitleaks.exe' if system == 'windows' else 'gitleaks'
    if system == 'windows':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo(binary)
            if info.file_size > 64 * 1024**2:
                raise RuntimeError('scanner binary exceeds limit')
            content = archive.read(info)
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            info = archive.getmember(binary)
            if not info.isfile() or info.size > 64 * 1024**2:
                raise RuntimeError('invalid scanner archive entry')
            content = archive.extractfile(info).read()
    target = Path(destination).resolve() / binary
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != content:
        raise RuntimeError('existing test scanner differs; preserve it and choose another directory')
    if not target.exists():
        with target.open('xb') as stream:
            stream.write(content)
        target.chmod(0o755)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', required=True)
    print(install(parser.parse_args().destination))
