# Chat permissions and safeguards

Open **Settings > Safeguards and policies…** to edit existing robot policies and
app-owned host policies. **MCP access > All safeguards…** opens the same editor.
Use **New policy…** when a robot policy does not exist. No controller connection
is made by loading, reviewing or saving these local settings.

## Robot policy

| Area | Editable controls |
| --- | --- |
| Server safeguards | Online-write permission, exact-list or broad supported-target write scope, chat/client-form approval method, prepared-change expiry, file/backup limits, concurrent/queued requests, controller-lock waiting, evidence/edit directories. |
| Per-robot connection | Explicit host, transport, port, deadline, username, password environment-variable name, remote read roots, native provider and sample provider. |
| Per-robot write scope | Exact replacement/creation paths, native R/PR targets, program deployment names and description targets/origin/encoding/size. |
| HTTP reads | Explicit origin, exact file allowlist, username and credential environment-variable name. |
| SNPX reads | Enable/disable, port, exact I/O targets, allowed memory ranges and explicit group/analog decoding. |

Clearing **Allow reviewed controller writes** blocks controller uploads,
description changes, native R/PR changes and native program deployment. Local
backups, analysis and edit staging remain available. Enabling it still requires
the selected target-scope policy and the normal proposal/approval workflow.
This does not add motion or program execution.

**Controller write scope** defaults to `allowlisted`, requiring the existing exact
replacement, creation, R/PR, deployment and description lists. An operator may
select `all_supported` for broad access across all configured robots, including
robots later added to that policy. Exact write lists remain saved but are not used
in this mode. Online writes must still be enabled, and native/description providers
must still be explicitly configured. The mode preserves configured read roots,
explicit per-request target validation, supported application-file classes,
storage-only TXT/CSV creation, R/PR typing, program protection, preserved originals,
approval, preflight, single-use plans and readback. It does not permit arbitrary
hosts, system/safety file writes, I/O forcing, motion or program execution.

Store a PC-specific broad policy outside Git and select that same file in the
desktop and MCP runtime. It applies to every chat using that policy after the
MCP server reconnects; saving alone does not elevate existing sessions. The
experimental app host has a separate write channel when that host is used.

The robot selector supports multiple robots, adding/renaming IDs and removing a
robot from the draft. Target lists use one entry per line. SNPX ranges use
`address,count`; decoding uses `GI[1]=u16` or `AI[1]=i16`. The actual runtime policy
validator checks combinations and supported bounds during review. Unknown local
metadata and other robots are preserved; changing a displayed default does not
rewrite unrelated settings.

## App-owned framework

The **App framework** tab edits `fanuc-application.json`: service enablement,
host write permission, exact outer-tool write scopes, reviewed read classifications,
argument budgets, deadlines, retained-session mode, lease duration and resource
identity. It preserves backend identity/artifacts and stages a new policy revision
without activation. Finish/release retained work and explicitly refresh the app host.
Local preparation, staging, jobs and reloads also use the host write channel.
Robot online-write permission remains separate.

The **Host settings** tab accepts only the FANUC app host's **runtime.json**.
Click **Load runtime** to select its interpreter for schema validation. Remembered
runtime paths are not executed automatically. Global controls cover the HTTP
request budget, loopback port and authentication-token rotation. The token is
masked and omitted from review text. Saves require a stopped host and reconciled
retained work/recovery, using its administrative locks. The editor never stops
processes, releases contexts or acknowledges recovery automatically.

FANUC review plans use an exclusive retained session. Transient mode loses
process-owned plans; shared mode does not isolate chat-owned plans. Changing a
mode or permission does not establish acceptance. See [the framework workflow](APP_FRAMEWORK.md).

## Review, save and apply

1. Load the same policy file selected by the intended MCP/native runtime.
2. Change the relevant access controls and targets.
3. Click **Review changes**. Inspect the exact file, source/proposal hashes, diff
   and complete policy. Any subsequent edit invalidates that review.
4. Click **Save reviewed policy**. Changed policies retain the original bytes in
   `.fanuc-policy-history/` beside the file. No-change saves retain original bytes.
   An external edit invalidates the loaded snapshot instead of being overwritten.
5. Reconnect the MCP server, or explicitly load the policy again in Native
   Controller. Refresh/reconnect a stopped app host after its policy changes.
   Retrieve fresh evidence and prepare new changes after a policy transition.

Saving does not elevate an existing session or make a robot change. Save each
policy separately; robot and app-host files are not a grouped transaction. Closing
with unsaved edits offers cancel/discard. Store policies and their history outside
the source checkout or in ignored local storage. Backups can contain private
settings; the history directory is excluded from Git.

The **Built-in checks** page lists integrity requirements rather than permission
switches: exact source/proposal hashes, single-use plans, commit preflight,
verified readback, explicit per-request target/path validation, SFTP host-key verification,
supported write classes, protocol bounds, authentication and recovery barriers.
These remain mandatory. This editor changes app policy, not the Codex
host's own Full access/approval settings or the controller's safety configuration.
