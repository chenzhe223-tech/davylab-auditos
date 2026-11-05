from decimal import Decimal
from auditos.audit_engine.settlement_checks import review_rows
from auditos.ingestion.settlement_xlsx import SettlementRow


def make_row(number: int, amount: str, code: str = "0301") -> SettlementRow:
    return SettlementRow("结算明细", number, code, "电缆敷设", "m",
                         Decimal("10"), Decimal("5"), Decimal(amount))


def test_amount_mismatch_is_not_a_deduction():
    findings = review_rows([make_row(2, "60.00")])
    assert len(findings) == 1
    assert findings[0].code == "AMOUNT_MISMATCH"
    assert findings[0].difference_amount == Decimal("10.00")
    assert findings[0].requires_human_review


def test_duplicate_code_is_only_a_signal():
    findings = review_rows([make_row(2, "50.00"), make_row(3, "50.00")])
    duplicate = [item for item in findings if item.code == "DUPLICATE_ITEM_CODE"]
    assert len(duplicate) == 1
    assert "不得仅凭此规则判定重复计价" in duplicate[0].description
