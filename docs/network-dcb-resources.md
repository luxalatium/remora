# netdcb: read-only DCB configuration

Default off. A Bash six-hook resource module invokes the C collector through
`data_collect_netdcb NODE OUTDIR TMPDIR [--check]`. No Python, ethtool or dcb CLI
runtime dependency. Linux RTM_GETDCB / DCB_CMD_IEEE_GET only; no SET operations.
It uses remora-network-resources/v1 JSONL and the existing network selector
`REMORA_ETH_INTERFACES` (exact comma list, sorted, no duplicates; maximum 16).
Discovery includes DOWN Ethernet and IPoIB interfaces, excludes loopback and
other transports. Explicit non-Ethernet selections remain UNSUPPORTED with
`not_applicable_to_transport`; missing interfaces remain ABSENT.

| Field | Kind | Unit | Scope |
| --- | --- | --- | --- |
| pfc_cap | metadata | traffic_classes | netdev_dcb_configuration |
| pfc_enable_mask | gauge | bitmask | netdev_dcb_configuration |
| priority_N_pfc_enabled (N=0..7) | gauge | boolean | netdev_dcb_priority_configuration |
| ets_cap | metadata | traffic_classes | netdev_dcb_configuration |
| priority_N_to_tc (N=0..7) | gauge | index | netdev_dcb_priority_configuration |

Numeric values are exact decimal strings; boolean values are "0"/"1".
PFC cap is the reported number of traffic classes that can simultaneously
support PFC, not a priority count. ETS cap is retained exactly as the driver reports it, including zero; the
collector does not reinterpret zero as a class count. Mapping indexes must be 0..7; they are not
validated against reported cap because a driver may report inactive mappings.
Priority numbers refer to 802.1p priorities. This is current network namespace
netdev configuration, not a job resource or a traffic counter. Multiple priorities
may map to one TC. Mapping alone cannot assign PB/TC/frame counters to individual
priorities. Disabled PFC is valid readable configuration, not missing support.

Each sample retains complete bounded request/response hex, actual byte count,
kernel peer PID, sequence, separate PFC/ETS status, interface indexes before/after,
transport, driver symlink identity before/after, boot, namespace and time bounds.
Missing PFC/ETS attributes produce ABSENT / `response_attribute_not_exposed` for
that group only. Kernel EOPNOTSUPP is UNSUPPORTED / `kernel_get_not_supported`;
EPERM/EACCES is DENIED. No zero is fabricated for unavailable attributes.
Malformed framing, duplicate recognized attributes, wrong interface/sequence/peer,
short structures, illegal mapping/cap, truncation or identity change suppress all
interpreted values for that interface while retaining received bytes.
Unknown attributes and longer compatible structs are retained and ignored.

Exit 0: at least one configuration field readable and no hard errors; 69: all
optional unavailable; 74: denied, malformed, timeout, identity, bounds or I/O error;
65: invalid arguments/selector; 75: output lock contention. Missing one group with
another readable is valid partial availability (per-field status), not hard error.
`--check` performs interface discovery only, never appends/truncates samples or
asserts DCB support. Safe owned regular single-link outputs, nofollow locks,
pending markers and fsync follow other network collectors. Interrupted output
requires a new task TMPDIR; finalize retains all raw data. Per-interface receive
wait is bounded at 1 second (maximum 16 interfaces); no retries/privileged fallback.

Standard validation: tests/test_network_dcb.py adversarial wire/output/lifecycle
fixtures; tests/network_dcb_live.py independent dcb PFC/ETS before/after oracle,
three repetitions through C and Bash, explicit per-interface support and SKIP.
Development test drivers may use Python; installed collector/modules do not.

To pair configuration with existing linkhealth counters, enable both opt-in
modules in the installer configuration. Select exact interfaces only after
checking the current node's netinfo output, for example:

```bash
export REMORA_ETH_INTERFACES=eno1,eno2
"$REMORA_BIN/binary_data_collectors/data_collect_netdcb" "$HOSTNAME" "$OUTDIR" "$TMPDIR"
```

Re-read configuration when a sample matters; this module does not assert that
configuration stays unchanged between samples or that PFC is active on the link.
References: [iproute2 PFC](https://man7.org/linux/man-pages/man8/dcb-pfc.8.html),
[iproute2 ETS](https://man7.org/linux/man-pages/man8/dcb-ets.8.html), and the target
system's retained `/usr/include/linux/dcbnl.h` in qualification evidence.
