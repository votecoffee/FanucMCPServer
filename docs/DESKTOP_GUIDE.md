# Desktop workflows

## Connections

Use **Robot > Connection** to create or edit a connection profile and the
**Saved robot** selector on Backup / History to switch profiles. A successful
connection test or backup remembers that robot for the next app session.
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
