from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


def decimal_value(value: str | float | Decimal) -> Decimal:
    """Convert via string to avoid binary-float arithmetic leakage."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal value: {value!r}") from exc


def calculate_amount(quantity: str | float | Decimal,
                     unit_price: str | float | Decimal) -> Decimal:
    return (decimal_value(quantity) * decimal_value(unit_price)).quantize(
        CENT, rounding=ROUND_HALF_UP
    )


def amount_difference(declared: str | float | Decimal,
                      calculated: str | float | Decimal) -> Decimal:
    return (decimal_value(declared) - decimal_value(calculated)).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
