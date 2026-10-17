# Network resource field reference

The optional Bash/C modules use `remora-network-resources/v1`. All default to off.
See [snapshot contract, selection and tests](network-resources.md). Field allowlists are fixed per module.
Interface/port selection is supported; individual field filtering belongs to consumers.
Definitions come from [the C allowlists](../src/C_data_collectors_src/remora_netresources.h).

This reference describes the producer schema, not runtime support on any machine.
A readable field can be inapplicable to the current link layer or retain an unknown sentinel.
Missing values are null plus status, never zero. Numeric counters are unsigned64 decimal strings.
Counters are absolute snapshots; metadata is never differenced. Device metadata and driver software
counter domains can be shared across ports. Do not add overlapping port/netdev/error totals.

`events` is the producer event label; some Linux netdev error members specifically count bad packets.
`native_count` retains driver semantics. `native_ticks` has no validated time conversion.
`DWORD` is four bytes (`byte_scale=4`); other fields use `byte_scale=0` meaning not applicable.
`DmaWait` is a software native_count; older native_ticks labels must be interpreted by producer revision.
`native_text` preserves driver text: port rate is nominal Gb/sec, PCIe link speed is GT/s,
PCIe width is lanes, NUMA is an index (-1 unknown), CPU locality is a list, identifiers/masks are not rates.
netdev speed is Mbit/s (-1 unknown). Driver/device paths retain symlink targets.

## netinfo

| Field | Kind | Unit | Scope |
| --- | --- | --- | --- |
| `ifindex` | metadata | index | netdev/current_network_namespace |
| `iflink` | metadata | index | netdev/current_network_namespace |
| `type` | metadata | ARPHRD | netdev/current_network_namespace |
| `flags` | metadata | bitmask | netdev/current_network_namespace |
| `operstate` | metadata | text | netdev/current_network_namespace |
| `carrier` | metadata | boolean | netdev/current_network_namespace |
| `mtu` | metadata | bytes | netdev/current_network_namespace |
| `speed` | metadata | Mbit/s | netdev/current_network_namespace |
| `duplex` | metadata | text | netdev/current_network_namespace |
| `address` | metadata | text | netdev/current_network_namespace |
| `addr_assign_type` | metadata | enum | netdev/current_network_namespace |
| `state` | metadata | native_text | RDMA_port |
| `phys_state` | metadata | native_text | RDMA_port |
| `link_layer` | metadata | native_text | RDMA_port |
| `rate` | metadata | native_text | RDMA_port |
| `lid` | metadata | native_text | RDMA_port |
| `sm_lid` | metadata | native_text | RDMA_port |
| `cap_mask` | metadata | native_text | RDMA_port |
| `active_width` | metadata | native_text | RDMA_port |
| `active_speed` | metadata | native_text | RDMA_port |
| `node_guid` | metadata | native_text | RDMA_device (repeated on each exposed port) |
| `sys_image_guid` | metadata | native_text | RDMA_device (repeated on each exposed port) |
| `fw_ver` | metadata | native_text | RDMA_device (repeated on each exposed port) |
| `node_type` | metadata | native_text | RDMA_device (repeated on each exposed port) |
| `hca_type` | metadata | native_text | RDMA_device (repeated on each exposed port) |
| `board_id` | metadata | native_text | RDMA_device (repeated on each exposed port) |
| `numa_node` | metadata | native_text | backing_device/PCI (repeated per associated netdev/port) |
| `local_cpulist` | metadata | native_text | backing_device/PCI (repeated per associated netdev/port) |
| `current_link_speed` | metadata | native_text | backing_device/PCI (repeated per associated netdev/port) |
| `current_link_width` | metadata | native_text | backing_device/PCI (repeated per associated netdev/port) |
| `vendor` | metadata | native_text | backing_device/PCI (repeated per associated netdev/port) |
| `device` | metadata | native_text | backing_device/PCI (repeated per associated netdev/port) |
| `driver` | metadata | native_text | backing_device/PCI (repeated per associated netdev/port) |
| `device_path` | metadata | symlink_target | backing_device/PCI (repeated per associated netdev/port) |
| `gid_netdev.*` | metadata | netdev_name | RDMA_port/GID_index → netdev association |
| `gid_netdev` | metadata | netdev_name | RDMA_port/GID_mapping_directory |

Sources: netdev/port/device metadata paths under `/sys/class`; GID mapping under port `gid_attrs/ndevs`.

## neterrors

| Field | Kind | Unit | Scope |
| --- | --- | --- | --- |
| `rx_errors` | counter | events | netdev/current_network_namespace |
| `tx_errors` | counter | events | netdev/current_network_namespace |
| `rx_dropped` | counter | packets | netdev/current_network_namespace |
| `tx_dropped` | counter | packets | netdev/current_network_namespace |
| `rx_length_errors` | counter | events | netdev/current_network_namespace |
| `rx_over_errors` | counter | events | netdev/current_network_namespace |
| `rx_crc_errors` | counter | events | netdev/current_network_namespace |
| `rx_frame_errors` | counter | events | netdev/current_network_namespace |
| `rx_fifo_errors` | counter | events | netdev/current_network_namespace |
| `rx_missed_errors` | counter | events | netdev/current_network_namespace |
| `tx_aborted_errors` | counter | events | netdev/current_network_namespace |
| `tx_carrier_errors` | counter | events | netdev/current_network_namespace |
| `tx_fifo_errors` | counter | events | netdev/current_network_namespace |
| `tx_heartbeat_errors` | counter | events | netdev/current_network_namespace |
| `tx_window_errors` | counter | events | netdev/current_network_namespace |
| `collisions` | counter | events | netdev/current_network_namespace |
| `rx_nohandler` | counter | packets | netdev/current_network_namespace |
| `rx_otherhost_dropped` | counter | packets | netdev/current_network_namespace |

Sources: `class/net/IF/statistics/FIELD` relative to `/sys`.
Associated entity metadata from netinfo is also emitted, except GID mappings.

## ibcounters

| Field | Kind | Unit | Scope |
| --- | --- | --- | --- |
| `symbol_error` | counter | events | exposed_RDMA_port |
| `link_downed` | counter | events | exposed_RDMA_port |
| `link_error_recovery` | counter | events | exposed_RDMA_port |
| `port_xmit_discards` | counter | packets | exposed_RDMA_port |
| `port_rcv_errors` | counter | events | exposed_RDMA_port |
| `port_rcv_constraint_errors` | counter | events | exposed_RDMA_port |
| `port_xmit_constraint_errors` | counter | events | exposed_RDMA_port |
| `local_link_integrity_errors` | counter | events | exposed_RDMA_port |
| `excessive_buffer_overrun_errors` | counter | events | exposed_RDMA_port |
| `VL15_dropped` | counter | packets | exposed_RDMA_port |
| `port_xmit_wait` | counter | native_ticks | exposed_RDMA_port |
| `unicast_xmit_packets` | counter | packets | exposed_RDMA_port |
| `unicast_rcv_packets` | counter | packets | exposed_RDMA_port |
| `multicast_xmit_packets` | counter | packets | exposed_RDMA_port |
| `multicast_rcv_packets` | counter | packets | exposed_RDMA_port |

Sources: `class/infiniband/DEV/ports/PORT/counters_ext/FIELD_64 → counters/FIELD` relative to `/sys`.
Associated entity metadata from netinfo is also emitted, except GID mappings.

## mlxcounters

| Field | Kind | Unit | Scope |
| --- | --- | --- | --- |
| `out_of_buffer` | counter | native_count | exposed_mlx_RDMA_port |
| `duplicate_request` | counter | native_count | exposed_mlx_RDMA_port |
| `local_ack_timeout_err` | counter | native_count | exposed_mlx_RDMA_port |
| `packet_seq_err` | counter | native_count | exposed_mlx_RDMA_port |
| `rnr_nak_retry_err` | counter | native_count | exposed_mlx_RDMA_port |
| `req_cqe_error` | counter | native_count | exposed_mlx_RDMA_port |
| `resp_cqe_error` | counter | native_count | exposed_mlx_RDMA_port |
| `req_cqe_flush_error` | counter | native_count | exposed_mlx_RDMA_port |
| `resp_cqe_flush_error` | counter | native_count | exposed_mlx_RDMA_port |
| `req_remote_access_errors` | counter | native_count | exposed_mlx_RDMA_port |
| `req_remote_invalid_request` | counter | native_count | exposed_mlx_RDMA_port |
| `resp_local_length_error` | counter | native_count | exposed_mlx_RDMA_port |
| `resp_remote_access_errors` | counter | native_count | exposed_mlx_RDMA_port |
| `rx_read_requests` | counter | requests | exposed_mlx_RDMA_port |
| `rx_write_requests` | counter | requests | exposed_mlx_RDMA_port |
| `rx_atomic_requests` | counter | requests | exposed_mlx_RDMA_port |
| `np_cnp_sent` | counter | packets | exposed_mlx_RDMA_port |
| `rp_cnp_handled` | counter | packets | exposed_mlx_RDMA_port |
| `rp_cnp_ignored` | counter | packets | exposed_mlx_RDMA_port |
| `np_ecn_marked_roce_packets` | counter | packets | exposed_mlx_RDMA_port |

Sources: `class/infiniband/DEV/ports/PORT/hw_counters/FIELD` relative to `/sys`.
Associated entity metadata from netinfo is also emitted, except GID mappings.

## opacounters

| Field | Kind | Unit | Scope |
| --- | --- | --- | --- |
| `TxPkt` | counter | packets | exposed_hfi1_RDMA_port |
| `RxPkt` | counter | packets | exposed_hfi1_RDMA_port |
| `TxWords` | counter | DWORD | exposed_hfi1_RDMA_port |
| `RxWords` | counter | DWORD | exposed_hfi1_RDMA_port |
| `TxWait` | counter | native_ticks | exposed_hfi1_RDMA_port |
| `DmaWait` | counter | native_count | exposed_hfi1_RDMA_port |
| `RcvCstrErr` | counter | native_count | exposed_hfi1_RDMA_port |
| `XmitCstrErr` | counter | native_count | exposed_hfi1_RDMA_port |
| `UnknownFrame` | counter | native_count | exposed_hfi1_RDMA_port |
| `PktDrop` | counter | native_count | exposed_hfi1_RDMA_port |
| `Unaligned` | counter | native_count | exposed_hfi1_RDMA_port |
| `SeqNak` | counter | native_count | exposed_hfi1_RDMA_port |
| `RnrNak` | counter | native_count | exposed_hfi1_RDMA_port |
| `OtherNak` | counter | native_count | exposed_hfi1_RDMA_port |
| `RcTimeOut` | counter | native_count | exposed_hfi1_RDMA_port |
| `RcResend` | counter | native_count | exposed_hfi1_RDMA_port |
| `RcSeqNak` | counter | native_count | exposed_hfi1_RDMA_port |

Sources: `class/infiniband/DEV/ports/PORT/hw_counters/FIELD` relative to `/sys`.
Associated entity metadata from netinfo is also emitted, except GID mappings.
