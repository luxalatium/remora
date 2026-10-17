# Network collector contract (v1)

The eth, ib and opa modules retain their module function names and collector CLI:
`data_collect_<module> NODE OUTDIR TMPDIR`. State and data stay in TMPDIR.
The first call establishes a baseline; subsequent calls emit interval counts,
not rates. Timestamps are Unix seconds at the midpoint of the two readings.
The existing plots and legacy filenames/column positions remain compatible:

| Module | File | Columns |
| --- | --- | --- |
| eth | network_eth_traffic-NODE.txt | time tx_packets rx_packets tx_bytes rx_bytes tx_err tx_dropped rx_frame_err rx_crc_err |
| ib | ib_packets-NODE.txt | time tx_packets rx_packets tx_bytes rx_bytes |
| opa | opa_packets-NODE.txt | time tx_packets rx_packets tx_bytes rx_bytes |

These columns now contain bytes as labelled. Historical unversioned files are
never rewritten or automatically rescaled. Interpret those with their original
producer revision; do not combine them with repaired output without migration.
Use a fresh TMPDIR for a new run/revision. New sidecars identify this contract.

## Selection

* eth defaults to all administratively UP Ethernet (ARPHRD_ETHER, type=1)
  netdevs. Loopback and IPoIB are excluded. This is a netdev sum, not unique wire
  traffic: stacked VLAN/bond/veth devices may overlap. Select physical netdevs
  explicitly when that distinction matters.
* ib defaults to all ACTIVE InfiniBand link-layer ports across all devices,
  including hfi1 for compatibility with installations where opa is disabled.
* opa selects only ACTIVE hfi1 ports; it never aliases a Mellanox device.
  ib and opa can describe the same hfi1 port and must not be added together.
* Set comma-separated exact names with REMORA_ETH_INTERFACES (e.g. eth0,eth1),
  REMORA_IB_PORTS or REMORA_OPA_PORTS (e.g. mlx5_0/1,mlx5_1/1 or hfi1_0/2).
  No globs, whitespace, traversal, duplicates or shell evaluation. Explicit
  Ethernet selection can intentionally include loopback or IPoIB. Explicit
  fabric selection still requires ACTIVE InfiniBand and the module's family.
  Missing/down explicit entities fail; they are never silently substituted.

Both the shell module and direct collector use the installed native reader.
REMORA_BINARIES=0 remains a supported module entry path, using the same reader
for exact unsigned arithmetic and identical selection. Network modules therefore
require their installed collector even in shell mode; copying only a shell
module without its binary is no longer supported and fails explicitly. The
installer already builds these readers whenever the module is selected.

## Units and sources

Counter reads are independent, not an atomic hardware snapshot. Each metric
selects a readable counters_ext/*_64, then counters/* path. Missing or unsupported
(ENOENT/ENODEV/ENOSYS/EOPNOTSUPP/EINVAL) permits fallback; malformed values,
permissions and other I/O errors fail instead of being masked.

Ethernet statistics are packets, bytes or events (scale 1). Standard InfiniBand
data counters count four-octet units (scale 4). hfi1 PMA data counters come from
DcXmitFlits/DcRcvFlits (eight-octet flits, scale 8), rather than assuming the
InfiniBand ABI's scale applies to OPA. On hfi1, per-metric hw_counters fallback
uses TxPkt/RxPkt (packets) and TxWords/RxWords (32-bit DWORDs, scale 4). This
supports CN5000 where the classic PMA reads return EINVAL, including an ACTIVE
port 2. Sources and scales are recorded separately; backend changes rebaseline.
No tick-to-time conversion is inferred for wait counters (outside this schema).

References: [Linux sysfs ABI](https://github.com/torvalds/linux/blob/master/Documentation/ABI/stable/sysfs-class-infiniband),
[Cornelis hfi1 counter definitions](https://github.com/cornelisnetworks/opa-hfi1/blob/bfb3da47c6f90d89015c7c7ce01c88a4e7581c7c/drivers/infiniband/hw/hfi1/chip.c),
[its PMA mapping](https://github.com/cornelisnetworks/opa-hfi1/blob/bfb3da47c6f90d89015c7c7ce01c88a4e7581c7c/drivers/infiniband/hw/hfi1/mad.c).
[Cornelis PM flit units](https://github.com/cornelisnetworks/opa-ff/blob/master/IbAccess/Common/Inc/stl_pm.h) explicitly define eight bytes per flit. Driver-specific semantics and node classes require runtime qualification.

## Raw evidence, state and failures

`network-MODULE-NODE.raw.tsv` preserves every attempted selected-counter read,
with sample ID, entity, metric, relative sysfs path, native unsigned value or
missing marker, errno, and byte multiplier and interface/HCA identity. `network-MODULE-NODE.events.tsv`
records baseline, OK interval, gap/reset/selection or backend change and errors.
Only complete comparable intervals are summed into the legacy file. A missing,
reset, saturated, overflowing or incomparable interval never becomes zero.
Real zero differences on complete intervals remain valid zeros.

Per-node/module state is versioned, validated, run-local and atomically replaced
under a nonblocking exclusive lock. A pending marker makes interrupted output
transactions fail closed on the next invocation; retain the evidence and start
with a new TMPDIR. There are no workers, global /dev/shm files, signals to other
processes, or recursive cleanup. Reinitialization is idempotent. Counter reset,
selection/source change or a read failure invalidates the interval and primes
again on recovery. Exit 69 means unsupported, 65 invalid input/state, 74 I/O or
incomplete transaction, 75 concurrent writer; other nonzero means failure.

REMORA_NETWORK_SYSFS_ROOT overrides /sys for deterministic development fixtures;
it is recorded in state identity and must not be changed within an interval.
Normal users need no root, UMAD device, daemon, or sysfs writes.

## Standard qualification

Run `python3 -B -m unittest discover -s tests -p test_network.py` locally and on
allocated Linux compute. Run `python3 tests/network_live.py --prefix PREFIX
--output NEW_DIRECTORY` on an allocated node for source-aware bracketed oracles,
explicit/default selections, native/module parity, unsupported handling and a
bounded loopback workload. Preserve commands, source/binary hashes, environment,
all raw repetitions and failures; never substitute a local fixture PASS for live
hardware qualification. See tests/README.md for the acceptance matrix.
