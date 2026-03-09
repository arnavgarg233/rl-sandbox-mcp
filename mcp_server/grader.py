"""
Grading engine that compares agent output spreadsheets against oracle files.

Supports cell-by-cell numeric comparison with configurable tolerance,
string matching, and formula preservation checks.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from spreadsheet_engine import (
    OdsEngine,
    XlsxEngine,
    _index_to_col_letter,
    _resolve_path,
    get_engine,
)


class GradeStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"


class CellComparison(BaseModel):
    cell: str
    expected: str | int | float | bool | None
    actual: str | int | float | bool | None
    match: bool
    reason: str = ""


class GradingResult(BaseModel):
    status: GradeStatus
    score: float = Field(ge=0.0, le=1.0)
    total_cells: int
    matched_cells: int
    mismatches: list[CellComparison] = Field(default_factory=list)
    notes: str = ""


class GradingConfig(BaseModel):
    numeric_tolerance: float = 0.01
    check_formulas: bool = False
    ignore_empty_cells: bool = True
    sheets_to_check: list[str] | None = None
    pass_threshold: float = 1.0


def grade_spreadsheet(
    agent_output_path: str,
    oracle_path: str,
    config: GradingConfig | None = None,
) -> GradingResult:
    """Compare an agent's output spreadsheet against the oracle (expected) output."""
    if config is None:
        config = GradingConfig()

    agent_path = _resolve_path(agent_output_path)
    expected_path = _resolve_path(oracle_path)

    agent_engine = get_engine(agent_path)
    expected_engine = get_engine(expected_path)

    agent_info = agent_engine.list_sheets(agent_path)
    expected_info = expected_engine.list_sheets(expected_path)

    sheets_to_check = config.sheets_to_check
    if sheets_to_check is None:
        sheets_to_check = [s.name for s in expected_info.sheets]

    total_cells = 0
    matched_cells = 0
    mismatches: list[CellComparison] = []

    for sheet_name in sheets_to_check:
        agent_sheet_names = [s.name for s in agent_info.sheets]
        if sheet_name not in agent_sheet_names:
            expected_sheet = expected_engine.get_sheet_info(expected_path, sheet_name)
            total_cells += expected_sheet.row_count * expected_sheet.col_count
            mismatches.append(
                CellComparison(
                    cell=f"{sheet_name}!*",
                    expected=f"Sheet '{sheet_name}'",
                    actual=None,
                    match=False,
                    reason="Sheet missing from agent output",
                )
            )
            continue

        expected_sheet = expected_engine.get_sheet_info(expected_path, sheet_name)
        rows = expected_sheet.row_count
        cols = expected_sheet.col_count

        if rows == 0 or cols == 0:
            continue

        start = "A1"
        end = f"{_index_to_col_letter(cols - 1)}{rows}"

        expected_range = expected_engine.read_range(expected_path, sheet_name, start, end)
        agent_range = agent_engine.read_range(agent_path, sheet_name, start, end)

        for ri, (exp_row, act_row) in enumerate(
            zip(expected_range.values, agent_range.values)
        ):
            for ci, (exp_val, act_val) in enumerate(zip(exp_row, act_row)):
                cell_ref = f"{_index_to_col_letter(ci)}{ri + 1}"

                if config.ignore_empty_cells and exp_val is None:
                    continue

                total_cells += 1

                if _values_match(exp_val, act_val, config.numeric_tolerance):
                    matched_cells += 1
                else:
                    mismatches.append(
                        CellComparison(
                            cell=f"{sheet_name}!{cell_ref}",
                            expected=exp_val,
                            actual=act_val,
                            match=False,
                            reason=_mismatch_reason(exp_val, act_val, config.numeric_tolerance),
                        )
                    )

        if config.check_formulas:
            formula_total, formula_matched = _check_formulas_for_sheet(
                expected_engine, agent_engine,
                expected_path, agent_path,
                sheet_name, rows, cols, mismatches,
            )
            total_cells += formula_total
            matched_cells += formula_matched

    score = matched_cells / total_cells if total_cells > 0 else 1.0

    if score >= config.pass_threshold:
        status = GradeStatus.PASS
    elif score > 0:
        status = GradeStatus.PARTIAL
    else:
        status = GradeStatus.FAIL

    return GradingResult(
        status=status,
        score=round(score, 4),
        total_cells=total_cells,
        matched_cells=matched_cells,
        mismatches=mismatches[:50],
        notes=f"Checked {len(sheets_to_check)} sheet(s). "
              f"First {min(len(mismatches), 50)} mismatches shown."
              if mismatches else "All cells match.",
    )


def _check_formulas_for_sheet(
    expected_engine: OdsEngine | XlsxEngine,
    agent_engine: OdsEngine | XlsxEngine,
    expected_path: Path,
    agent_path: Path,
    sheet_name: str,
    rows: int,
    cols: int,
    mismatches: list[CellComparison],
) -> tuple[int, int]:
    """Compare formulas between oracle and agent output for a sheet.

    Only cells that have a formula in the oracle are checked.  A formula
    mismatch counts as an additional cell failure (separate from the value
    comparison) so that purely hard-coded answers score lower than proper
    formula-based solutions.

    Returns (formula_total, formula_matched) so the caller can fold them
    into the overall score.
    """
    formula_total = 0
    formula_matched = 0
    for ri in range(rows):
        for ci in range(cols):
            cell_ref = f"{_index_to_col_letter(ci)}{ri + 1}"
            oracle_formula = expected_engine.read_formula(expected_path, sheet_name, cell_ref)
            if oracle_formula is None:
                continue

            agent_formula = agent_engine.read_formula(agent_path, sheet_name, cell_ref)
            oracle_norm = oracle_formula.strip().lower()
            agent_norm = (agent_formula or "").strip().lower()

            formula_total += 1
            if oracle_norm == agent_norm:
                formula_matched += 1
            else:
                mismatches.append(
                    CellComparison(
                        cell=f"{sheet_name}!{cell_ref} (formula)",
                        expected=oracle_formula,
                        actual=agent_formula,
                        match=False,
                        reason=f"Formula mismatch: expected '{oracle_formula}', got '{agent_formula}'",
                    )
                )
    return formula_total, formula_matched


def _values_match(
    expected: str | int | float | bool | None,
    actual: str | int | float | bool | None,
    tolerance: float,
) -> bool:
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False

    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(expected) - float(actual)) <= tolerance

    if isinstance(expected, (int, float)):
        try:
            return abs(float(expected) - float(actual)) <= tolerance
        except (ValueError, TypeError):
            return False

    return str(expected).strip() == str(actual).strip()


def _mismatch_reason(
    expected: str | int | float | bool | None,
    actual: str | int | float | bool | None,
    tolerance: float,
) -> str:
    if actual is None:
        return "Cell is empty in agent output"
    if expected is None:
        return "Cell should be empty"

    if isinstance(expected, (int, float)):
        try:
            diff = abs(float(expected) - float(actual))
            return f"Numeric mismatch: diff={diff:.6f}, tolerance={tolerance}"
        except (ValueError, TypeError):
            return f"Expected numeric {expected}, got non-numeric '{actual}'"

    return f"String mismatch: expected '{expected}', got '{actual}'"
