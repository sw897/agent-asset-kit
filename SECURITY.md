# Security boundaries

AgentAssetKit stores reviewed copies and provenance; it does not sandbox skills or certify them as safe. The `--reviewed` flag is an operator acknowledgement. Gitleaks detects supported secret patterns; it cannot establish authorization or eliminate every possible sensitive value.

Keep credentials, cookies, private keys, native databases, raw transcripts and local runtimes out of asset libraries. Local configuration, candidates, baseline snapshots, plans and backups are ignored by Git. Treat those local files as private as well.

Preview target paths and changes before applying. Deployment and trials bind applications to frozen plans and reject changed sources, configuration or targets. Unknown changes and interrupted operations must be reconciled explicitly.

Acceptance prepares package and metadata snapshots before switching content. A failed write rolls back only recognized versions; interrupted receipts block further operations until reviewed recovery in the original workspace. Recovery preserves prepared copies and refuses subsequent edits. Legacy receipts without complete snapshots require manual reconciliation.

Native discovery starts the configured clients in skill catalog modes without a model turn. MCP capability declarations are validated offline for structure and package references. Connection state, authentication, aggregate entries and tool availability are maintained and verified by the target client or external tools. Asset workflows do not call an MCP manager or change MCP configuration; legacy local connection records are left untouched.

Local `audit` and `doctor` can invoke a declared package Python interpreter to read installed distribution metadata. Native discovery invokes client programs or runtimes, and custom commands/probes are trusted local executables. Review those programs independently. `audit --library-only` skips local runtime and native-client checks; it validates library content and declarations, not operational readiness or every possible secret.

`try` and `share` write client files immediately, and `save` updates library assets without making a Git commit. Bulk `deploy` and `trial start` use explicit preview/apply plans. `tick` is an authorized combined operation that generates and applies its deployment plan in the same run; the macOS scheduler runs collection only. Check the [command write scopes](docs/commands.md) before automating a workflow.

For public issues, provide a minimal reproduction with synthetic data. Keep credentials, private skill libraries and personal logs out of public reports.

An explicit root needs a valid library marker, and a worktree must belong to an initialized primary library. Canonical content, shared memory and local metadata cannot traverse library links. Deployment scans the exact prepared version before switching native targets, including a historical stable revision. Interrupted preparation and rollback retain ownership until explicit recovery.

Client installation paths and aliases are local state. Reviewed domain context and explicitly accepted local-source provenance can contain paths in a user's private library; content review determines whether sharing is appropriate. No automatic personal-information anonymization is provided.

The `.local/` ignore rule protects shared Git content, not the privacy of local files or machine backups. Candidates, prepared copies and receipts can include private skill content and paths. Inspect and redact reports before sharing; keep recovery data until the operation is resolved. Diagnostic steps and recovery entry points are documented in [troubleshooting](docs/troubleshooting.md).
