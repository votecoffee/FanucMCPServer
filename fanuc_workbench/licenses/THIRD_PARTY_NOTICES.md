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
