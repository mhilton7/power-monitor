from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    NetworkPolicyRevision,
    SensorNetworkCidr,
    SensorNetworkPolicy,
    Site,
)
from app.problem import ProblemError

POLICY_MODES = {
    "allow_listed_private",
    "allow_all_private",
    "deny_all",
    "legacy_authenticated_any",
    "legacy_public_and_listed",
}
POLICY_DIRECTIONS = {"device_ingress", "server_pull"}
METADATA_NETWORKS = (
    ipaddress.ip_network("169.254.169.254/32"),
    ipaddress.ip_network("100.100.100.200/32"),
    ipaddress.ip_network("fd00:ec2::254/128"),
)
PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    address: str
    direction: str
    mode: str
    reason: str
    matching_rule: str | None = None


def effective_client_ip(
    direct_address: str,
    forwarded_for: str | None,
    trusted_proxy_cidrs: str,
) -> str:
    """Resolve the first untrusted hop from a trusted reverse-proxy chain."""
    direct = canonical_ip(direct_address)
    trusted = [
        ipaddress.ip_network(item.strip(), strict=False)
        for item in trusted_proxy_cidrs.split(",")
        if item.strip()
    ]
    direct_trusted = any(
        direct.version == network.version and direct in network for network in trusted
    )
    if forwarded_for and direct_trusted:
        forwarded = [canonical_ip(item) for item in forwarded_for.split(",") if item.strip()]
        for candidate in reversed(forwarded):
            candidate_trusted = any(
                candidate.version == network.version and candidate in network for network in trusted
            )
            if not candidate_trusted:
                return str(candidate)
        if forwarded:
            return str(forwarded[0])
    return str(direct)


def canonical_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError as exc:
        raise ProblemError(
            422, "Invalid sensor IP", "Enter a valid IP address", "invalid_ip_address"
        ) from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _never_allowed(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    if address.is_loopback:
        return "Loopback addresses are never sensor networks"
    if address.is_link_local:
        return "Link-local and cloud metadata addresses are not permitted"
    if address.is_multicast:
        return "Multicast addresses are not permitted"
    if address.is_unspecified:
        return "Unspecified addresses are not permitted"
    if any(
        address in network for network in METADATA_NETWORKS if address.version == network.version
    ):
        return "Cloud metadata addresses are not permitted"
    return None


def _private_network(network: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
    if isinstance(network, ipaddress.IPv4Network):
        return any(
            network.subnet_of(candidate)
            for candidate in PRIVATE_NETWORKS
            if isinstance(candidate, ipaddress.IPv4Network)
        )
    return any(
        network.subnet_of(candidate)
        for candidate in PRIVATE_NETWORKS
        if isinstance(candidate, ipaddress.IPv6Network)
    )


def private_sensor_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return _never_allowed(address) is None and any(
        address.version == network.version and address in network for network in PRIVATE_NETWORKS
    )


def canonical_private_network(value: str) -> str:
    try:
        network = ipaddress.ip_network(value.strip(), strict=False)
    except ValueError as exc:
        raise ProblemError(
            422, "Invalid CIDR", "Enter a valid IPv4 or IPv6 CIDR", "invalid_cidr"
        ) from exc
    rejection = _never_allowed(network.network_address)
    if rejection:
        raise ProblemError(422, "Invalid CIDR", rejection, "invalid_cidr_range")
    if network.is_multicast or not _private_network(network):
        raise ProblemError(
            422,
            "Private CIDR required",
            "Public, reserved, and multicast networks are blocked by default",
            "public_cidr_blocked",
        )
    if any(network.overlaps(item) for item in METADATA_NETWORKS if network.version == item.version):
        raise ProblemError(
            422, "Invalid CIDR", "Cloud metadata ranges are not permitted", "metadata_cidr_blocked"
        )
    return str(network)


async def ensure_site_policies(
    session: AsyncSession, site: Site
) -> tuple[SensorNetworkPolicy, SensorNetworkPolicy]:
    policies = list(
        await session.scalars(
            select(SensorNetworkPolicy).where(SensorNetworkPolicy.site_id == site.id)
        )
    )
    by_direction = {item.direction: item for item in policies}
    now = datetime.now(UTC)
    ingress = by_direction.get("device_ingress")
    if ingress is None:
        ingress = SensorNetworkPolicy(
            site_id=site.id,
            direction="device_ingress",
            mode="legacy_authenticated_any",
            revision=1,
            migration_notice_pending=True,
            migrated_from_legacy=True,
        )
        session.add(ingress)
        await session.flush()
        session.add(
            NetworkPolicyRevision(
                policy_id=ingress.id,
                revision=1,
                mode=ingress.mode,
                cidrs=[],
                changed_at=now,
                reason="Legacy signed ingress behavior preserved for administrator review.",
            )
        )
    pull = by_direction.get("server_pull")
    if pull is None:
        pull_mode = (
            "legacy_public_and_listed"
            if site.allow_public_polling
            else "allow_listed_private"
            if site.allowed_cidrs
            else "deny_all"
        )
        pull = SensorNetworkPolicy(
            site_id=site.id,
            direction="server_pull",
            mode=pull_mode,
            revision=1,
            migration_notice_pending=True,
            migrated_from_legacy=True,
        )
        session.add(pull)
        await session.flush()
        for value in site.allowed_cidrs:
            session.add(
                SensorNetworkCidr(
                    policy_id=pull.id,
                    network=canonical_private_network(value),
                    label="Migrated site CIDR",
                    enabled=True,
                )
            )
        session.add(
            NetworkPolicyRevision(
                policy_id=pull.id,
                revision=1,
                mode=pull.mode,
                cidrs=[
                    {"network": value, "label": "Migrated site CIDR", "enabled": True}
                    for value in site.allowed_cidrs
                ],
                changed_at=now,
                reason="Legacy server-pull behavior preserved for administrator review.",
            )
        )
    return ingress, pull


async def policy_cidrs(session: AsyncSession, policy_id: str) -> list[SensorNetworkCidr]:
    return list(
        await session.scalars(
            select(SensorNetworkCidr)
            .where(SensorNetworkCidr.policy_id == policy_id)
            .order_by(SensorNetworkCidr.network)
        )
    )


async def policy_for_site(session: AsyncSession, site: Site, direction: str) -> SensorNetworkPolicy:
    if direction not in POLICY_DIRECTIONS:
        raise ValueError("unknown sensor network direction")
    ingress, pull = await ensure_site_policies(session, site)
    return ingress if direction == "device_ingress" else pull


async def evaluate_policy(
    session: AsyncSession,
    policy: SensorNetworkPolicy,
    raw_address: str,
) -> PolicyDecision:
    address = canonical_ip(raw_address)
    normalized = str(address)
    # This mode is migration-only and deliberately preserves the exact old behavior:
    # any source could reach ingress, but still needed a valid token/signature.
    if policy.mode == "legacy_authenticated_any":
        return PolicyDecision(
            True,
            normalized,
            policy.direction,
            policy.mode,
            "Legacy signed-device ingress behavior is preserved pending review",
            "Signed authentication (legacy network-unrestricted)",
        )
    rejection = _never_allowed(address)
    if rejection:
        return PolicyDecision(False, normalized, policy.direction, policy.mode, rejection)
    if policy.mode == "deny_all":
        return PolicyDecision(
            False, normalized, policy.direction, policy.mode, "Device network access is locked down"
        )
    cidrs = await policy_cidrs(session, policy.id)
    for entry in cidrs:
        network = ipaddress.ip_network(entry.network)
        if entry.enabled and address.version == network.version and address in network:
            if (
                isinstance(network, ipaddress.IPv4Network)
                and network.prefixlen <= 30
                and address == network.broadcast_address
            ):
                return PolicyDecision(
                    False,
                    normalized,
                    policy.direction,
                    policy.mode,
                    "Broadcast addresses are not permitted",
                    f"{entry.label} ({entry.network})",
                )
            return PolicyDecision(
                True,
                normalized,
                policy.direction,
                policy.mode,
                "Address matched an enabled private-network rule",
                f"{entry.label} ({entry.network})",
            )
    if policy.mode == "allow_all_private" and any(
        address.version == private.version and address in private for private in PRIVATE_NETWORKS
    ):
        return PolicyDecision(
            True,
            normalized,
            policy.direction,
            policy.mode,
            "Address is in a private network",
            "All private networks",
        )
    if policy.mode == "legacy_public_and_listed" and address.is_global:
        return PolicyDecision(
            True,
            normalized,
            policy.direction,
            policy.mode,
            "Legacy public-polling opt-in is preserved pending review",
            "Legacy public polling",
        )
    return PolicyDecision(
        False,
        normalized,
        policy.direction,
        policy.mode,
        "Address does not match the effective sensor network policy",
    )


async def evaluate_site_address(
    session: AsyncSession, site: Site, direction: str, raw_address: str
) -> PolicyDecision:
    return await evaluate_policy(
        session, await policy_for_site(session, site, direction), raw_address
    )


def policy_summary(policy: SensorNetworkPolicy, cidr_count: int) -> str:
    if policy.mode == "allow_listed_private":
        return f"Listed private networks only · {cidr_count} CIDR{'s' if cidr_count != 1 else ''}"
    if policy.mode == "allow_all_private":
        return "All private sensor networks allowed"
    if policy.mode == "deny_all":
        return "Device network access denied"
    if policy.mode == "legacy_authenticated_any":
        return "Legacy signed ingress · review required"
    return "Legacy public pull opt-in · review required"


async def poll_policy_parameters(
    session: AsyncSession, site: Site
) -> tuple[list[str], bool, SensorNetworkPolicy]:
    policy = await policy_for_site(session, site, "server_pull")
    if policy.mode == "deny_all":
        return [], False, policy
    entries = await policy_cidrs(session, policy.id)
    cidrs = [item.network for item in entries if item.enabled]
    if policy.mode == "allow_all_private":
        cidrs.extend(["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"])
    return cidrs, policy.mode == "legacy_public_and_listed", policy
