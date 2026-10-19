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
