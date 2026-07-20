from __future__ import annotations

import pytest

from app.polling.ssrf import AddressRejected, validate_poll_target
from app.security.browser import hash_password, password_is_strong, verify_password
from app.topology import AggregateItem, overlap_warnings, would_create_cycle


def test_password_argon2_and_strength() -> None:
    password = "Correct-Horse-Battery-99"
    assert password_is_strong(password)
    encoded = hash_password(password)
    assert encoded.startswith("$argon2id$")
    assert verify_password(encoded, password)
    assert not verify_password(encoded, "wrong")


def test_cycle_and_double_count_warnings() -> None:
    parents = {"main": None, "branch": "main", "sub": "branch", "leg1": None, "leg2": None}
    assert would_create_cycle("main", "sub", parents)
    warnings = overlap_warnings(
        [
            AggregateItem("main", "main"),
            AggregateItem("branch", "branch"),
            AggregateItem("leg1", "service-leg", "service"),
        ],
        parents,
    )
    assert any("overlaps" in warning for warning in warnings)
    assert any("complete two-leg" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_ssrf_rejects_loopback_and_allows_site_private_cidr() -> None:
    with pytest.raises(AddressRejected):
        await validate_poll_target(
            host="127.0.0.1",
            port=443,
            scheme="https",
            allowed_cidrs=["127.0.0.0/8"],
            allowed_domains=[],
        )
    result = await validate_poll_target(
        host="192.168.20.10",
        port=443,
        scheme="https",
        allowed_cidrs=["192.168.20.0/24"],
        allowed_domains=[],
    )
    assert result == ("192.168.20.10",)
    with pytest.raises(AddressRejected):
        await validate_poll_target(
            host="169.254.169.254",
            port=80,
            scheme="http",
            allowed_cidrs=["0.0.0.0/0"],
            allowed_domains=[],
        )
