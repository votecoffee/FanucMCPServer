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

## Connections

Use **Robot > Connection** to create or edit a connection profile and the
**Saved robot** selector on Backup / History to switch profiles. A successful
connection test, backup, live file sample or raw-file upload remembers that robot
for the next app session.
Selection alone is also remembered when no previous successful connection is
available. Reopening the app selects the profile without automatically connecting.
Passwords are excluded from saved profiles and desktop preferences.

## Tables, panes and tabs

Drag any table column boundary to change its width. Columns use interactive
resizing; their widths are restored on the next launch. Drag the divider between
stacked content panes to allocate more room to either pane, including the watch
table on **Live / Recorder**. Pane sizes are restored too.

Drag tab headers to reorder them. Each tab bar's **Tabs** menu can hide/show
individual pages or show all pages. At least one page stays visible. Tab order,
visibility and the selected page are saved. Hiding a page does not stop an active
recording, scheduled job or controller operation.

Desktop preferences live in the OS application configuration directory alongside
`profiles.json`, in `desktop.json`. They contain layout and profile-selection
preferences, independently of MCP access policies and saved controller evidence.
They are local user data and belong outside a public source checkout.

## Live / Recorder

Position-register axes display as wrapped, ordered `axis=value` rows rather than
raw JSON. Hover over the value for the full captured data. Unavailable values
show their error in the tooltip. Display formatting leaves recorded data intact.
Use **Add watch row** and **Remove selected watch rows** to change the watch list,
and drag the vertical splitter to expose more rows.

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
