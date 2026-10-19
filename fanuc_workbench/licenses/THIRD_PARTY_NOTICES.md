# Third-party notices

FanucMCPServer's original code is covered by the repository's MIT license.
Adapted third-party code retains its upstream copyright and license notices.
These notices and license texts are included in desktop and headless wheels.

## ONE Robotics go-fanuc

- Source: [onerobotics/go-fanuc](https://github.com/onerobotics/go-fanuc)
- Pinned commit: `a32123825912d38cface5669cf75ab8b17299a82`
- Upstream files reviewed: `http_client.go`, `parse.go`
- Adaptation: the `/MD/<file>?_TEMPLATE=` HTTP retrieval convention in
  `fanuc_workbench/services/http_files.py`; parser format coverage informed
  additional synthetic regression cases.
- Copyright (c) 2019 ONE Robotics Company
- License: [MIT](go-fanuc-MIT.txt)

The HTTP adapter adds exact-file policy, same-host origins, byte limits,
HTML/redirect rejection, separate optional credentials and disabled environment
proxies. It does not adopt the upstream write API or network defaults.

## ONE Robotics vqlog.js

- Source: [onerobotics/vqlog.js](https://github.com/onerobotics/vqlog.js)
- Pinned commit: `49287e0ad706154cf3e2d2a2a6e13a0cd4d3648b`
- Upstream files adapted: `lib/vqlog/entry.js`, `event.js`, `utilities.js`,
  `log.js` and the ten event classifiers under `lib/vqlog/events/`.
- Adaptation: `fanuc_workbench/extensions/vqlog/feature.py`, including the event
  field formats and conventional 2 ms tick default.
- Copyright (c) 2014 Jay Strybis
- License: [MIT](vqlog-MIT.txt), also retained as `LICENSE.txt` inside the
  self-contained VQLOG extension release.

The Python adaptation preserves unknown/malformed rows, uses null for wildcard
IDs, corrects skip-field extraction, bounds input/output and exposes tick period
as an unverified analysis parameter. It does not silently replace missing values
or assume clock wraparound.

## fanuc_ucl

- Source: [valstad-shipworks/fanuc_ucl](https://github.com/valstad-shipworks/fanuc_ucl)
- Pinned commit: `ce5b12ddc5b567d3f9cacf0d19e09e59ec89be4b`
- Upstream files adapted: `src/hmi/proto/wire.rs`, `src/hmi/proto/ports.rs`
  and the session handshake in `src/hmi/mod.rs`.
- Adaptation: the bounded synchronous Python SNPX reader in
  `fanuc_workbench/services/snpx.py`.
- Copyright 2026 Bowan Foryt
- License: [Apache License 2.0](fanuc-ucl-Apache-2.0.txt).
  No separate upstream NOTICE file was present at the pinned revision.

The adaptation implements session setup and memory reads, omits the upstream
assignment-clearing command, exposes no write/command API, validates exact
read policy and framing, bounds capture duration and preserves wire evidence.
It does not use the upstream high-level `connect()` method, which also clears
assignments. It does not incorporate RMI, Stream Motion or proprietary binaries.
