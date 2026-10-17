# Optional user-space network resources (v1)

These five Bash modules call installed C collectors. They require no Python,
root, UMAD access, daemon, external network command or sysfs writes. They are
independently selectable and default to **off**; existing eth/ib/opa files and
plots are unchanged. Enable desired modules with the installer, for example
`--module netinfo=on --module neterrors=on --module ibcounters=on`.
Select installed modules at run time through `REMORA_MODULES` as usual.

| Module | Scope and collected information |
| --- | --- |
| netinfo | All visible netdevs/RDMA ports, including DOWN: index/type/flags/state, carrier/MTU/speed/duplex/address, port rate/link layer/LID, GUID/firmware/board, PCI driver/path/link speed/width, NUMA/CPU locality, exposed GID-to-netdev associations |
| neterrors | Ethernet netdev errors, RX/TX drops, CRC/frame/length/overflow/FIFO/missed errors, carrier/aborted/window/heartbeat errors, collisions, nohandler/otherhost drops |
| ibcounters | InfiniBand port symbol/link/constraint/integrity/overrun errors, discards, VL15 drops, transmit wait, unicast/multicast packet counts |
| mlxcounters | mlx4/mlx5 exposed port hardware request/retry/timeout/CQE/access errors, RX read/write/atomic requests, CNP/ECN counters |
| opacounters | hfi1 port Tx/Rx packets/DWORDs, TxWait/DmaWait, constraints, unknown frames/drops/unaligned, NAK/retry/resend/timeout counters |

All fabric modules retain inactive ports and their state. IB requires an
InfiniBand link layer; mlx accepts Ethernet and InfiniBand. Readable RoCE
counters on IB, or counters on DOWN ports, do not imply applicable traffic.
Family detection uses the bound PCI driver; a missing driver link permits a
recorded device-name fallback. Device-level counters and unclassified gauges,
high-water marks or arbitrary numeric driver attributes are outside this v1
allowlist. No port scan or MAD command is run.

Exact comma-separated `REMORA_ETH_INTERFACES`, `REMORA_IB_PORTS`,
`REMORA_OPA_PORTS` selectors reuse the existing environment forwarding.
netinfo applies ETH and IB selectors independently. Fabric names are DEVICE/PORT;
port 2 is supported. An explicit missing/wrong-family entity fails without
substitution. neterrors defaults to all type=1 netdevs, including DOWN, with
loopback/IPoIB available through explicit selection. netinfo defaults to all
netdevs. Names/globs/traversal/duplicates/empty explicit lists are rejected.

## Snapshot data contract

Collector CLI: `data_collect_MODULE NODE OUTDIR TMPDIR [--check]`.
Bash init performs `--check` discovery without truncating output; collect appends
one JSON object to `TMPDIR/MODULE_NODE.jsonl`. Remaining Bash hooks preserve it.
The v1 modules provide structured data; a dedicated HTML plot is not provided.

Each `remora-network-resources/v1` sample includes module/node/kernel/sysfs root,
boot ID/network namespace, wall and monotonic start/end timestamps, discovered
entities with family evidence, and per-field records. Sources are relative to
`sysfs_root`. Boot/namespace are UNKNOWN if unavailable; scope remains the visible
node/network namespace or driver RDMA port, never attributed to a process/user.
Each record has entity/scope, metric/source, kind/unit/byte_scale, status/errno,
**raw text** and a trimmed value. Numeric values are unsigned decimal **strings**
so uint64 never passes through floating point. Raw trailing newlines are retained
and JSON escaped. Non-ASCII bytes use byte-wise \u00XX escapes, preserving
the original bytes without UTF-8 normalization. Reads are bounded, sequential
and non-atomic. Metadata OK means the source was read: kernel/driver sentinel
values such as NUMA/speed -1 retain their native unknown meaning. A port nominal
rate, including on DOWN ports, is separate from measured traffic throughput.

Counters are absolute native snapshots; this v1 emits no deltas, rates or sums.
Resets/wrap/saturation remain visible in raw values. Consumers may difference
only known cumulative fields across matching identity/source/context and valid
samples; never difference metadata or turn missing values into zero. Netdev
aggregates overlap their error subcategories and stacked interfaces; ib and opa
may overlap on hfi1. Do not add such values together. Endpoint/direction and
hardware/driver semantics remain distinct. The scope identifies the exposed
sysfs object: driver software counters may be shared across ports or a device.
Treat resets, wraps and saturated values as gaps when deriving differences.

Netdev and IB errors use packets/events; mlx/hfi driver error semantics use
`native_count` unless a packet/request meaning is established by the field.
OPA TxWords/RxWords use DWORD (byte_scale=4). TxWait, DmaWait and port_xmit_wait
remain **native_ticks**, with no inferred duration/congestion percentage.
Other byte_scale values are 0 (not applicable), not an implicit byte conversion.
IB extended counters are tried before classic counters; every failed attempt
is retained. Only absent/unsupported errno permits fallback; denial/parse/I/O
errors do not get hidden by a different source. Driver counters can be cached
by the kernel; no `lifespan` setting is changed.

Per-field states: OK, ABSENT, UNSUPPORTED, DENIED, MALFORMED, ERROR; unavailable
values are null with errno and readable malformed raw text where available.
Exit 0 means some primary data is readable; optional ABSENT/UNSUPPORTED fields
remain individually visible. Exit 69 means no applicable readable primary data;
65 invalid selection/input; 74 I/O/denial/malformed/partial output; 75 concurrent
writer. A PARTIAL snapshot preserves successful fields and error records.
`--check` validates discovery only and does not establish readable counter support.

Output/lock/transaction-marker files are owned, single-link regular files opened
without following symlinks. Reads are nonblocking and bounded; discovery caps
128 selected entities, 512 GID mappings per port, 8192 records per sample,
bounded directory enumeration and an 8 MiB output cap. A sample limitation
field explicitly explains enumeration/record/byte limit failures.
A pending marker or non-newline-terminated existing output fails closed; preserve
the run and use a new TMPDIR. No global temp state, workers, process signaling,
recursive cleanup or destructive finalizer. `REMORA_NETWORK_SYSFS_ROOT` is a
recorded development fixture override, not forwarded to remote worker shells.

## Standard tests

`python3 -B -m unittest discover -s tests -p 'test_network*.py'` runs the original
eth/ib/opa contract/environment tests and new snapshot fixtures. Python here is
an optional development harness; it is not installed or executed by modules.
The hard-link rejection fixture reports SKIP when the test filesystem itself
rejects hard-link creation; this never relaxes the collector single-link guard.
On allocated Linux compute, run `tests/network_resources_live.py --prefix PREFIX
--output NEW_DIRECTORY` for independently read source brackets, complete
availability evidence, explicit/default selection, shell/native paths and raw
preservation. `runtime/check.sh --module NAME` also checks each installed module
in isolation with Bash/jq. Unsupported families are SKIP, not zero or PASS.
Retain raw samples, source/binary hashes, all command outputs/exit codes, kernel,
allocation identity and source revision. Local fixtures do not qualify hardware.

Sources: [Linux netdev statistics](https://docs.kernel.org/networking/statistics.html),
[Linux InfiniBand sysfs ABI](https://github.com/torvalds/linux/blob/master/Documentation/ABI/stable/sysfs-class-infiniband),
[Cornelis hfi1 counter definitions](https://github.com/cornelisnetworks/opa-hfi1/blob/bfb3da47c6f90d89015c7c7ce01c88a4e7581c7c/drivers/infiniband/hw/hfi1/chip.c).
See also [legacy network contract](network-modules.md).
