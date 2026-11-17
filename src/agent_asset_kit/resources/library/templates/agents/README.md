# Project instruction templates

Keep only reusable behavioral constraints in these templates. Discover project names, goals, stacks, paths and commands from the project's existing code and documentation instead of copying them into AGENTS.md.

Run `aak init-project PROJECT` for the base constraints. Select `--template software`, `research` or `documents` only when its extra constraints are relevant. Read the proposed file and diff, preserve existing instructions, then apply with `aak apply-project PLAN_ID --reviewed`. A changed target invalidates the preview; existing generated sections are never overwritten automatically.

Run these commands against an initialized Home Repo; PROJECT identifies the separate target project. Applying a plan also requires local configuration and Gitleaks, and writes only the reviewed project instruction file, with its backup and receipt retained locally. Creating or using a target project does not require an AAK review workspace.

Templates are static Markdown; variable substitution is not supported.

Tool installation updates do not overwrite these copied templates. Review and merge template improvements explicitly. Project-specific instruction edits continue in the target project's own AGENTS.md.

References: [AGENTS.md](https://github.com/agentsmd/agents.md) and [Anbeeld's global instructions](https://github.com/Anbeeld/AGENTS.md). Adapt principles rather than copying project facts, fixed tools or mandatory delegation.
