# NIC buffers, queues and link health

`nicstats` and `linkhealth` are optional, default-off Bash modules backed by C
collectors. They use read-only Linux `SIOCETHTOOL` requests (`GDRVINFO`,
`GSTRINGS/ETH_SS_STATS`, `GSTATS`). They require neither Python, the ethtool CLI,
root, a service nor a network probe. Configure either module through the existing
installer (`modules.nicstats` / `modules.linkhealth` = `on`). These modules retain
JSONL raw data; processing/plot/finalize hooks preserve it and do not add HTML plots.

Default discovery covers all Ethernet(type1) and IPoIB(type32) interfaces,
including DOWN, excluding loopback. `REMORA_ETH_INTERFACES='eth0,ib0'` selects exact
names, sorted and checked for duplicates. Missing selected interfaces get ABSENT;
unknown driver families get UNSUPPORTED with their raw GET observations retained.
Support is per interface and field, not implied by discovery or another interface.
The supported identities are `mlx5_core`, `mlx5_core[ib_ipoib]`, `ixgbe`, `igb`,
`tg3`, `bnxt_en`. Plain `ib_ipoib` counters remain the responsibility of existing
network modules. The driver string and exact counter name must both match.

`nicstats` covers allocation/buffer pressure, queue stop/wake/drop and hardware
or priority discards. `linkhealth` covers global pause, priority pause/PFC, and
available FEC bits/blocks or PCS symbol errors. Names remain driver-native.
Missing fixed allowlist fields are ABSENT/null; dynamic queue/priority patterns
produce records only for actually returned canonical indices. The preserved full
schema lets a reader distinguish missing patterns from zero. A readable zero PFC
counter does not establish PFC enabled; a missing PFC counter does not establish
PFC disabled. On IPoIB(type32), Ethernet pause/storm, priority discard and physical
Ethernet discard/threshold interpretations are UNSUPPORTED with
`availability_reason=not_applicable_to_ipoib_transport`; a returned numeric raw
value is still preserved. FEC bits, PCS symbols and link-down events remain
driver-native physical observations, without an enabled-feature claim. FEC bits, blocks and symbol errors are distinct units. No BER,
per-job usage, rate, delta, cross-interface sum or hardware diagnosis is derived.
`native_count` retains an unverified hardware/software quantum explicitly.

The CLI is `data_collect_MODULE NODE OUTDIR TMPDIR [--check]`; output is
`TMPDIR/MODULE_NODE.jsonl`, schema `remora-network-resources/v1`, compatible with
the existing runtime validator. `--check` checks discovery only. Exit0 means at
least one selected counter readable,69 none optional/unsupported,65 invalid
arguments/selection,74 partial/denied/malformed/identity change/I/O,75 lock busy.
Optional missing fields do not turn readable peers into failures. Inspect every
record's status; UNSUPPORTED/ABSENT/DENIED are not zero.

Each counter record includes interface, metric, unit, scope, source, raw decimal
uint64 string, value string or null, errno and status. Entities include driver,
version, firmware, PCI/USB bus string, type and operstate. Frame context includes
kernel, boot ID, network namespace and wall/monotonic start/end times. Interface
ifindex appears in its source snapshot before and after sampling. Physical-port,
priority/CoS and vport/queue counters may overlap between interfaces, PFs/VFs,
IPoIB aliases or aggregates; never sum them blindly. Queue counters are not
queue occupancy gauges or packet-buffer capacity measurements.

`source_snapshots` retain the full returned driver-info structure as hex, both
fixed32-byte GSTRINGS arrays as hex, and GSTATS values as index-ordered uint64
strings. Every original name byte including padding is retained; driver-info
bytes use the running host's Linux UAPI/native byte order. This does not include
pointers or uninitialized user memory: request buffers are zero initialized.
Before/after driver info, stat count, names and ifindex must agree before any
interpretation. A change yields PARTIAL/errno ESTALE with returned raw data.
Sequential ioctl requests are not an atomic view and cannot exclude an ABA
change. Unsupported/failed responses are null, never invented zero arrays.

Limits are16interfaces,16384driver stats/interface,32768interpreted records,
64MiB/frame. Exceeding discovery/stat/record limits marks PARTIAL with a reason;
interfaces beyond the limit are not reported as complete coverage. A frame too
large to commit or failed allocation/write leaves the pending marker and fails74.
No implicit filtering or truncation converts this to a success. Samples use an
owned single-link regular file, nonblocking nofollow lock, pending marker and
fsync. Interrupted/incomplete output fails closed; recover in a new TMPDIR.
No global process-kill or filesystem cleanup is performed. The fixture backend
is compiled only with `REMORA_NIC_TEST`; installed collectors ignore fixture-root
and fault-injection environment variables.

The standard suite is `python3 -m unittest discover -s tests -p 'test_network*.py'`
(development only). `tests/network_nic_live.py` runs on allocated Linux compute,
compares three repetitions per module and Bash/native path against independently
bracketed `ethtool -S/-i`, validates source name/index/value mapping and units,
and checks finalizers leave raw intact. `runtime/check.sh --module MODULE` checks
both existing implementations and JSONL. Preserve all source/oracle outputs,
commands, statuses, binary/source hashes and repetitions. No formal overhead or
legacy full-wrapper qualification is implied by these tests.

Field definitions follow. Definitions are capability candidates; availability
must be established on the specific node/driver/kernel. Site observations belong
in a separately versioned evidence catalog.

References: [Linux statistics](https://docs.kernel.org/networking/statistics.html),
[mlx5 counters](https://docs.kernel.org/networking/device_drivers/ethernet/mellanox/mlx5/counters.html),
[Linux v6.12 UAPI](https://github.com/torvalds/linux/blob/v6.12/include/uapi/linux/ethtool.h),
and v6.12 `igb_ethtool.c`, `ixgbe_ethtool.c`, `tg3.c`, `bnxt_ethtool.c` for driver
names and scopes. Vendor kernels can differ; exact names and raw sources remain
available. No Linux driver implementation is copied into the collector.

| Driver | Module | Field / pattern | Unit | Scope |
| --- | --- | --- | --- | --- |
| mlx5 | nicstats | rx_out_of_buffer | events | driver_receive_queue_aggregate |
| mlx5 | nicstats | rx_buff_alloc_err | events | netdev_aggregate |
| mlx5 | nicstats | tx_queue_stopped | events | netdev_aggregate |
| mlx5 | nicstats | tx_queue_wake | events | netdev_aggregate |
| mlx5 | nicstats | tx_queue_dropped | packets | netdev_aggregate |
| mlx5 | nicstats | rx_oversize_pkts_buffer | packets | netdev_aggregate |
| mlx5 | nicstats | rx_oversize_pkts_sw_drop | packets | netdev_aggregate |
| mlx5 | nicstats | rx_discards_phy | packets | physical_port |
| mlx5 | nicstats | tx_discards_phy | packets | physical_port |
| mlx5 | nicstats | rx_buffer_passed_thres_phy | events | physical_port |
| mlx5 | linkhealth | rx_pause_ctrl_phy | frames | physical_port |
| mlx5 | linkhealth | tx_pause_ctrl_phy | frames | physical_port |
| mlx5 | linkhealth | rx_global_pause | frames | physical_port |
| mlx5 | linkhealth | tx_global_pause | frames | physical_port |
| mlx5 | linkhealth | rx_global_pause_duration | microseconds | physical_port |
| mlx5 | linkhealth | tx_global_pause_duration | microseconds | physical_port |
| mlx5 | linkhealth | rx_global_pause_transition | events | physical_port |
| mlx5 | linkhealth | tx_pause_storm_warning_events | events | physical_port |
| mlx5 | linkhealth | tx_pause_storm_error_events | events | physical_port |
| mlx5 | linkhealth | link_down_events_phy | events | physical_port |
| mlx5 | linkhealth | rx_corrected_bits_phy | bits | physical_port |
| mlx5 | linkhealth | rx_bits_phy | bits | physical_port |
| mlx5 | linkhealth | rx_pcs_symbol_err_phy | symbols | physical_port |
| mlx5 | linkhealth | rx_prio0_pause | frames | physical_port_priority |
| mlx5 | linkhealth | rx_prio0_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | tx_prio0_pause | frames | physical_port_priority |
| mlx5 | linkhealth | tx_prio0_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | rx_prio0_pause_transition | events | physical_port_priority |
| mlx5 | linkhealth | rx_prio1_pause | frames | physical_port_priority |
| mlx5 | linkhealth | rx_prio1_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | tx_prio1_pause | frames | physical_port_priority |
| mlx5 | linkhealth | tx_prio1_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | rx_prio1_pause_transition | events | physical_port_priority |
| mlx5 | linkhealth | rx_prio2_pause | frames | physical_port_priority |
| mlx5 | linkhealth | rx_prio2_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | tx_prio2_pause | frames | physical_port_priority |
| mlx5 | linkhealth | tx_prio2_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | rx_prio2_pause_transition | events | physical_port_priority |
| mlx5 | linkhealth | rx_prio3_pause | frames | physical_port_priority |
| mlx5 | linkhealth | rx_prio3_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | tx_prio3_pause | frames | physical_port_priority |
| mlx5 | linkhealth | tx_prio3_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | rx_prio3_pause_transition | events | physical_port_priority |
| mlx5 | linkhealth | rx_prio4_pause | frames | physical_port_priority |
| mlx5 | linkhealth | rx_prio4_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | tx_prio4_pause | frames | physical_port_priority |
| mlx5 | linkhealth | tx_prio4_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | rx_prio4_pause_transition | events | physical_port_priority |
| mlx5 | linkhealth | rx_prio5_pause | frames | physical_port_priority |
| mlx5 | linkhealth | rx_prio5_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | tx_prio5_pause | frames | physical_port_priority |
| mlx5 | linkhealth | tx_prio5_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | rx_prio5_pause_transition | events | physical_port_priority |
| mlx5 | linkhealth | rx_prio6_pause | frames | physical_port_priority |
| mlx5 | linkhealth | rx_prio6_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | tx_prio6_pause | frames | physical_port_priority |
| mlx5 | linkhealth | tx_prio6_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | rx_prio6_pause_transition | events | physical_port_priority |
| mlx5 | linkhealth | rx_prio7_pause | frames | physical_port_priority |
| mlx5 | linkhealth | rx_prio7_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | tx_prio7_pause | frames | physical_port_priority |
| mlx5 | linkhealth | tx_prio7_pause_duration | microseconds | physical_port_priority |
| mlx5 | linkhealth | rx_prio7_pause_transition | events | physical_port_priority |
| ixgbe | nicstats | rx_no_buffer_count | native_count | driver_hardware |
| ixgbe | nicstats | rx_dropped | packets | netdev_aggregate |
| ixgbe | nicstats | tx_dropped | packets | netdev_aggregate |
| ixgbe | linkhealth | rx_flow_control_xon | frames | physical_port |
| ixgbe | linkhealth | rx_flow_control_xoff | frames | physical_port |
| ixgbe | linkhealth | tx_flow_control_xon | frames | physical_port |
| ixgbe | linkhealth | tx_flow_control_xoff | frames | physical_port |
| igb | nicstats | rx_no_buffer_count | native_count | driver_hardware |
| igb | nicstats | rx_dropped | packets | netdev_aggregate |
| igb | nicstats | tx_dropped | packets | netdev_aggregate |
| igb | linkhealth | rx_flow_control_xon | frames | physical_port |
| igb | linkhealth | rx_flow_control_xoff | frames | physical_port |
| igb | linkhealth | tx_flow_control_xon | frames | physical_port |
| igb | linkhealth | tx_flow_control_xoff | frames | physical_port |
| tg3 | nicstats | rx_discards | packets | driver_hardware |
| tg3 | nicstats | tx_discards | packets | driver_hardware |
| tg3 | linkhealth | rx_xon_pause_rcvd | frames | physical_port |
| tg3 | linkhealth | rx_xoff_pause_rcvd | frames | physical_port |
| tg3 | linkhealth | tx_xon_sent | frames | physical_port |
| tg3 | linkhealth | tx_xoff_sent | frames | physical_port |
| tg3 | linkhealth | rx_xoff_entered | events | physical_port |
| bnxt_en | nicstats | rx_total_oom_discards | native_count | netdev_aggregate |
| bnxt_en | nicstats | rx_total_netpoll_discards | native_count | netdev_aggregate |
| bnxt_en | nicstats | rx_total_ring_discards | native_count | netdev_aggregate |
| bnxt_en | nicstats | tx_total_ring_discards | native_count | netdev_aggregate |
| bnxt_en | nicstats | rx_stat_discard | packets | physical_port |
| bnxt_en | nicstats | tx_stat_discard | packets | physical_port |
| bnxt_en | nicstats | rx_buffer_passed_threshold | events | physical_port |
| bnxt_en | linkhealth | rx_pause_frames | frames | physical_port |
| bnxt_en | linkhealth | tx_pause_frames | frames | physical_port |
| bnxt_en | linkhealth | continuous_pause_events | events | physical_port |
| bnxt_en | linkhealth | resume_pause_events | events | physical_port |
| bnxt_en | linkhealth | continuous_roce_pause_events | events | physical_port |
| bnxt_en | linkhealth | resume_roce_pause_events | events | physical_port |
| bnxt_en | linkhealth | rx_corrected_bits | bits | physical_port |
| bnxt_en | linkhealth | rx_fec_corrected_blocks | blocks | physical_port |
| bnxt_en | linkhealth | rx_fec_uncorrectable_blocks | blocks | physical_port |
| mlx5 | nicstats | rx[0..4095]_buff_alloc_err | events | netdev_receive_queue |
| mlx5 | nicstats | tx[0..4095]_dropped | packets | netdev_transmit_queue |
| mlx5 | nicstats | tx[0..4095]_stopped | events | netdev_transmit_queue |
| mlx5 | nicstats | tx[0..4095]_wake | events | netdev_transmit_queue |
| mlx5 | nicstats | rx_prio[0..7]_discards | packets | physical_port_priority |
| mlx5 | nicstats | rx_prio[0..7]_buf_discard | packets | physical_port_priority |
| mlx5 | nicstats | rx_prio[0..7]_cong_discard | packets | physical_port_priority |
| igb | nicstats | rx_queue_[0..4095]_drops | packets | netdev_receive_queue |
| ixgbe | linkhealth | rx_pb_[0..7]_pxon | frames | physical_port_packet_buffer |
| ixgbe | linkhealth | rx_pb_[0..7]_pxoff | frames | physical_port_packet_buffer |
| ixgbe | linkhealth | tx_pb_[0..7]_pxon | frames | physical_port_packet_buffer |
| ixgbe | linkhealth | tx_pb_[0..7]_pxoff | frames | physical_port_packet_buffer |
| bnxt_en | nicstats | rx_discard_bytes_cos[0..7] | bytes | physical_port_cos |
| bnxt_en | nicstats | rx_discard_packets_cos[0..7] | packets | physical_port_cos |
