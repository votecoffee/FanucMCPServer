# Local MakeTP/PrintTP validation

Inspect `capabilities` before choosing a workflow. The `standalone-offline`
profile has no native utilities. The explicitly selected
`standalone-local-native` profile adds pinned local compilation; it still has no
controller, GUI, extension, deployment or shared-host routes. Never change a
connection/profile or policy to bypass a refusal.

Use an imported saved LS artifact and its exact source hash. Discover
`list_native_toolchains`; tools cannot supply an arbitrary executable, command,
configuration, context, destination or environment. Ask `prepare_native_validation`
for a review binding the source and selected toolchain hashes. Present its source,
core version, compiler/printer, configuration/profile/context hashes, timeout and
plan SHA-256. Then call `run_native_validation` for that one reviewed plan.

Inspect the returned diagnostics and compiled/listing comparison artifact.
Compilation is not robot execution or deployment acceptance. No program is
loaded, selected or run on a controller. A busy validation leaves another plan
prepared. Discard unused review plans with `discard_native_validation`; that
operation cannot stop active work or clear uncertainty.

After a lost response, use `native_validation_status` and retained artifacts.
Do not replay the plan, silently start another worker, reconcile uncertainty,
close user software or substitute a shell/native API command. Unverified
outcomes retain their receipts and outputs. Native tool availability and a
source/fixture pass are not evidence of an accepted real vendor-tool pilot.
