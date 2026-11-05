from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import load_workbook


@dataclass(frozen=True)
class SettlementRow:
    sheet: str
    row_number: int
    item_code: str | None
    description: str
    unit: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    declared_amount: Decimal | None


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def read_settlement_xlsx(path: str | Path, sheet_name: str | None = None) -> list[SettlementRow]:
    """Read explicit template columns: A code, B description, C unit, D qty, E price, F amount."""
    workbook = load_workbook(filename=Path(path), read_only=True, data_only=True)
    try:
        sheet = workbook[sheet_name] if sheet_name else workbook.active
        rows: list[SettlementRow] = []
        for row_number, values in enumerate(sheet.iter_rows(min_row=2, max_col=6, values_only=True), 2):
            if not any(value is not None for value in values):
                continue
            code, description, unit, quantity, unit_price, amount = values
            rows.append(SettlementRow(
                sheet=sheet.title, row_number=row_number,
                item_code=str(code).strip() if code is not None else None,
                description=str(description or "").strip(),
                unit=str(unit).strip() if unit is not None else None,
                quantity=_to_decimal(quantity), unit_price=_to_decimal(unit_price),
                declared_amount=_to_decimal(amount),
            ))
        return rows
    finally:
        workbook.close()
