# Service skill index

No skills are registered in this new library. Existing client skills remain with their clients until reviewed and accepted. Source, service classification, native bindings and conditional dependencies are separate dimensions.

After adding reviewed classification metadata, use `aak index --agent AGENT --reviewed` to generate the service index. Registration does not establish native discovery or successful business behavior.

`index` replaces this generated file. Keep hand-maintained instructions and evidence in the owning package or another library document. manifest.json records skill IDs, hashes and bindings; categories.json defines classifications; releases.json selects release versions; decisions.json retains reviewed source decisions.

MCP capability requirements in `requirements.mcp` are portable metadata. `aak audit` validates their structure and package references; service availability and connection setup are verified in the target client or an external tool.
