# {{LIBRARY_NAME}}

This Home Repo is a user-owned library for personal context, shared memory, saved skills, service knowledge and templates. AgentAssetKit (aak) is the separately installed tool that creates and maintains the library and manages skill trials, reviewed updates, distribution and synchronization across Agents and devices.

Each Agent uses a complete independent skill copy. Reviewed edits can be saved back to this library and shared when needed. This repository starts with no skills or personal profile data.

## Getting started

This library was created by `aak init`, which defaults to `~/agent-home` and initializes Git on main. With `--agents codex`, it also selects the local Agent. If local setup is incomplete, run `aak configure --agents codex`. Gitleaks is found on PATH. Preview the Agent instruction entry with `aak attach`, then write it with `aak attach --apply`.

The default library is available from any project directory. For a custom location, run commands inside this library or select it with `aak --root /path/to/library COMMAND`. An explicit root or AGENT_ASSET_KIT_ROOT takes precedence over the enclosing library, then the default location.

Run `aak status` to check the selected primary library and local setup. Tool upgrades update `aak guide`, but do not overwrite this library's files or templates. Consult the current guide before manually updating copied instructions.

## Everyday use

Try a local directory containing SKILL.md with `aak try ./my-skill --to codex`.
Use the name printed by that command in `aak save NAME` to keep your edited copy.
Run `aak share NAME --to claude` when you want a separate copy on another Agent.

These commands run against the primary library, not an isolated review worktree. `try` and `share` write the selected client immediately; `save` updates this library. None creates a Git commit. Use `aak agent list` to check enabled targets and `save --dry-run` to inspect edits before saving. Existing native skills without a quick-operation receipt use the collection workflow below.

## Project tasks

Ask the Agent to consult your library when working on a project. It can read your preferences, shared context and relevant skills, then follow the target project's own instructions and verification commands. Review improvements to an installed skill and keep them with `aak save NAME --from codex`, then share them when needed.

For optional navigation across projects, copy `templates/project-index.md` to `agents/projects.md` and fill in project purposes, repository URLs, instruction paths and related projects. Device-specific clone paths can optionally go in ignored `.local/project-paths.md`. Read only the entries relevant to the task. [Project index template](templates/project-index.md)

## Advanced collection

If initialization did not include `--agents`, run `aak configure --agents codex` before collection. `aak status` shows the selected library and whether local setup is complete.

```sh
aak collect
```

Collection stores complete candidates under `.local/candidates/` without modifying native copies. Review them, commit any existing primary-library edits, refresh and create a review workspace:

```sh
aak refresh
aak workspace --agent codex
```

Enter the returned workspace or select its path with `--root`. Review complete files, provenance, license and dependencies, then accept and synchronize:

```sh
aak accept CANDIDATE_ID --kind custom --agent codex --reviewed
aak sync --agent codex --message "Accept reviewed resources" --local-only
```

Use `--kind third-party` when that matches the reviewed source. Local sync commits the workspace and merges it into the primary library, retaining its branch. Return to the clean primary main checkout before previewing and applying bulk distribution:

```sh
aak deploy
aak deploy --apply --plan PLAN_ID
```

Use the preview's `plan_id` and keep filters/options identical. Git is initialized on main by default, without a commit or remote; configure its author before the first local sync. Gitleaks is required for collection, acceptance, sync and actual deployment. Publication defaults to disabled. `sync` stages all allowed changes in the selected checkout, so inspect its diff and index first.

For library-only checks or CI, run `aak audit --library-only`. It checks reviewed packages, references, skill dependency relationships, MCP declarations, tracked paths and immutable release references without local Agent/runtime setup. It does not establish service availability or replace secret scanning. Historical stable references require the relevant Git history.

Read AGENTS.md for content scope. Keep client identities, native memory, authentication, complete project histories and knowledge-base originals at their owning sources. Client installation paths, instance aliases, candidates, baselines and receipts stay in ignored `.local/`; verification artifacts stay in ignored `artifacts/`.

Quick `try` and committed-version `trial start` are different workflows. Revisions marked `--trial` keep the previous stable version for other targets until explicit promotion. Revision, merge, trial, promotion and recovery are described in [the workflow guide](https://github.com/sw897/agent-asset-kit/blob/main/docs/workflows.md), [command reference](https://github.com/sw897/agent-asset-kit/blob/main/docs/commands.md) and `aak --help`.

A valid .agent-asset-kit.json marker identifies this library. Passing --root never adopts an ordinary directory. Skill source metadata and domain references need content review before sharing; local-source provenance may itself contain a local Git URL.
