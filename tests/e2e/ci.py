#!/usr/bin/env python3
"""Compile and run CLI scenarios using synthetic data only."""

import argparse
from pathlib import Path
import platform
import py_compile
import subprocess
import sys

from install_scanner import install

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scanner', help='use an existing scanner; otherwise download and verify the pinned official release')
    args = parser.parse_args()
    scanner = Path(args.scanner).resolve() if args.scanner else install(ROOT / '.local/ci-tools')
    for path in (ROOT / 'src/agent_asset_kit').glob('*.py'):
        py_compile.compile(str(path), doraise=True)
    suites = ['bootstrap.py', 'boundaries.py', 'transactions.py', 'workflows.py', 'skill_dependencies.py']
    if platform.system() != 'Windows':
        suites += ['run.py', 'skill_catalog.py']
    for name in suites:
        result = subprocess.run([sys.executable, ROOT / 'tests/e2e' / name, '--scanner', scanner], cwd=ROOT)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
