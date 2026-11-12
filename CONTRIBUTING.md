# Contributing

Use Python 3.11 or later. Keep the CLI installable without runtime Python dependencies. Third-party Agent clients, Git and Gitleaks remain external programs. Skill capability requirements are portable declarations; service connections, authentication and aggregate entries belong to clients or external managers.

Keep executable source under `src/agent_asset_kit/`, reusable blank library files under `src/agent_asset_kit/resources/library/`, and synthetic scenarios under `tests/`. Do not add personal skills, profiles, live credentials, native databases, or raw session logs.

For changes, review the diff and run the relevant end-to-end scenarios. Tests must exercise the real CLI in synthetic homes and temporary Git repositories. They must not change your installed agents or contact a provider. Write receipts under ignored `artifacts/e2e/` and state the actual platforms and clients tested.

```sh
python3 tests/e2e/ci.py --scanner /absolute/path/to/gitleaks
git diff --check
```

Add a behavioral scenario for a meaningful new failure mode. Avoid tests that restate implementation details. Never automatically discard conflicts, reset user changes, propagate a missing copy as a deletion, or switch a trial globally.
