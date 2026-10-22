# Native register and program changes

For native data, inspect the `core.controller.*`, `core.registers.*` and
`core.programs.*` operation descriptors. These require an explicitly configured
vendor provider. Preserve a native register/program inspection before preparing
a change. Review robot/endpoint, target, before/proposed values or source hashes
and the exact proposal SHA-256. Native changes use `commit_change` only after
explicit approval and client confirmation. R readback is exact; PR axes use the
stated numerical tolerance with exact metadata. LS deployment never selects or
runs a program. A failed native commit consumes its plan: inspect actual state
and evidence before preparing another change. Do not retry or roll back blindly.

