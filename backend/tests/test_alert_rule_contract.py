from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import AlertRuleWrite
from app.services.bootstrap import DEFAULT_ALERTS


@pytest.mark.parametrize("rule_type", [item[1] for item in DEFAULT_ALERTS])
def test_alert_rule_write_accepts_every_bootstrap_rule(rule_type: str) -> None:
    payload = AlertRuleWrite(
        name="Existing bootstrap rule",
        rule_type=rule_type,  # type: ignore[arg-type]
        severity="warning",
    )

    assert payload.rule_type == rule_type


def test_alert_rule_write_rejects_unknown_rule_type() -> None:
    with pytest.raises(ValidationError):
        AlertRuleWrite(
            name="Unknown rule",
            rule_type="not_a_real_rule",  # type: ignore[arg-type]
            severity="warning",
        )
