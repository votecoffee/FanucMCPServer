# Desktop workflows

## Start menu shortcut

On Windows, `install_and_run.bat` installs the desktop dependencies, creates a
shortcut under **Start > FanucMCPServer**, then opens the app. The shortcut uses
the supplied application icon and the installation's `pythonw.exe`, so launching
from Start does not open a console. Administrator access is unnecessary.

For a manual desktop installation, add or refresh the current user's shortcut:

```powershell
.\.venv\Scripts\python.exe -B -m fanuc_workbench.desktop_install
```

Run this command again from the desired desktop Python environment after moving
or replacing an installation. The shortcut records absolute resolved paths; a
moved virtual environment may also need to be recreated. To remove the shortcut:

```powershell
.\.venv\Scripts\python.exe -B -m fanuc_workbench.desktop_install --remove
```

Removal retains the application, settings and other Start menu entries. The
headless MCP bundle has its own installer and does not create desktop shortcuts.

## Startup and local diagnostics

The desktop records startup and unexpected Python exceptions in a local rotating
`desktop.log`, including launches from Start without a console. A startup error
shows a dialog with the log location. Unexpected GUI callback errors are logged
and reported too. These logs are separate from controller evidence and MCP audit
records; handled operation errors continue to use their existing messages.

Use **Help > Open desktop log folder** to open the directory. Windows logs live
under `%LOCALAPPDATA%\FanucMCPServer\logs`; the log rotates at 1 MiB with
three rotated backups. macOS uses `~/Library/Application Support/FanucMCPServer/logs`;
Linux uses `$XDG_STATE_HOME/FanucMCPServer/logs`, defaulting to
`~/.local/state/FanucMCPServer/logs`. If the directory cannot be written, normal
startup can still continue and an error dialog explains that no log was written.

**Help > About FanucMCPServer** shows the app version and expandable details for
the Python executable, settings directory and desktop log location. Logs stay on
the local machine. Review them before sharing: exception messages can contain
private paths or controller details.

## Connections

Use **Robot > Connection** to edit the selected connection, **New connection** to
add one, or **Manage connections** to add, edit, duplicate and remove profiles in
one dialog. Manager changes stay staged until **Save and select**; Cancel keeps
the original list. Renaming replaces the selected profile. Connection names must
be unique, and the editor validates the address, transport, port and timeout.
Custom ports and timeouts are retained; switching transport adjusts only a
standard FTP/SFTP port. Passwords are available in memory for this app session.

Use the **Saved robot** selector on Backup / History to switch profiles. A successful
connection test, backup, live file sample or raw-file upload remembers that robot
for the next app session.
Selection alone is also remembered when no previous successful connection is
available. Reopening the app selects the profile without automatically connecting.
Passwords are excluded from saved profiles and desktop preferences.
Profiles and desktop preferences are flushed to a temporary file before atomic
replacement. A failed serialization, flush or replacement retains the previous
saved file and removes the incomplete temporary file.
Renaming a connection preserves its remembered successful identity; changing its
endpoint does not mark the new endpoint as successfully connected. Saving or
selecting a profile makes no immediate connection. Desktop profiles configure
FTP/SFTP; MCP and Native Controller access use their separately loaded policy files.

## Tables, panes and tabs

Drag any table column boundary to change its width. Columns use interactive
resizing; their widths are restored on the next launch. Drag the divider between
stacked content panes to allocate more room to either pane, including the watch
table on **Live / Recorder**. Pane sizes are restored too.
The side-by-side dependency tree and unused-program list in **Search / Analysis**
also have a draggable divider whose position is remembered.

Drag tab headers to reorder them. Each tab bar's **Tabs** menu can hide/show
individual pages or show all pages. At least one page stays visible. Tab order,
visibility and the selected page are saved. Hiding a page does not stop an active
recording, scheduled job or controller operation.

Use **Settings > Reset desktop layout** to restore default column widths, balance
the panes, show every tab and restore the original tab order and selection. The
reset applies immediately and is saved for the next launch. It keeps the current
widgets and staged edits in place. Live / Recorder's default value column is
520 pixels wide; you can resize it to suit your watch values.

Desktop preferences live in the OS application configuration directory alongside
`profiles.json`, in `desktop.json`. They contain layout and profile-selection
preferences, independently of MCP access policies and saved controller evidence.
They are local user data and belong outside a public source checkout.

## Live / Recorder

Position-register axes display as wrapped, ordered `axis=value` rows rather than
raw JSON. Hover over the value for the full captured data. Unavailable values
show their error in the tooltip. Display formatting leaves recorded data intact.
Use **Add watch row** and **Remove selected watch rows** to change the watch list,
and drag the vertical splitter to expose more rows. Watch expressions, aliases
and row count are remembered locally (up to 1000 rows); sampled values are not
saved to desktop preferences. Unsupported expressions and duplicate watch names
are rejected before collection.

Each recording is bound to its workspace or desktop file connection and watch
definitions. The recording label identifies the captured source independently
of the current live-file target. Changing the source, workspace, connection or
watch definitions pauses polling and ignores late results from the previous
request. Existing data remains available for export. Choose **New recording**
before collecting a different source/watch list; confirm clearing the current
buffers only after exporting anything you want to keep. Cancel retains them.

**Export recording JSON** saves the source snapshot, watch definitions, captured
values and errors, timeline events and current cycle events in one local file.
Connection passwords are excluded from the source snapshot. Export uses atomic
replacement, retaining the previous file if writing fails. Individual trend/
timeline CSV and cycle JSON exports remain available.

Collection stops at 10,000 received samples per recording, including samples
whose trend rows were later cleared. Export and start a new recording to
continue. Invalid or backwards sample timestamps stop collection while retaining
accepted data. Recordings stay in memory until exported; only watch drafts and
desktop preferences are restored on app launch. Current-workspace snapshots and
timeline analysis exclude watch data belonging to a different source/watch list.

## Fleet / Scheduler

The **Run** checkbox controls both **Back up checked now** and scheduled backups.
Set each interval to 1–525600 minutes. Invalid intervals are shown in the row and
skipped. Enable the in-app scheduler explicitly when ready.

Fleet rows are bound to the connection settings displayed when they were loaded.
If those settings change, the old row is skipped until profiles are reloaded.
Reloading preserves choices, intervals and results for unchanged connections;
new or changed connections start unchecked. A reload requested during an active
fleet job waits for that job to finish before rebuilding the rows.

## Backup progress

Backup / History shows an activity indicator during directory discovery, then a
percentage based on processed listed files. ASCII companion attempts are included
in their source file's work. This is file-based progress, independent of file size
or transfer speed. The bar reaches 100% after the manifest has been saved.
Failed files and unavailable companions appear in the final counts and manifest;
100% means the capture finished, and can include failures or warnings.

## Create a robot access policy

Use **Settings > Create robot policy**, **Settings > MCP access > New policy**,
or **Native Controller > New policy**. The form starts with the selected connection
profile and read-only access. Enter a robot ID, connection details, read roots and
an optional password environment-variable name. The form never saves a password.

To permit reviewed writes, enter exact permitted remote filenames, storage-file
creation paths, register targets, native program names or description targets,
then clear **Read-only controller access**. Native R/PR/program targets require
the explicit `frrobot` provider; descriptions require the controller's HTTP(S)
origin. Wildcards and unsupported/system-file targets are refused. Existing
operation-specific approval, target and readback checks still apply.

Click **Review policy** to validate and inspect the complete JSON, then **Create
policy** to choose a new `.json` file. Editing any form input requires a fresh
review. Existing files are never overwritten. Prefer `robot_bridge.local.json`
outside the source checkout or under its ignored `.local/` directory. Relative
`robot-state` and `robot-edits` directories resolve beside the policy file.

Creating a policy makes no controller connection and changes no loaded session.
Select the file in MCP setup and reconnect that server, or click **Load** in
Native Controller when ready. The selected policy path and native robot selection
are remembered locally; the app does not automatically load/connect at launch.

## I/O / PLC Map import

Open **I/O / PLC Map > PLC Tags / Import** and click **Import PLC tags (CSV / L5X)**.
The import reads the selected export locally and leaves its bytes unchanged.
It loads a searchable catalog; it does not connect to a PLC or change either
controller. For a binary PLC project, export its tags as CSV or its source as
L5X using the engineering application first.

CSV supports a `Name`, `Tag`, `Tag Name` or `PLC Tag` header, optional scope,
description, data type and alias target columns, and an optional explicit
`Robot I/O` column. Comma, semicolon and tab delimiters are recognized. Rockwell
`TYPE,SCOPE,NAME,...` tag/alias/comment rows and the app's exported I/O map CSV
are supported. Program-scoped tags retain `Program:<scope>.` qualification.

L5X loads controller/program tags, explicit alias targets, UDT member definitions
and per-instance member comments. Arrays are listed without expanding indices;
recursive/deep UDT expansion is bounded and reported. The catalog uses available
declarative tag metadata; it does not derive physical robot bit routing from PLC
logic or module sizes. Exported aliases remain visible for mapping review.

Select the desired robot I/O row in the upper table, select a PLC tag below, and
click **Assign selected tag to selected I/O**. The qualified tag is staged in the
upper grid. For multiple mappings, **Review suggested mappings** uses explicit
I/O tokens such as `DI[1]` in imported descriptions/comments or the CSV Robot I/O
column. Missing/ambiguous targets stay unapplied. Existing assignments are kept
unless **Include replacements** is checked in the full preview.

Filtering also scopes suggestions. Use `tag:Robot1.` to match a qualified tag-name
prefix, or `scope:Cell` to select a program scope. Plain text searches tags,
descriptions and aliases, which can include multiple references to the same I/O.
Only 500 matching catalog rows are displayed at once; refine the filter for a
large export. The suggestion review covers all matches, including rows beyond
the displayed page, and shows every proposed assignment.

Click **Save PLC tag map** to save staged grid assignments to the local workspace.
The CSV/L5X source and controller are unchanged. The original export's SHA-256 is
available on the import status tooltip. Import bounds are 32 MiB, 20000 tag/member
entries, and bounded XML/CSV depth, rows and fields. DTD/entity declarations and
malformed/conflicting definitions are refused. The imported catalog remains in
memory for the current session; saved workspace mappings persist across sessions.
