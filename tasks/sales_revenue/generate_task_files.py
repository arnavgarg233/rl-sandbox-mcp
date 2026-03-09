"""
Generate starting and oracle spreadsheet files for the sales revenue task.

Creates:
  - starting_files/sales_data.ods  (input the agent receives)
  - oracle/expected_revenue.ods    (correct answer for grading)
"""
from __future__ import annotations

import random
from pathlib import Path

from odf import table, text
from odf.opendocument import OpenDocumentSpreadsheet

TASK_DIR = Path(__file__).parent
NUM_ROWS = 50
SEED = 99

PRODUCTS = [
    ("Widget A", "Electronics"),
    ("Widget B", "Electronics"),
    ("Gadget X", "Accessories"),
    ("Gadget Y", "Accessories"),
    ("Tool Alpha", "Hardware"),
    ("Tool Beta", "Hardware"),
    ("Part 100", "Components"),
    ("Part 200", "Components"),
    ("Service Plan", "Services"),
    ("Extended Warranty", "Services"),
]


def _create_ods(
    path: Path,
    headers: list[str],
    rows: list[list[str | float | int | None]],
) -> None:
    doc = OpenDocumentSpreadsheet()
    sheet = table.Table(name="Sheet1")

    header_row = table.TableRow()
    for h in headers:
        cell = table.TableCell(valuetype="string")
        cell.addElement(text.P(text=h))
        header_row.addElement(cell)
    sheet.addElement(header_row)

    for row_data in rows:
        tr = table.TableRow()
        for val in row_data:
            if val is None:
                cell = table.TableCell()
                cell.addElement(text.P(text=""))
            elif isinstance(val, float):
                cell = table.TableCell(valuetype="float", value=str(val))
                cell.addElement(text.P(text=f"{val:.2f}"))
            elif isinstance(val, int):
                cell = table.TableCell(valuetype="float", value=str(val))
                cell.addElement(text.P(text=str(val)))
            else:
                cell = table.TableCell(valuetype="string")
                cell.addElement(text.P(text=str(val)))
            tr.addElement(cell)
        sheet.addElement(tr)

    doc.spreadsheet.addElement(sheet)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    print(f"Created: {path}")


def main() -> None:
    random.seed(SEED)

    starting_rows: list[list[str | float | int | None]] = []
    oracle_rows: list[list[str | float | int | None]] = []

    for _ in range(NUM_ROWS):
        product, category = random.choice(PRODUCTS)
        quantity = random.randint(1, 500)
        unit_price = round(random.uniform(5.0, 999.99), 2)
        revenue = round(quantity * unit_price, 2)

        starting_rows.append([product, category, quantity, unit_price])
        oracle_rows.append([product, category, quantity, unit_price, revenue])

    _create_ods(
        TASK_DIR / "starting_files" / "sales_data.ods",
        headers=["Product", "Category", "Quantity", "Unit_Price"],
        rows=starting_rows,
    )

    _create_ods(
        TASK_DIR / "oracle" / "expected_revenue.ods",
        headers=["Product", "Category", "Quantity", "Unit_Price", "Revenue"],
        rows=oracle_rows,
    )

    print(f"\nGenerated {NUM_ROWS} rows of sales data.")


if __name__ == "__main__":
    main()
