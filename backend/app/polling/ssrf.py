from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class AddressRejected(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


def _is_allowed_ip(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    allowed_cidrs: list[str],
    allow_public: bool,
) -> bool:
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
    ):
        return False
    if address.is_reserved:
        return False
    networks = [ipaddress.ip_network(item, strict=False) for item in allowed_cidrs]
    if any(address in network for network in networks):
        return True
    return allow_public and address.is_global


async def validate_poll_target(
    *,
    host: str,
    port: int,
    scheme: str,
    allowed_cidrs: list[str],
    allowed_domains: list[str],
    allowed_ports: tuple[int, ...] = (80, 443, 8080, 8443),
    allow_public: bool = False,
) -> tuple[str, ...]:
    if scheme not in {"http", "https"}:
        raise AddressRejected("only HTTP and HTTPS targets are supported")
    if port not in allowed_ports:
        raise AddressRejected("target port is not permitted")
    normalized_host = host.rstrip(".").lower()
    try:
        literal = ipaddress.ip_address(normalized_host.strip("[]"))
    except ValueError:
        if not any(
            normalized_host == domain.lower() or normalized_host.endswith(f".{domain.lower()}")
            for domain in allowed_domains
        ):
            raise AddressRejected("hostname is outside the site's permitted domains") from None
        loop = asyncio.get_running_loop()
        try:
            records = await loop.getaddrinfo(
                normalized_host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
            )
        except socket.gaierror as exc:
            raise AddressRejected("hostname could not be resolved") from exc
        addresses = tuple(sorted({record[4][0] for record in records}))
    else:
        addresses = (str(literal),)
    if not addresses:
        raise AddressRejected("target resolved to no addresses")
    for raw_address in addresses:
        address = ipaddress.ip_address(raw_address)
        if not _is_allowed_ip(address, allowed_cidrs, allow_public):
            raise AddressRejected(f"resolved address {address} is not permitted")
    return addresses
