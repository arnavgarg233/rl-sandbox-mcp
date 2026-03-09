"""
Generate starting and oracle spreadsheet files for the banking reserve task.

Creates:
  - starting_files/cash_flows.ods  (input the agent receives)
  - oracle/expected_reserves.ods   (correct answer for grading)
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from odf import table, text
from odf.opendocument import OpenDocumentSpreadsheet

TASK_DIR = Path(__file__).parent
NUM_ROWS = 100
RATE_INTERVAL = 10
SEED = 42


def _create_ods(
    path: Path,
    headers: list[str],
    rows: list[list[str | float | None]],
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
                cell.addElement(text.P(text=f"{val:.4f}"))
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


def _interpolate_rate(
    period: int,
    known_rates: dict[int, float],
) -> float:
    """Linear interpolation between nearest known rate points."""
    periods = sorted(known_rates.keys())

    if period in known_rates:
        return known_rates[period]

    below = max((p for p in periods if p < period), default=None)
    above = min((p for p in periods if p > period), default=None)

    if below is None and above is not None:
        return known_rates[above]
    if above is None and below is not None:
        return known_rates[below]
    if below is None and above is None:
        return 0.0

    r_below = known_rates[below]
    r_above = known_rates[above]
    fraction = (period - below) / (above - below)
    return r_below + fraction * (r_above - r_below)


def main() -> None:
    random.seed(SEED)

    known_rates: dict[int, float] = {}
    for p in range(1, NUM_ROWS + 1):
        if p == 1 or p % RATE_INTERVAL == 0 or p == NUM_ROWS:
            known_rates[p] = round(random.uniform(0.02, 0.15), 4)

    starting_rows: list[list[str | float | None]] = []
    oracle_rows: list[list[str | float | None]] = []

    for period in range(1, NUM_ROWS + 1):
        cash_flow = round(random.uniform(10000, 500000), 2)
        rate_point: float | None = known_rates.get(period)

        starting_rows.append([
            period,
            cash_flow,
            rate_point,
        ])

        interp_rate = _interpolate_rate(period, known_rates)
        reserve = round(cash_flow * interp_rate, 2)
        oracle_rows.append([
            period,
            cash_flow,
            rate_point,
            reserve,
        ])

    _create_ods(
        TASK_DIR / "starting_files" / "cash_flows.ods",
        headers=["Period", "Cash_Flow_Amount", "Rate_Point"],
        rows=starting_rows,
    )

    _create_ods(
        TASK_DIR / "oracle" / "expected_reserves.ods",
        headers=["Period", "Cash_Flow_Amount", "Rate_Point", "Required_Reserve"],
        rows=oracle_rows,
    )

    print(f"\nGenerated {NUM_ROWS} rows with {len(known_rates)} known rate points.")
    print("Known rate periods:", sorted(known_rates.keys()))


if __name__ == "__main__":
    main()
