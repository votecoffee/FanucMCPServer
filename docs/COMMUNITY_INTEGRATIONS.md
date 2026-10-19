# Community integration notes

Community components are evaluated against the shared-service, exact-target and
read-only contracts. Public documentation and synthetic fixtures belong in the
repository; local controllers, captures and acceptance records do not.

## Incorporated references

| Source | Pinned revision | Integration |
| --- | --- | --- |
| [ONE Robotics go-fanuc](https://github.com/onerobotics/go-fanuc) | `a32123825912d38cface5669cf75ab8b17299a82` | Optional bounded `/MD/` HTTP file reader; additional register/position format coverage |
| [ONE Robotics vqlog.js](https://github.com/onerobotics/vqlog.js) | `49287e0ad706154cf3e2d2a2a6e13a0cd4d3648b` | Self-contained reloadable VQLOG analysis with explicit timing assumptions |
| [fanuc_ucl](https://github.com/valstad-shipworks/fanuc_ucl) | `ce5b12ddc5b567d3f9cacf0d19e09e59ec89be4b` | Experimental read-only SNPX wire adapter with no assignment commands |

Upstream MIT/Apache-2.0 notices are retained in
[third-party notices](../fanuc_workbench/licenses/THIRD_PARTY_NOTICES.md), with
license texts shipped in desktop/headless wheels and VQLOG release snapshots.
Go and JavaScript runtimes are not required. The Python adaptations add bounded
input, unavailable-data handling, policy checks and synthetic regression tests.

## SNPX implementation boundary

[fanuc_ucl](https://github.com/valstad-shipworks/fanuc_ucl), reviewed at
`ce5b12ddc5b567d3f9cacf0d19e09e59ec89be4b`, informed the experimental SNPX reader.
It supplies Rust/Python bindings, typed registers/positions, protocol tests and
joint-unit conventions. Its implementation is Apache-2.0 licensed. Controller
SNPX availability and options must be checked for the target controller package;
open-source client software does not establish entitlement or compatibility.

The [HMI implementation](https://github.com/valstad-shipworks/fanuc_ucl/blob/ce5b12ddc5b567d3f9cacf0d19e09e59ec89be4b/src/hmi/mod.rs)
can issue `CLRASG` during initialization and `SETASG` when assigning variables.
These mutate controller communication mappings and can affect other clients.
They must not be hidden inside a read-only connection or sampling operation.

The shared Python adapter distinguishes session setup from assignment commands
and exposes only exact digital reads and bounded raw `%R` words. It omits the
upstream clearing step entirely. It cannot verify assignment identity and never
decodes raw words as robot R/PR values. An offline feature worker receives no
controller handle. RMI/stream-motion and mapping setup remain outside its scope.

The provider defaults to disabled and has simulated protocol/MCP coverage only.
Controller acceptance remains pending for handshake behavior, mapping ownership,
coexistence and recovery. See [SNPX reads](SNPX_READS.md) for configuration,
evidence and the explicit unverified-mapping contract.

## Other candidates

- [fanucpy](https://github.com/torayeff/fanucpy) offers a custom KAREL/socket
  architecture, but requires installed controller programs and relevant options.
- [TP+](https://github.com/kobbled/tp_plus) offers offline program authoring. Its
  README/license-file discrepancy should be resolved before incorporating code.
- Public SDK examples backed by commercial binaries are not treated as freely
  reusable implementations. Check source availability and each component's
  actual license before adoption.
