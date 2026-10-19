# Community integration notes

Community components are evaluated against the shared-service, exact-target and
read-only contracts. Public documentation and synthetic fixtures belong in the
repository; local controllers, captures and acceptance records do not.

## Incorporated references

| Source | Pinned revision | Integration |
| --- | --- | --- |
| [ONE Robotics go-fanuc](https://github.com/onerobotics/go-fanuc) | `a32123825912d38cface5669cf75ab8b17299a82` | Optional bounded `/MD/` HTTP file reader; additional register/position format coverage |
| [ONE Robotics vqlog.js](https://github.com/onerobotics/vqlog.js) | `49287e0ad706154cf3e2d2a2a6e13a0cd4d3648b` | Self-contained reloadable VQLOG analysis with explicit timing assumptions |

Upstream MIT notices are retained in
[third-party notices](../fanuc_workbench/licenses/THIRD_PARTY_NOTICES.md), with
license texts shipped in desktop/headless wheels and VQLOG release snapshots.
Go and JavaScript runtimes are not required. The Python adaptations add bounded
input, unavailable-data handling, policy checks and synthetic regression tests.

## SNPX evaluation

[fanuc_ucl](https://github.com/valstad-shipworks/fanuc_ucl), reviewed at
`ce5b12ddc5b567d3f9cacf0d19e09e59ec89be4b`, is a possible future SNPX/HMI provider.
It supplies Rust/Python bindings, typed registers/positions, protocol tests and
joint-unit conventions. Its implementation is Apache-2.0 licensed. Controller
SNPX availability and options must be checked for the target controller package;
open-source client software does not establish entitlement or compatibility.

The [HMI implementation](https://github.com/valstad-shipworks/fanuc_ucl/blob/ce5b12ddc5b567d3f9cacf0d19e09e59ec89be4b/src/hmi/mod.rs)
can issue `CLRASG` during initialization and `SETASG` when assigning variables.
These mutate controller communication mappings and can affect other clients.
They must not be hidden inside a read-only connection or sampling operation.

A provider must therefore distinguish protocol connection from mapping setup,
preserve and validate operator-owned assignments, avoid clearing shared maps,
and make any mapping change an explicit reviewed operation. Reads must use
known mappings and refuse unavailable/mismatched assignments. Core policy must
own controller credentials and writes; an offline feature worker must not gain
an unrestricted controller handle. RMI/stream-motion operations remain outside
the supported MCP contract.

No SNPX provider or controller-side mapping setup is enabled by this evaluation.
Adding it requires protocol/fixture coverage followed by controller acceptance
for mapping ownership, coexistence, stale references and recovery behavior.

## Other candidates

- [fanucpy](https://github.com/torayeff/fanucpy) offers a custom KAREL/socket
  architecture, but requires installed controller programs and relevant options.
- [TP+](https://github.com/kobbled/tp_plus) offers offline program authoring. Its
  README/license-file discrepancy should be resolved before incorporating code.
- Public SDK examples backed by commercial binaries are not treated as freely
  reusable implementations. Check source availability and each component's
  actual license before adoption.
