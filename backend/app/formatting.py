from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def _decimal(value: Decimal | str | int) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("value must be an exact finite decimal") from exc
    if not result.is_finite():
        raise ValueError("value must be an exact finite decimal")
    return result


def _trim(value: Decimal, *, minimum_places: int = 0) -> str:
    text = format(value, "f")
    if "." not in text:
        return f"{text}.{''.join('0' for _ in range(minimum_places))}" if minimum_places else text
    whole, fraction = text.split(".", 1)
    fraction = fraction.rstrip("0")
    if len(fraction) < minimum_places:
        fraction += "0" * (minimum_places - len(fraction))
    return f"{whole}.{fraction}" if fraction else whole


def _group(text: str) -> str:
    sign = ""
    if text.startswith("-"):
        sign, text = "-", text[1:]
    whole, separator, fraction = text.partition(".")
    grouped = f"{int(whole):,}"
    return f"{sign}{grouped}{separator}{fraction}"


def format_currency(
    value: Decimal | str | int, currency: str = "USD", *, symbol: bool = True
) -> str:
    amount = _decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    prefix = "$" if currency == "USD" and symbol else f"{currency} " if symbol else ""
    return f"{prefix}{_group(format(amount, '.2f'))}"


def format_energy_rate(
    value: Decimal | str | int,
    currency: str = "USD",
    *,
    derived: bool = False,
) -> str:
    places = 4 if derived else 5
    rounded = _decimal(value).quantize(
        Decimal(1).scaleb(-places),
        rounding=ROUND_HALF_UP,
    )
    text = format(rounded, ".4f") if derived else _trim(rounded, minimum_places=2)
    prefix = "$" if currency == "USD" else f"{currency} "
    return f"{prefix}{_group(text)}/kWh"


def format_energy(value: Decimal | str | int) -> str:
    rounded = _decimal(value).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return f"{_group(_trim(rounded))} kWh"


def format_percentage(value: Decimal | str | int) -> str:
    rounded = _decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{_group(_trim(rounded))}%"


def format_tier_range(
    lower_bound_kwh: Decimal | str | int,
    upper_bound_exclusive_kwh: Decimal | str | int | None,
) -> str:
    lower = _decimal(lower_bound_kwh)
    if upper_bound_exclusive_kwh is None:
        display_lower = lower
        if lower == lower.to_integral_value() and lower > 0:
            display_lower += Decimal("1")
        return f"{_group(_trim(display_lower))} kWh and above"
    upper = _decimal(upper_bound_exclusive_kwh)
    return f"{_group(_trim(lower))}\u2013{_group(_trim(upper))} kWh"


def format_billing_period(start: date | datetime, end: date | datetime) -> str:
    start_date = start.date() if isinstance(start, datetime) else start
    end_date = end.date() if isinstance(end, datetime) else end
    return (
        f"{start_date.strftime('%b')} {start_date.day}, {start_date.year} \u2013 "
        f"{end_date.strftime('%b')} {end_date.day}, {end_date.year}"
    )


def format_decimal_detail(value: Decimal | str | int) -> str:
    return format(_decimal(value), "f")
