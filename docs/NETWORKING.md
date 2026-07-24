# Networking

`push` works across NAT because the sensor initiates HTTPS. `pull` is suitable for a trusted LAN or VPN. `hybrid` combines signed outbound presence with direct history retrieval. For remote sites, prefer outbound push or WireGuard/Tailscale-style private routing; never forward a public port to an ESP32.

Device UUID is permanent while IP and hostname are observations. DHCP reservations improve operations but are not identity. Heartbeats may propose an address; the server records its source and validates it against site policy. mDNS is optional and commonly does not traverse Docker networks or VLANs.

Suggested VLAN policy permits sensors to reach DHCP, approved DNS, NTP, and TCP 443 on the Power Monitor host. Deny sensor-to-user and sensor-to-sensor lateral traffic. For pull/hybrid, permit worker-to-sensor TCP on the configured fixed port. PostgreSQL remains only on the internal Compose network.

Allowed CIDRs/domains and ports are administrator-controlled through the explicit per-site ingress
and server-pull policies. An empty legacy pull list is migrated to explicit deny-all; it never
means "all private." See [Sensor network policy](SENSOR_NETWORK_POLICY.md) for modes, canonical
CIDR validation, trusted-proxy handling, and the no-scan address test. DNS results are checked when
connecting to limit rebinding. A signed heartbeat or valid `/api/v1/health` is authoritative;
ping/TCP/DNS tests only explain connectivity.

The **Administration > Sites & Network** site detail shows both ingress and pull summaries and
links to the selected site's controls. Browser/administrator reachability is
not governed by these sensor policies. **Add current private network** uses
forwarded headers only from configured trusted proxies, proposes at most an
IPv4 `/24` or IPv6 ULA `/64`, and requires review before saving.
