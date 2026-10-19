# Chat permissions and safeguards

Open **Settings > Safeguards and policies…** to edit existing robot policies and
optional broker policies. **MCP access > All safeguards…** opens the same editor.
Use **New policy…** when a robot policy does not exist. No controller connection
is made by loading, reviewing or saving these local settings.

## Robot policy

| Area | Editable controls |
| --- | --- |
| Server safeguards | Online-write permission, chat/client-form approval method, prepared-change expiry, file/backup limits, concurrent/queued requests, controller-lock waiting, evidence/edit directories. |
| Per-robot connection | Explicit host, transport, port, deadline, username, password environment-variable name, remote read roots, native provider and sample provider. |
| Per-robot write scope | Exact replacement/creation paths, native R/PR targets, program deployment names and description targets/origin/encoding/size. |
| HTTP reads | Explicit origin, exact file allowlist, username and credential environment-variable name. |
| SNPX reads | Enable/disable, port, exact I/O targets, allowed memory ranges and explicit group/analog decoding. |

Clearing **Allow reviewed controller writes** blocks controller uploads,
description changes, native R/PR changes and native program deployment. Local
backups, analysis and edit staging remain available. Enabling it still requires
the exact per-robot target allowlists and the normal proposal/approval workflow.
This does not add motion or program execution.

The robot selector supports multiple robots, adding/renaming IDs and removing a
robot from the draft. Target lists use one entry per line. SNPX ranges use
`address,count`; decoding uses `GI[1]=u16` or `AI[1]=i16`. The actual runtime policy
validator checks combinations and supported bounds during review. Unknown local
metadata and other robots are preserved; changing a displayed default does not
rewrite unrelated settings.

## Optional broker

Select the installed broker's **runtime.json**, then click **Load broker runtime**.
The broker's own interpreter validates its schema. It is never started by this
editor. A remembered runtime path is not executed automatically.

The **FANUC service policy** controls service enablement, broker write permission,
exact outer-tool write scopes, reviewed legacy read classifications, argument
budgets, deadlines, retained-session mode, lease duration and resource identity.
Empty exact write allowlists and unrestricted legacy service-wide permission are
distinct choices. The broker's write channel also covers local preparation,
staging, job start/cancellation and worker reload. Robot online-write permission
remains independent and is checked by the robot server.

**Broker server settings** controls the HTTP request budget, loopback port and
authentication-token rotation. The token is masked and omitted from review text.
These settings affect all registered apps. Registered broker policy can be saved
only while the broker is stopped and applicable retained work/recovery barriers
are reconciled. The editor uses the broker's administrative locks and never
stops processes, releases contexts or acknowledges recovery automatically.
Changing a token/port requires reconnecting frontends. A staged service descriptor
can be edited separately without registering it.

FANUC uses an exclusive retained session for assessed broker workflows. Transient
mode loses process-owned plans; shared mode does not isolate chat-owned plans.
Changing a mode or a permission is not acceptance of that workflow.

## Review, save and apply

1. Load the same policy file selected by the intended MCP/native runtime.
2. Change the relevant access controls and targets.
3. Click **Review changes**. Inspect the exact file, source/proposal hashes, diff
   and complete policy. Any subsequent edit invalidates that review.
4. Click **Save reviewed policy**. Changed policies retain the original bytes in
   `.fanuc-policy-history/` beside the file. No-change saves retain original bytes.
   An external edit invalidates the loaded snapshot instead of being overwritten.
5. Reconnect the MCP server, or explicitly load the policy again in Native
   Controller. Refresh/reconnect a stopped broker after its policy changes.
   Retrieve fresh evidence and prepare new changes after a policy transition.

Saving does not elevate an existing session or make a robot change. Save each
policy separately; robot and broker files are not a grouped transaction. Closing
with unsaved edits offers cancel/discard. Store policies and their history outside
the source checkout or in ignored local storage. Backups can contain private
settings; the history directory is excluded from Git.

The **Built-in checks** page lists integrity requirements rather than permission
switches: exact source/proposal hashes, single-use plans, commit preflight,
verified readback, explicit target/path rules, SFTP host-key verification,
supported write classes, protocol bounds, authentication and recovery barriers.
These remain mandatory. This editor changes app/broker policy, not the Codex
host's own Full access/approval settings or the controller's safety configuration.
