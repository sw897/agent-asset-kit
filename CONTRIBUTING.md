# Contributing

AgentAssetKit manages personal context and reusable skills across Agents and devices. Keep the separately installed tool, the user's Home Repo and independent native skill copies distinct. See [AGENTS.md](AGENTS.md) for development rules and [README.md](README.md) for the product scope.

Use Python 3.11 or later. Keep the CLI installable without runtime Python dependencies. Third-party Agent clients, Git and Gitleaks remain external programs. Skill capability requirements are portable declarations; service connections, authentication and aggregate entries belong to clients or external managers.

Keep executable source under `src/agent_asset_kit/`, reusable blank library files under `src/agent_asset_kit/resources/library/`, and synthetic scenarios under `tests/`. Do not add personal skills, profiles, live credentials, native databases, or raw session logs.

## Install the working version

From the tool repository, create an isolated environment:

```sh
python3 -m venv .venv
```

Activate it with `. .venv/bin/activate` on macOS/Linux, or `.\.venv\Scripts\Activate.ps1` in Windows PowerShell. Use `python` instead of `python3` when that is the Python 3.11+ command on the device. Then install the current source:

```sh
python -m pip install .
aak --version
aak --help
python -c "import agent_asset_kit; print(agent_asset_kit.__file__)"
```

Check that `aak` and the imported package belong to this environment. Reinstall after source or bundled-resource changes. Build release checks from a fresh source snapshot and install into a new environment to avoid stale build files. Do not initialize a personal Home Repo in the tool repository or use a real Agent installation as a test fixture.

## Validate changes

Review the diff and run relevant end-to-end scenarios. Tests must exercise the real CLI in synthetic homes and temporary Git repositories. They must not change installed agents or contact a model/business provider. External-source scenarios use synthetic Git repositories; the runner may download the pinned scanner if `--scanner` is omitted. Write receipts under ignored `artifacts/e2e/` and state the actual platforms and clients tested.

```sh
python tests/e2e/ci.py --scanner /absolute/path/to/gitleaks
git diff --check
```

Add a behavioral scenario for a meaningful new failure mode. Avoid tests that restate implementation details. Never automatically discard conflicts, reset user changes, propagate a missing copy as a deletion, or switch a trial globally.

Command or workflow changes must update the README, bundled `aak guide`, relevant documents and blank library templates together. Check relative links and executable examples as well as prose. A source checkout can contain newer resources than an installed CLI; verify the installed package when changing bundled files. Keep project instruction templates static and limited to reusable behavioral constraints.

The tool's tests belong here. A personal Home Repo should consume public commands such as `aak audit --library-only`, rather than copying internal modules or the generic test suite. Test coverage and a Home Repo CI example are in [docs/validation.md](docs/validation.md).
