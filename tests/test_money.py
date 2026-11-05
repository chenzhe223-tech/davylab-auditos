from decimal import Decimal
from auditos.audit_engine.money import amount_difference, calculate_amount


def test_calculate_amount_rounds_half_up():
    assert calculate_amount("2.5", "3.333") == Decimal("8.33")


def test_amount_difference():
    assert amount_difference("100.00", "99.98") == Decimal("0.02")
