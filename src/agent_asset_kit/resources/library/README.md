# {{LIBRARY_NAME}}

A user-owned AgentAssetKit library. The toolkit is installed separately; this repository starts with no skills or personal profile data.

## Getting started

`aak init --agents codex` creates this library at `~/agent-home` by default, initializes Git on main and selects the local Agent. Gitleaks is found on PATH. Preview the Agent instruction entry with `aak attach`, then write it with `aak attach --apply`.

The default library is available from any project directory. For a custom location, run commands inside this library or select it with `aak --root /path/to/library COMMAND`. An explicit root or AGENT_ASSET_KIT_ROOT takes precedence over the enclosing library, then the default location.

## Everyday use

Try a local directory containing SKILL.md with `aak try ./my-skill --to codex`.
Use the name printed by that command in `aak save NAME` to keep your edited copy.
Run `aak share NAME --to claude` when you want a separate copy on another Agent.

## Project tasks

Ask the Agent to consult your library when working on a project. It can read your preferences, shared context and relevant skills, then follow the target project's own instructions and verification commands. Review improvements to an installed skill and keep them with `aak save NAME --from codex`, then share them when needed.

For optional navigation across projects, copy `templates/project-index.md` to `agents/projects.md` and fill in project purposes, repository URLs, instruction paths and related projects. Device-specific clone paths can optionally go in ignored `.local/project-paths.md`. Read only the entries relevant to the task. [Project index template](templates/project-index.md)

## Advanced collection

If initialization did not include `--agents`, run `aak configure --agents codex` before collection. `aak status` shows the selected library and whether local setup is complete.

```sh
aak collect
```

Review complete candidates under `.local/candidates/` before acceptance:

```sh
aak accept CANDIDATE_ID --kind custom --agent codex --reviewed
aak sync --agent codex --message "Accept reviewed resources" --local-only
aak deploy
aak deploy --apply --plan PLAN_ID
```

Git is initialized on main by default, without a commit or remote. Configure its author before the first local sync. Gitleaks is required for collection, acceptance, sync and deployment. Publication defaults to disabled.

Read AGENTS.md for content scope. Keep client identities, native memory, authentication, complete project histories and knowledge-base originals at their owning sources. Client installation paths, instance aliases, candidates, baselines and receipts stay in ignored `.local/`; verification artifacts stay in ignored `artifacts/`.

Collect native edits before refresh/deploy, create an isolated `workspace`, then select its returned directory with `--root`. Revision, merge, trial, promotion and recovery commands are described in the toolkit's workflow documentation and `aak --help`.

A valid .agent-asset-kit.json marker identifies this library. Passing --root never adopts an ordinary directory. Skill source metadata and domain references need content review before sharing; local-source provenance may itself contain a local Git URL.
