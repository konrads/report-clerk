from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Border, Font, PatternFill, Side

# Each report declares SHEETS = [(sheet name, template_data key, [columns])]; this turns them into one workbook.
Sheet = tuple[str, str, list[str]]

HEADER_FONT = Font(bold=True, color="120D07")
HEADER_FILL = PatternFill("solid", fgColor="EEF1EA")
HEADER_BORDER = Border(bottom=Side(style="thin", color="120D07"))
FIRST_ROW = 5


def _value(row: Any, column: str) -> Any:
    value = row.get(column) if isinstance(row, dict) else getattr(row, column)
    return float(value) if isinstance(value, Decimal) else value


def _text(cell) -> None:
    # XLSX strings must remain strings even when they start with =.
    if isinstance(cell.value, str):
        cell.data_type = "s"


def build(title: str, sheets: list[Sheet], template_data: dict, logo: Path) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, key, columns in sheets:
        ws = workbook.create_sheet(name[:31])
        image = Image(str(logo))
        image.width, image.height = image.width * 24 // image.height, 24
        ws.add_image(image, "A1")
        ws.row_dimensions[1].height = 22
        ws["A3"] = title
        _text(ws["A3"])
        ws["A3"].font = Font(bold=True, size=14)

        for col, column in enumerate(columns, start=1):
            cell = ws.cell(FIRST_ROW, col, column.replace("_", " ").capitalize())
            _text(cell)
            cell.font, cell.fill, cell.border = HEADER_FONT, HEADER_FILL, HEADER_BORDER
        for r, row in enumerate(template_data[key], start=FIRST_ROW + 1):
            for col, column in enumerate(columns, start=1):
                cell = ws.cell(r, col, _value(row, column))
                _text(cell)
                if isinstance(cell.value, float):
                    cell.number_format = "#,##0.00"
                elif isinstance(cell.value, date):
                    cell.number_format = "yyyy-mm-dd"

        for col, column in enumerate(columns, start=1):
            values = [str(ws.cell(r, col).value or "") for r in range(FIRST_ROW, ws.max_row + 1)]
            ws.column_dimensions[ws.cell(FIRST_ROW, col).column_letter].width = min(max(map(len, values)) + 2, 50)
        ws.freeze_panes = ws.cell(FIRST_ROW + 1, 1)

    out = BytesIO()
    workbook.save(out)
    return out.getvalue()
