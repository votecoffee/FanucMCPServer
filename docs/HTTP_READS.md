# Optional controller HTTP file reads

The optional HTTP reader retrieves explicitly allowlisted text files from a
configured controller's MD device. It uses the controller's generated-file
interface rather than scraping HTML or executing KAREL/KCL commands. The request
convention is adapted from [ONE Robotics go-fanuc](https://github.com/onerobotics/go-fanuc).
See [third-party notices](../fanuc_workbench/licenses/THIRD_PARTY_NOTICES.md).

## Configuration

Add these fields to a robot entry in the local MCP JSON policy:

```json
{
  "http_read_url": "https://192.0.2.1",
  "http_read_files": ["md:NUMREG.VA", "md:POSREG.VA", "md:IOSTATE.DG"]
}
```

The origin must use the same host as that robot's FTP/SFTP profile. Scheme and
port are explicit; the URL cannot contain paths, credentials, queries or
fragments. A nonempty allowlist of at most 64 unique flat MD filenames is
required. Supported extensions are VA, DG, LS, LOG, TXT and CSV. Each file must
also be inside the profile's existing read roots. Defaults remain disabled.

For optional HTTP Basic authentication, configure `http_read_username` and
`http_read_password_env` together. The latter names an environment variable;
never put its secret value in JSON or chat. HTTP credentials are separate from
FTP/SFTP credentials. HTTPS uses normal certificate verification. Basic
authentication over HTTP is unencrypted.

## Behavior

- `read_file`, `collect_logs` and file-based samples use HTTP for exactly the
  configured HTTP files. Other files continue using the profile's FTP/SFTP
  transport. An HTTP failure never silently falls back to FTP/SFTP.
- Directory listing, reachability probes, backups and controller write
  preflight/readback continue using FTP/SFTP. HTTP-only file access does not
  establish that those operations are available.
- Native sampling retains precedence when the native provider is enabled.
- `read_file` preserves received bytes and SHA-256 and reports its `transport`.
  HTTP receipts are bound to the configured origin. File-based samples report
  `file_transports` and retain the captured file bytes.
- Requests use GET, bounded timeouts and `max_file_bytes`. Only status 200 is
  accepted. Redirects, HTML/login pages and content-encoded responses are
  refused. Partial failed captures are removed. Environment HTTP proxies are
  disabled; credentials are never forwarded through redirects.
- Read-only mode remains enabled by default. HTTP adds no upload, CGI or
  controller-value write endpoint, and does not broaden write targets.

Reconnect the MCP host after enabling HTTP reads or changing file/credential
policy. Once enabled, endpoint-only host/port updates may use
`reload_connections`, retaining the scheme and all policy. Fresh reads are
required afterward. Keep configuration, credentials and capture evidence local.

Controller web/file interfaces differ across software versions. Offline HTTP
fixtures verify policy, transport and byte handling; physical-controller
compatibility remains a separate acceptance check.
