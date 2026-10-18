# PFC live acceptance

Run on an authorized allocated Linux compute node, after a clean build/install:

```bash
python3 -B tests/network_nic_live.py --prefix PREFIX --output NEW_NIC_DIRECTORY
bash tests/network_pfc_live.sh PREFIX NEW_PFC_DIRECTORY
bash tests/network_pfc_probe_live.sh NEW_DCB_DIRECTORY
```

The first test covers default interface discovery, three native and three Bash
samples of both modules, driver-specific units/scopes, full raw API mapping,
independent ethtool brackets and finalizer preservation. The second adds exact
single-interface selectors for each observed ixgbe Ethernet interface, including
DOWN interfaces, and separately records read-only PFC/configuration queries.
These are development oracles; neither test nor its CLI dependencies are
installed in the collection runtime. Modules remain Bash hooks plus C collectors.

The third test builds `network_pfc_probe.c` with strict GCC and performs only
`RTM_GETDCB/DCB_CMD_IEEE_GET`. It retains the complete netlink reply as hex,
kernel errno/stage, site UAPI header, compiler/source/binary hashes and effective
privileges. Three repetitions per ixgbe interface compare the PFC enable mask,
eight priority flags and priority-to-TC configuration against before/after DCB
CLI observations, with interface-index stability checks. The native request and
indication arrays remain decimal uint64 strings indexed as returned by the
driver; they are not renamed to per-priority traffic counters. This is a
development verifier. The default-off production `netdcb` Bash/C resource
module now collects PFC enable and priority-to-TC configuration directly; see
[its contract](network-dcb-resources.md). The diagnostic remains a separate
independent test, and its frame arrays are not netdcb configuration fields.

A readable enable mask of zero proves the observed configuration has all eight
priorities disabled. A many-to-one priority-to-TC mapping cannot separate
individual priority traffic from TC or packet-buffer totals. Record disabled
PFC as the reason for skipping enabled-traffic behavior, while retaining readable
zero counters. Configuration GET support, configuration state, counter scope and
traffic behavior remain separate claims.

For each ixgbe interface and each Bash/native path, the supplementary test takes
three repetitions. It verifies the 32 `rx_pb_[0..7]_pxon/pxoff` and
`tx_pb_[0..7]_pxon/pxoff` counters plus four global XON/XOFF counters against
independent before/after ethtool observations. Values stay decimal uint64
strings; brackets compare their lengths and lexical order, without floating
point conversion. Each counter must retain frames, exact interface identity,
and either `physical_port_packet_buffer` or global `physical_port` scope.

Record the following claims independently:

| Claim | Evidence required | Otherwise |
| --- | --- | --- |
| Counter readable | Driver identity, raw schema/value and collector/oracle agreement | SKIP with driver/API/field reason |
| Packet-buffer index | Exact ixgbe field name, index 0–7 | Never rename to priority |
| PFC enabled per priority | Successful read-only DCB `prio-pfc` configuration observation | UNKNOWN; configuration verification SKIP |
| Priority to TC/buffer mapping | Successful configuration observation with explicit mapping semantics | UNKNOWN; per-priority attribution SKIP |
| PFC traffic behavior | Controlled traffic and enabled/mapping proof | NOT_RUN in this acceptance test |

`dcb pfc show`, `dcb ets show`, `dcb buffer show`, ethtool pause and FEC GET
queries retain commands, exit codes, stdout and stderr. Missing tools, denied
access, unsupported API, missing counter, disabled configuration and IPoIB
transport inapplicability are different results. Optional configuration queries
do not fail otherwise correct counter collection. Zero does not establish
enabled PFC or exercised traffic; absent counters do not establish disabled PFC.
Global pause and RoCE pause events do not establish per-priority PFC.

Preserve all repetitions, failures, environment, hashes and raw JSONL. The test
never enables PFC, modifies the link, resets counters or generates traffic. No
monitoring-overhead or full-wrapper qualification is implied.
