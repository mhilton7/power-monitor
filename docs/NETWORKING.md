# Networking

`push` works across NAT because the sensor initiates HTTPS. `pull` is suitable for a trusted LAN or VPN. `hybrid` combines signed outbound presence with direct history retrieval. For remote sites, prefer outbound push or WireGuard/Tailscale-style private routing; never forward a public port to an ESP32.

Device UUID is permanent while IP and hostname are observations. DHCP reservations improve operations but are not identity. Heartbeats may propose an address; the server records its source and validates it against site policy. mDNS is optional and commonly does not traverse Docker networks or VLANs.

Suggested VLAN policy permits sensors to reach DHCP, approved DNS, NTP, and TCP 443 on the Power Monitor host. Deny sensor-to-user and sensor-to-sensor lateral traffic. For pull/hybrid, permit worker-to-sensor TCP on the configured fixed port. PostgreSQL remains only on the internal Compose network.

Allowed CIDRs/domains and ports are administrator-controlled. Loopback, link-local, metadata destinations, non-HTTP schemes, embedded credentials, and unapproved public addresses are rejected. DNS results are checked when connecting to limit rebinding. A signed heartbeat or valid `/api/v1/health` is authoritative; ping/TCP/DNS tests only explain connectivity.
