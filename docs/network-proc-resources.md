# TCP/UDP/TcpExt and host/per-CPU network resources

`netprotocol` and `softnet` are independently selectable, default-off Bash
modules calling compiled C collectors. No Python runtime, root, daemon, MAD,
setters, counter reset or persistent workers. Enable with installer options
`--module netprotocol=on --module softnet=on`. Both use the six existing module
hooks and preserve JSONL snapshots; dedicated HTML plots are not provided.

CLI: `data_collect_MODULE NODE OUTDIR TMPDIR [--check]`. `--check` is source
discovery only. The collector appends `TMPDIR/MODULE_NODE.jsonl` using
`remora-network-resources/v1`; see [shared snapshot contract](network-resources.md).
The additional `proc_root` and `source_snapshots` retain source identity, full
bounded source text, read status/errno and parse status/errno. Field `raw` is the
original numeric token, with decimal strings in `value`. Embedded NUL/non-ASCII
source bytes are escaped without normalization, including on malformed input.
Sources are relative to proc_root. Raw data is never rewritten by finalization.

## netprotocol

Reads `/proc/net/snmp` and `/proc/net/netstat` once each per sample. Header-name
mapping supports reordering and new fields; pair identity, width and duplicate
names are checked before mapping. A malformed pair invalidates the affected
file's fields, preserving other-source fields and all raw text. Missing fields
remain ABSENT/null, not zero. Unselected gauges (e.g. signed Tcp.MaxConn) are
not interpreted as counters. There is no protocol/field selector in this v1.

| Entity | Fields | Unit |
| --- | --- | --- |
| Tcp | InSegs, OutSegs, RetransSegs, InErrs, OutRsts | segments |
| Udp | InDatagrams, OutDatagrams, InErrors, RcvbufErrors, SndbufErrors | datagrams |
| TcpExt | ListenDrops, ListenOverflows, TCPBacklogDrop, TCPTimeouts | events |
| TcpExt | TCPSynRetrans | segments |

Scope: current `network_namespace`, kernel protocol stack, shared by users and
processes in that namespace. TCP/TcpExt accounting can include IPv6 TCP; Udp
here is the IPv4 UDP table. Separate Udp6/snmp6 collection is outside this v1.
Protocol segments/datagrams are not physical wire frames; offload and protocol
layer definitions matter. These counters do not capture all RDMA bypass traffic.
Aggregate errors and subcategories can overlap; never sum them as independent
traffic or derive a retransmission ratio without a defined denominator.

## softnet

Reads `/proc/net/softnet_stat`. Scope is **host/per-CPU softnet_data** even when
the file is visible through a network namespace; it is not isolated user/job
usage. Supports 13/15-column layouts with explicit CPU ID at column 13
(one-based). CPU ID is never inferred from row position. Unknown layouts emit
UNSUPPORTED records and preserve raw source. Mixed supported/unknown coverage,
duplicate CPU IDs or invalid unsigned32 hex cause PARTIAL/exit74.

| Field | Kind | Unit |
| --- | --- | --- |
| processed | counter | packets (kernel processing counts, not wire frames) |
| dropped | counter | packets |
| time_squeeze | counter | events; not time or elapsed seconds |
| cpu_id | metadata | index |
| layout_columns | metadata | columns |

Each supported row is entity `cpu.ID`; unidentified rows use `row.INDEX`.
Counters are unsigned32 hex in `raw`, exact decimal strings in `value`.
Wrap/reset/CPU hotplug remain visible as snapshots; consumers must preserve
counter width and CPU identity and treat invalid comparisons as gaps.
All visible CPU rows are collected; no per-CPU selector in this version.
Padding/other queue fields remain in source_snapshots, not emitted as counters.

## Failure, bounds and tests

Exit0: some readable primary counters, optional absent fields retained. Exit69:
none readable/unsupported backend. Exit65: invalid input. Exit74: malformed,
denied, I/O, partial coverage or bounds failure. Exit75: owned writer lock busy.
Source read and parse states are separate; a readable file can have an unsupported
layout. `REMORA_NETWORK_PROC_ROOT` is an absolute recorded fixture override,
not auto-forwarded to remote workers. No sysfs selector affects these modules.

Sources are bounded regular-file, nonblocking reads, <=1MiB each; <=1024tokens
per line, <=2048softnetrows, <=8192records/8MiB per sample. Limit failures retain
raw prefixes and explicit limitation. Output/lock/pending files must be owned,
single-link regular files, opened nofollow. Existing non-newline-terminated
output/pending marker fails closed. A write interrupted by a signal retains a
pending marker; use a new TMPDIR and preserve the failed evidence.

Run the [standard network tests](network-resources.md#standard-tests); new
fixtures cover header/CPU identity, uint64/uint32 limits, wrap/hotplug, source
malformation/permissions, filesystem safety, signals, shell/native parity and
actual Bash runtime validation. On allocated Linux compute, run
`python3 -B tests/network_proc_live.py --prefix PREFIX --output NEW_DIR` for
three native/Bash repetitions with independently parsed before/after sources,
coverage/units/raw checks and preservation. Python is a development-only oracle,
never installed or executed by the modules. Source bracket reset/wrap/hotplug
gaps are explicit and cannot count as successful numerical comparisons.

Primary semantic references: [Linux SNMP counters](https://docs.kernel.org/networking/snmp_counter.html),
[Linux v6.12 softnet layout](https://github.com/torvalds/linux/blob/v6.12/net/core/net-procfs.c),
[Linux IPv4 proc tables](https://github.com/torvalds/linux/blob/v6.12/net/ipv4/proc.c),
[IPv6 TCP accounting](https://github.com/torvalds/linux/blob/v6.12/net/ipv6/tcp_ipv6.c).
