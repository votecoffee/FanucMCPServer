# Optional SNPX/HMI reads

The experimental shared reader uses SNPX/HMI over TCP, normally port **60008**.
It uses Python's standard library and does not require Robot Object/PCDK client
binaries. The target controller must provide a compatible SNPX service and any
required licensed options. Check its FANUC documentation and installed package;
this project does not establish option entitlement or universal compatibility.

Only simulated TCP and MCP tests have passed. Controller acceptance remains
pending, including coexistence with other HMI/SNPX clients, the session handshake,
port conventions, service availability and stale/missing assignments. Network
responses do not authenticate the physical controller. SNPX traffic has no TLS
or application credentials in this implementation.

## Local configuration

Add the following fields to a robot entry in your ignored local policy. The host
and total socket-I/O timeout come from that robot's existing `profile`:

```json
{
  "profile": {
    "name": "Example robot",
    "host": "192.0.2.1",
    "roots": ["md:"],
    "timeout": 10
  },
  "sample_provider": "snpx",
  "snpx_read": {
    "port": 60008,
    "io_targets": ["DI[1]", "DO[9]", "GI[1]", "AI[1]"],
    "io_word_formats": {"GI[1]": "u16", "AI[1]": "i16"},
    "word_ranges": [{"address": 1, "count": 16}]
  }
}
```

These are fields inside a robot entry, not a complete bridge configuration. No
SNPX connection occurs while loading policy, listing robots or capabilities.
Omit `snpx_read` to disable all SNPX access. Configure at least one I/O target or
word range; each list is limited to 256 entries. Word ranges must not overlap.

`sample_provider` defaults to `"auto"`: existing native FRRobot sampling when
enabled, otherwise controller-generated files. Explicit `"snpx"` selects the
SNPX I/O sampler, even if the native provider is also enabled. There is no
automatic provider fallback. SNPX policy, port and sampling-provider changes
require reconnecting the host; `reload_connections` only permits the existing
endpoint host/timeout changes while preserving this policy. Installing this
host capability requires a host update/reconnect once; `reload_extensions`
cannot load a new controller transport into an older host.

## I/O samples

Use existing `sample_robot` and watch/recording tools with exact allowed targets:

```json
{"robot_id": "robot1", "expressions": ["DI[1]", "GI[1]", "AI[1]"]}
```

The digital sampler supports `DI`, `DO`, `RI`, `RO`, `UI` and `UO`, returning
booleans. `GI`, `GO`, `AI` and `AO` return one raw 16-bit word using the exact
decoding format configured for that target. Other expression types, unlisted
targets and duplicate targets are refused before connecting. The existing
two-second minimum sample interval and
job limits apply. Reads are sequential, not an atomic controller snapshot.
After a transport/protocol failure, remaining values are null with errors; valid
earlier values remain. Missing values never become zero/false automatically.

The fixed protocol ports use zero-based wire indices: DI/DO at offset 0, RI/RO
at 5000 and UI/UO at 6000. Public targets remain one-based. Address-space bounds
are DI/DO 1–5000 and RI/RO/UI/UO 1–1000; these are protocol bounds, not a claim
that every controller implements every index. Boolean reads transfer the
containing byte; retained wire evidence also includes neighboring bits.

Group/analog word ports use segment `0x0c` for GI/AI and `0x0a` for GO/AO.
GI/GO start at wire offset 0 and AI/AO at offset 1000; public indices are
one-based. This implementation permits indices 1–1000 for each word port. These
bounds do not establish installed channel counts or controller configuration.

Every allowed GI/GO/AI/AO target must also appear in `io_word_formats`. Choose
`"u16"` for unsigned 0–65535 or `"i16"` for signed -32768–32767 decoding. The
same bytes `ffff` produce 65535 or -1 respectively. There is no implicit format
or signedness inference, and format entries for digital/unlisted targets are
refused. The pinned reference client uses signed words; this adapter exposes the
raw word with an explicit operator-selected interpretation.

These are raw counts, with no conversion to volts, current or other engineering
units. Wider group values and controller-configured scaling are not inferred.
Verify the channel's width, signedness, scaling and port behavior on the target
controller. A compatible protocol response does not verify those settings.
Samples include per-expression `value_formats` (`bool`, `u16` or `i16`) and
`scaling_applied: false`; retained policy/evidence records preserve the selection.
Watches and recordings keep their source sample artifact IDs for that evidence.

Flags, typed numeric/position registers and motion are not implemented by this
provider. Use the existing file/native providers for their
supported operations.

## Raw assigned words

Discover `core.snpx.read_words` with `list_operations`/`describe_operation`, then
invoke it through `run_read_operation` using its returned revision:

```json
{
  "operation_id": "core.snpx.read_words",
  "revision": "core.snpx@1",
  "arguments": {"robot_id": "robot1", "address": 1, "count": 4}
}
```

Addresses refer to **one-based SNPX `%R` words**, each 16 bits. They are not
FANUC numeric `R[]` or position `PR[]` identifiers. A read may contain 1–128 words
inside a single allowed span and addresses 1–65535. Results include exact
little-endian bytes (`raw_hex`), unsigned `words_u16`, signed `words_i16`, the
configured endpoint and `mapping_verified: false`. A failed read returns null
data and errors with preserved evidence; it does not retry or use FTP.

The operator owns existing controller assignments. This reader does not inspect,
clear, create, repair or verify the assignment table. It cannot tell whether an
unassigned or changed word has a different meaning, so it never labels words as
R/PR values or decodes them as floats/positions. Verify mapping ownership and
meaning separately before interpreting data. Zero words are not proof of a valid
assignment. Automatic typed mapping and reviewed mapping setup remain future work.

## Protocol and evidence

Each capture opens one TCP connection, sends INIT and the `0x4f/0x01` session
handshake, then only `ReadSysMemory` (`0x04`) requests. The session handshake uses
the protocol's `Writing` header flag but contains no application memory data.
Its behavior and effect on other clients still require controller acceptance.
There is no memory-write service, arbitrary command route, `CLRASG`, `SETASG`,
controller program installation or I/O forcing. Enabling `allow_writes` adds no
SNPX write capability; reads also work with `allow_writes: false`.

Frames have bounded lengths and validated reply type, sequence, status and
packet count. Extended reads also validate service/segment and exact payload
size. Unsupported/fragmented protocol variants fail closed. Socket operations
share one capture deadline, rather than resetting a full timeout per response;
operating-system hostname resolution can occur outside that socket deadline.
Every capture closes its connection. There is no persistent worker or retry.

Captures retain `wire.json` with exact request/reply hex (including partial
replies on transport errors) and `sample.json` with results, endpoint and policy.
The artifact manifest hashes both files; results include the wire SHA-256 and
artifact ID. Audit records identify the read and any unavailable data. Keep
these controller-specific captures in ignored/private state directories.

The implementation adapts the pinned Apache-2.0 `fanuc_ucl` wire format, with its
copyright and license retained in [third-party notices](../fanuc_workbench/licenses/THIRD_PARTY_NOTICES.md).
It does not call the upstream high-level connection method, which clears
assignments. No upstream binaries or controller captures are redistributed.
