# Project instruction templates

Use base.md with one module only when relevant. Inspect existing project rules and real entry points before generating a preview. Preserve existing instructions and replace variables with observed project facts.

Run `aak init-project PROJECT --purpose PURPOSE --template software --command VERIFIED_COMMAND`, read the proposed file and diff, then apply with `aak apply-project PLAN_ID --reviewed`. A changed target invalidates the preview. Do not put personal profiles, machine-specific paths or this library's location into generated project rules.
