# Standard Ethernet pause and FEC resources

Opt-in `netpause` and `netfec` use six Bash hooks and compiled C collectors,
`data_collect_MODULE NODE OUTDIR TMPDIR [--check]`. No Python, ethtool CLI,
libnl, root, daemon or network traffic runtime dependency. The only operations
are generic-netlink family discovery and ethtool GET; standard read-only ioctl
configuration fallback is used only for optional netlink API unavailability
(EOPNOTSUPP/ENOSYS/ENOENT/EINVAL), never denial/malformed/timeout. Every initial
failure, request/response and fallback structure is retained. No setters.

| Module / fields | Kind | Unit | Scope |
| --- | --- | --- | --- |
| netpause: autoneg, rx_pause, tx_pause | gauge | boolean | netdev_ethtool_configuration |
| netpause: tx_pause_frames, rx_pause_frames | counter | frames | physical_port_mac_aggregate / emac / pmac |
| netpause: stats_source | metadata | enum_index | physical_port_mac |
| netfec: auto_selection, configured_off/rs/baser/llrs | gauge | boolean | netdev_ethtool_configuration |
| netfec: active_mode_index | gauge | link_mode_index | netdev_ethtool_active_mode |
| netfec: active_mode | metadata | enum | netdev_ethtool_active_mode |
| netfec: corrected_blocks, uncorrectable_blocks | counter | blocks | physical_port_fec |
| netfec: corrected_bits | counter | bits | physical_port_fec |
| netfec: STAT_lane_count | metadata | entries | source_lane_or_PCS_entries |
| netfec: lane_N_STAT | counter | blocks / bits | physical_port_lane_or_PCS_instance |
| netfec: ioctl_configured_fec_mask, ioctl_active_fec_mask (fallback only) | metadata | legacy_fec_bitmask | netdev_ethtool_configuration |
| netfec: per_lane_link_state / STAT_lane_breakdown | availability | none | physical_port_lane_or_PCS_instance |

All numeric values are exact decimal strings; booleans normalize any nonzero
API value to "1" while the original bytes remain in source_snapshots. Configured
FEC bitsets use link-mode indexes49(off),50(RS),51(BaseR),74(LLRS). They are not
legacy ioctl FEC masks. Active mode is independent of configured mode; unknown
future indexes remain numeric with symbol UNKNOWN. Legacy FEC_NONE means no
FEC configuration support, distinct from FEC_OFF; do not label NONE as Off.
Netlink active indexes0..4095 are preserved, without inventing Off for unknown.
FEC auto-selection is not link autonegotiation. Configuration/auto are meaningful
when link autonegotiation is disabled; collection does not assert that condition.
Pause RX/TX/autoneg are reported GET settings, not proof of negotiated wire
behavior; global pause is distinct from PFC. FEC mode does not prove lane health.

FEC statistics entry0 is the port total, followed by lane/PCS-instance entries
numbered0..N-1. Never call entry0 lane0, sum total plus lanes, infer optical lane
identity or infer link-up/down from counters. Missing array, empty array and
aggregate-only arrays have distinct explicit availability reasons. Each statistic
can have its own breakdown. The standard FEC GET has no per-lane link-up/down
attribute: that requested feature is explicitly UNSUPPORTED, never fabricated.
Corrected bits is an API-defined non-standard statistic, distinct from blocks.
No BER, rates, deltas, reset repair, per-job attribution or counter summation.

Discovery/selector match the existing network contract: exact sorted
REMORA_ETH_INTERFACES,16interfaces maximum, UP/DOWN Ethernet included, IPoIB
retained but all Ethernet interpretations unsupported, other transports and
loopback excluded by default. Identity (ifindex/driver/type) checked before/after.
Schema remora-network-resources/v1 retains kernel/boot/network namespace/time,
raw binary requests/replies including kernel errno/extendedACK bytes and native
byte order. Bounds:64KiB/reply,1second/query,4096bitbitset,32lane/PCSentries,
160records/interface. Unknown attributes retained/ignored; recognized duplicates,
malformed framing/types, identity/sequence/peer changes, bounds and output failures
suppress interpreted fields, preserving raw and returning74.

stats_source and ioctl_* masks are diagnostic metadata, excluded from
readable_primary so they alone cannot make an unsupported module PASS.

Exits0 readable configuration/counter without hard error;69 all optional
unavailable;65 invalid arguments/selector;74 hard/partial errors;75 lock busy.
`--check` is discovery only. Owned single-link nofollow regular output, nonblocking
lock, pending/incomplete guards and fsync follow existing network modules.
Finalizers preserve raw; use a new TMPDIR after interrupted output. Fixture backend
REMORA_ETHTOOL_TEST exists only in development builds, absent from installed C.

Standard fixtures: tests/test_network_ethtool.py. Four-site acceptance:
tests/network_ethtool_live.py, three native/Bash repetitions per module/interface,
independent before/after ethtool mode/statistics, raw source decoding, explicit
CLI/API/attribute/transport SKIP, nonroot/CapEff0 and finalizer hashes. Development
Python tools are never installed. Actual availability belongs in the versioned
network resource catalog; no all-node, active traffic or overhead qualification.

Primary definitions: https://docs.kernel.org/networking/ethtool-netlink.html
(PAUSE_GET and FEC_GET), Linux UAPI ethtool.h/ethtool_netlink.h. No kernel or driver
implementation is copied into these collectors.
