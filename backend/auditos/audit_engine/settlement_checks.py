from collections import defaultdict
from decimal import Decimal

from auditos.audit_engine.findings import AuditFinding
from auditos.audit_engine.money import amount_difference, calculate_amount
from auditos.ingestion.settlement_xlsx import SettlementRow


def review_rows(rows: list[SettlementRow], tolerance: Decimal = Decimal("0.02")) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    codes: dict[str, list[SettlementRow]] = defaultdict(list)
    for row in rows:
        ref = f"{row.sheet}!{row.row_number}"
        if row.item_code:
            codes[row.item_code].append(row)
        if row.quantity is None or row.unit_price is None or row.declared_amount is None:
            findings.append(AuditFinding(
                code="INCOMPLETE_AMOUNT_FIELDS", category="DATA_QUALITY",
                title="结算金额字段不完整",
                description="数量、综合单价或申报合价存在空值/非数值，请核对源表。",
                source_ref=ref, rule_id="SETTLEMENT.REQUIRED_AMOUNT_FIELDS", rule_version="0.1.0",
            ))
            continue
        calculated = calculate_amount(row.quantity, row.unit_price)
        difference = amount_difference(row.declared_amount, calculated)
        if abs(difference) > tolerance:
            findings.append(AuditFinding(
                code="AMOUNT_MISMATCH", category="DATA_QUALITY",
                title="申报合价与数量×单价不一致",
                description=f"按导入值计算为 {calculated}，源表申报为 {row.declared_amount}，差额为 {difference}。算术差异不等同于审减金额。",
                source_ref=ref, rule_id="SETTLEMENT.AMOUNT_RECALCULATION", rule_version="0.1.0",
                declared_amount=row.declared_amount, calculated_amount=calculated,
                difference_amount=difference,
            ))
        if row.quantity < 0:
            findings.append(AuditFinding(
                code="NEGATIVE_QUANTITY", category="DATA_QUALITY", title="工程量为负数",
                description="负工程量可能是冲销或特殊计量表达，需结合合同和计算底稿人工核实。",
                source_ref=ref, rule_id="SETTLEMENT.NEGATIVE_QUANTITY_REVIEW", rule_version="0.1.0",
            ))
    for code, duplicated in codes.items():
        if len(duplicated) > 1:
            refs = ", ".join(f"{item.sheet}!{item.row_number}" for item in duplicated)
            findings.append(AuditFinding(
                code="DUPLICATE_ITEM_CODE", category="PROFESSIONAL_REVIEW",
                title="清单编码重复出现",
                description=f"编码 {code} 出现 {len(duplicated)} 次（{refs}）。编码重复可能合理，不得仅凭此规则判定重复计价。",
                source_ref=refs, rule_id="SETTLEMENT.DUPLICATE_CODE_SIGNAL", rule_version="0.1.0",
            ))
    return findings
