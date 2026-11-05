from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")


def decimal_value(value: str | int | float | Decimal) -> Decimal:
    """Convert via string to avoid binary-float arithmetic leakage."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal value: {value!r}") from exc


def calculate_amount(quantity: str | int | float | Decimal,
                     unit_price: str | int | float | Decimal) -> Decimal:
    return (decimal_value(quantity) * decimal_value(unit_price)).quantize(
        CENT, rounding=ROUND_HALF_UP
    )


def amount_difference(declared: str | int | float | Decimal,
                      calculated: str | int | float | Decimal) -> Decimal:
    return (decimal_value(declared) - decimal_value(calculated)).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
