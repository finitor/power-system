# ECO-WORTHY Wi-Fi Battery Recon Notes

Date: 2026-05-29

## Devices

- Pack 1: `192.168.0.201`
- Pack 2: `192.168.0.202`

Observed from Raspberry Pi `blueberry.local` at `192.168.0.153`.

## Active TCP Findings

Targeted TCP scan from Mac with `nmap`:

- Hosts up: both packs
- Closed on both packs:
  - `21/tcp`
  - `22/tcp`
  - `23/tcp`
  - `53/tcp`
  - `80/tcp`
  - `81/tcp`
  - `443/tcp`
  - `502/tcp`
  - `1883/tcp`
  - `1884/tcp`
  - `5683/tcp`
  - `6668/tcp`
  - `6669/tcp`
  - `8080/tcp`
  - `8081/tcp`
  - `8883/tcp`
  - `8899/tcp`
  - `9999/tcp`

Top-1000 TCP scan from Mac:

- Pack 1: 976 closed, 24 filtered
- Pack 2: 929 closed, 71 filtered
- No open TCP services found.

Full TCP connect scan from Raspberry Pi using `scan_ecoworthy_tcp.py`:

- Pack 1: zero open TCP ports across `1-65535`
- Pack 2: zero open TCP ports across `1-65535`

## UDP Probe Findings

User-space UDP probe from Raspberry Pi using `probe_ecoworthy_udp.py` checked common UDP ports:

- `53`, `67`, `68`, `123`, `137`, `138`, `161`, `500`, `502`, `1900`, `5353`, `5683`, `6666`, `6667`, `6668`, `6669`, `8899`, `9999`, `10000`, `49152`
- No UDP responses from either pack.

This does not prove all UDP ports are closed; it only means these common service probes did not elicit responses.

## Reachability Notes

The Wi-Fi modules are reachable but sluggish/sleepy:

- Pack 1 ping from Pi initially succeeded with very high latency, around 983-2063 ms.
- Pack 2 initially ARPed as failed from Pi, then later responded to ping with around 91-1149 ms latency.
- Neighbor table showed MACs in the `aa:c2:37:08:fc:*` range when reachable:
  - Pack 1: `aa:c2:37:08:fc:21`
  - Pack 2: `aa:c2:37:08:fc:15`

## Packet Capture Status

Passive packet capture could not be run non-interactively:

- Mac `tcpdump` cannot open BPF without root.
- Pi `sudo tcpdump` requires an interactive password.

## Current Interpretation

The ECO-WORTHY Wi-Fi modules appear to be outbound-only cloud clients. They do not expose obvious local HTTP, MQTT, Modbus TCP, Tuya TCP, CoAP, SSH, telnet, or raw socket services on the LAN.

For local monitoring, RS485/CAN/RS232 or Bluetooth BMS polling is likely a better integration path than the Wi-Fi LAN path.
