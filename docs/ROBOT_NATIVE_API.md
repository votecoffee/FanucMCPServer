# Native controller data, register changes and program deployment

This development lane adds shared services and a GUI view for native R/PR/I/O,
current position, tasks, alarms and program information; reviewed R/PR value
changes; and native loading/compilation of LS programs. The MCP catalog carries
these operations without adding permanent MCP tool schemas.

The first provider uses the locally installed FANUC `FRRobot.FRCRobot` public
COM API to connect to the configured robot host over TCP. COM here is a vendor
data API, with no desktop/window automation. It requires a compatible licensed
FANUC installation. Other machines/controllers must verify provider availability.
The adapter uses bounded, separate worker processes and never automatically
retries a failed write. The host owns policy, preserved reads, prepared plans,
confirmation, audit, preflight and readback. Feature code cannot dispatch native
writes or arbitrary vendor API/KCL methods.

Native access is disabled by default. A local operator explicitly selects the
provider and exact register/program targets. Register changes preserve type and
position metadata unless the reviewed proposal explicitly supports a change;
initial PR edits change existing axis values only. Uninitialized PRs are refused.
Program loading accepts validated LS source and uses the native loader; uploading
an LS file alone is not reported as successful compilation. Program deployment
never selects, starts or executes a program.

Every change starts from a preserved native read tied to robot, endpoint, target
and SHA-256. Preparation freezes the proposal. Commit requires exact approval,
rechecks state and consumes the plan before a vendor write. Readback and loader
evidence distinguish verified results from uncertain failures. There is no
atomic compare-and-swap guarantee against other clients and no automatic rollback.

Development uses the user's two configured ROBOGUIDE virtual controllers. Native
test changes require review of the exact prepared test proposal before committing.
No physical controller or motion operation is part of this work.
