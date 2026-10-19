# Controller interfaces and software requirements

MCP is the connection between a chat/API client and the local FanucMCPServer
process. It is not a protocol implemented by the robot. The local server makes
outbound connections to explicitly configured controller services. No Windows
desktop automation is used for these controller operations.

## Interfaces used by this project

| Capability | Controller interface | Requirements and limits |
| --- | --- | --- |
| List/retrieve files, logs and backups; reviewed application-file transfer | FTP, or SFTP when selected in the profile | The controller must provide that server and grant the required file/device access. FTP normally uses TCP 21 plus passive data connections; SFTP normally uses TCP 22. Ports are configurable. |
| Default R/PR/I/O samples and offline diagnostics | Retrieve controller-generated `NUMREG.VA`, `POSREG.VA`, `IOSTATE.DG` and other evidence over the selected file transport, then parse locally | Availability varies by controller software, file devices and permissions. This does not scrape controller web pages. Captures are sequential and sampling is bounded. |
| Optional exact-file reads, logs and file-based samples | HTTP(S) generated-file retrieval under `/MD/` with an explicit origin and file allowlist | Disabled by default; same-host origins, bounded bytes and optional separate Basic credentials. No directory listing, backup or write fallback. See [HTTP reads](HTTP_READS.md). |
| Optional live description/comment changes | HTTP(S) to the controller's `/karel/ComSet` service, followed by fresh file-listing readback | Requires the controller's ComSet service and suitable HTTP/KAREL access settings. Disabled by default; exact origin/targets, byte policy and approval are required. This is a write even though the endpoint uses HTTP GET. |
| Optional native R/PR/I/O reads, position, tasks, alarms and program metadata; reviewed R/PR changes | FANUC Robot Object (`FRRobot.FRCRobot`) through locally installed COM software, which communicates with the controller over TCP/IP | Requires compatible licensed PC software and controller PC Interface support. COM is the local vendor data API, not window control. Vendor software manages the native connection; the app does not implement a raw socket-messaging server. |
| Experimental digital I/O samples and raw assigned words | SNPX/HMI over TCP, normally 60008 | Explicit read policy; no vendor PC binaries, write API or mapping setup. Word meanings are unverified; controller compatibility/coexistence acceptance remains pending. See [SNPX reads](SNPX_READS.md). |
| Reviewed LS program deployment | Selected FTP/SFTP transport to `md:NAME.LS`, with the controller's ASCII loader; native API preflight/catalog checks and TP/LS capture for verification | Requires the native provider as well as compatible controller ASCII loading support. A successful transfer alone does not prove compilation. No program selection or execution occurs. |

The web server is therefore **not required for ordinary file/log retrieval or
the default file-based samples**. HTTP is used by the optional description-write
path and, when explicitly enabled, the allowlisted HTTP file reader. Optional
native reads use the vendor API even in read-only mode.

This project does not install a custom KAREL program or use User Socket
Messaging, EtherNet/IP or OPC UA as its controller transport. Inspecting
EtherNet/IP evidence in a backup does not create an EtherNet/IP connection.

## Licensed options

Requirements depend on the chosen feature and exact controller/software package:

- **FTP file/log access:** needs an available controller FTP service and appropriate
  access settings. The project does not use PCDK for this path. Check the robot's
  installed package rather than assuming that every controller includes or enables
  the same services.
- **SFTP:** needs a controller-side SFTP **server**, not just a client or a PC-side
  Python library. FANUC identifies S612 as the licensed SFTP Server option on
  R-50iA, and distinguishes it from S613 SFTP Client. Firmware/authentication
  requirements also apply; this does not establish SFTP support on older
  controller families. See [FANUC's SFTP overview](https://techtransfer.fanucamerica.com/tech-transfer/secure-file-transfer-protocol-overview).
- **Native Robot Object access:** needs a licensed compatible FANUC Robot Server
  runtime/PC Developer's Kit installation on the PC and the controller's PC
  Interface capability. This applies to native reads as well as writes. Confirm
  current order codes and runtime licensing with FANUC for the target software;
  installing ROBOGUIDE does not by itself prove entitlement for physical-robot
  deployment. A separate vendor's [Robot Server integration requirements](https://help.visualcomponents.com/5.0/Premium/en/English/Connectivity/Fanuc_robot_plugin.htm)
  document the same PC runtime/controller-interface distinction. Visual Components
  itself is not a dependency of FanucMCPServer.
- **Loading LS source on the controller:** needs ASCII Program Loader, identified
  by FANUC as R796. Copying an already compiled binary file and compiling LS
  source are different capabilities. Reading an available LS listing does not
  establish that its upload/loader option is installed. See
  [FANUC's LS editing/loading requirements](https://techtransfer.fanucamerica.com/tech-transfer/how-to-edit-tp-programs-in-a-text-editor-with-ls-files).
- **HTTP descriptions:** requires the specific built-in service and permissions
  on the target controller. Verify HTTP/KAREL security and option availability
  against its FANUC manuals; no universal licensing requirement is asserted for
  ComSet across controller generations. No custom KAREL application is supplied.
- **SNPX/HMI:** requires a compatible enabled controller service and any licensed
  controller options required by its software package. The standard-library client
  does not require Robot Object/PCDK on the PC. Check the target's FANUC manuals
  and entitlements; this experimental implementation has no controller acceptance.

Original project code is MIT licensed; adapted components retain the licenses in
[third-party notices](../fanuc_workbench/licenses/THIRD_PARTY_NOTICES.md).
FANUC software/options retain their own licenses
and are not redistributed. An offline option-text match is evidence only, not
proof of installed entitlements or working controller services.

## Read-only access

Use **Settings → MCP access…** and leave **Read-only controller access** checked,
or set `allow_writes` to `false` in the local policy. Reconnect the MCP server to
apply it. This blocks all supported controller change classes while retaining
local backups, analyses and staged edits. It does not remove a native-read
licensing requirement, disable controller services for other applications or
stop a write already running in another process.

For approved write use, uncheck the setting and retain exact allowlists and the
configured approval workflow. Motion, program start/stop, I/O forcing, frame
writes and mastering/DCS writes remain unsupported. Native deployment validation
and physical-controller acceptance remain pending. See
[MCP setup](ROBOT_BRIDGE.md), [native API limits](ROBOT_NATIVE_API.md) and the
[coverage matrix](ROBOT_BRIDGE_COVERAGE.md).
