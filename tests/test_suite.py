"""
Test suite covering spreadsheet engine, grader, MCP tool dispatch, and task lifecycle.

Run:
    python -m pytest tests/test_suite.py -v
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp_server"))

from odf import table, text
from odf.opendocument import OpenDocumentSpreadsheet

from models import (
    ReadCellRequest,
    WriteCellRequest,
    ReadRangeRequest,
    WriteRangeRequest,
    SetFormulaRequest,
    TaskState,
    TaskStatus,
    ToolCallRequest,
    ToolCallResponse,
    ToolDefinition,
)
from spreadsheet_engine import (
    OdsEngine,
    _parse_cell_ref,
    _col_letter_to_index,
    _index_to_col_letter,
)
from grader import grade_spreadsheet, GradingConfig, GradeStatus


@pytest.fixture
def sample_ods(tmp_path: Path) -> Path:
    """Create a small ODS spreadsheet for testing."""
    doc = OpenDocumentSpreadsheet()
    sheet = table.Table(name="Sheet1")

    headers = ["Name", "Value", "Rate"]
    header_row = table.TableRow()
    for h in headers:
        cell = table.TableCell(valuetype="string")
        cell.addElement(text.P(text=h))
        header_row.addElement(cell)
    sheet.addElement(header_row)

    test_data = [
        ("Alice", 100.0, 0.05),
        ("Bob", 200.0, 0.10),
        ("Carol", 300.0, 0.15),
    ]
    for name, value, rate in test_data:
        row = table.TableRow()
        c1 = table.TableCell(valuetype="string")
        c1.addElement(text.P(text=name))
        row.addElement(c1)
        c2 = table.TableCell(valuetype="float", value=str(value))
        c2.addElement(text.P(text=str(value)))
        row.addElement(c2)
        c3 = table.TableCell(valuetype="float", value=str(rate))
        c3.addElement(text.P(text=str(rate)))
        row.addElement(c3)
        sheet.addElement(row)

    doc.spreadsheet.addElement(sheet)
    filepath = tmp_path / "test.ods"
    doc.save(str(filepath))
    return filepath


# ============================================================
# Cell reference parsing
# ============================================================

class TestCellRefParsing:
    def test_simple_ref(self) -> None:
        row, col = _parse_cell_ref("A1")
        assert row == 0
        assert col == 0

    def test_multi_letter_col(self) -> None:
        row, col = _parse_cell_ref("AA1")
        assert row == 0
        assert col == 26

    def test_large_row(self) -> None:
        row, col = _parse_cell_ref("B100")
        assert row == 99
        assert col == 1

    def test_invalid_ref_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid cell reference"):
            _parse_cell_ref("123")

    def test_empty_ref_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid cell reference"):
            _parse_cell_ref("")

    def test_col_letter_roundtrip(self) -> None:
        for i in range(100):
            letter = _index_to_col_letter(i)
            assert _col_letter_to_index(letter) == i


# ============================================================
# Spreadsheet read/write
# ============================================================

class TestSpreadsheetEngine:
    def test_read_cell_string(self, sample_ods: Path) -> None:
        engine = OdsEngine()
        result = engine.read_cell(sample_ods, "Sheet1", "A1")
        assert result.value == "Name"
        assert result.value_type == "string"

    def test_read_cell_float(self, sample_ods: Path) -> None:
        engine = OdsEngine()
        result = engine.read_cell(sample_ods, "Sheet1", "B2")
        assert result.value == 100.0
        assert result.value_type == "float"

    def test_read_cell_empty(self, sample_ods: Path) -> None:
        engine = OdsEngine()
        result = engine.read_cell(sample_ods, "Sheet1", "Z99")
        assert result.value is None
        assert result.value_type == "empty"

    def test_read_range(self, sample_ods: Path) -> None:
        engine = OdsEngine()
        result = engine.read_range(sample_ods, "Sheet1", "A1", "C2")
        assert len(result.values) == 2
        assert result.values[0] == ["Name", "Value", "Rate"]
        assert result.values[1][0] == "Alice"
        assert result.values[1][1] == 100.0

    def test_write_cell(self, sample_ods: Path) -> None:
        engine = OdsEngine()
        engine.write_cell(sample_ods, "Sheet1", "D1", "NewCol")
        result = engine.read_cell(sample_ods, "Sheet1", "D1")
        assert result.value == "NewCol"

    def test_write_cell_numeric(self, sample_ods: Path) -> None:
        engine = OdsEngine()
        engine.write_cell(sample_ods, "Sheet1", "D2", 42.5)
        result = engine.read_cell(sample_ods, "Sheet1", "D2")
        assert result.value == 42.5

    def test_write_range(self, sample_ods: Path) -> None:
        engine = OdsEngine()
        engine.write_range(sample_ods, "Sheet1", "D1", [["Total"], [5.0], [10.0], [15.0]])
        result = engine.read_range(sample_ods, "Sheet1", "D1", "D4")
        assert result.values[0][0] == "Total"
        assert result.values[1][0] == 5.0

    def test_write_cell_bool(self, sample_ods: Path) -> None:
        engine = OdsEngine()
        engine.write_cell(sample_ods, "Sheet1", "D2", True)
        result = engine.read_cell(sample_ods, "Sheet1", "D2")
        assert result.value_type == "boolean" or result.value == "True"

    def test_bad_sheet_raises(self, sample_ods: Path) -> None:
        engine = OdsEngine()
        with pytest.raises(ValueError, match="Sheet 'FakeSheet' not found"):
            engine.read_cell(sample_ods, "FakeSheet", "A1")

    def test_list_sheets(self, sample_ods: Path) -> None:
        engine = OdsEngine()
        info = engine.list_sheets(sample_ods)
        assert len(info.sheets) == 1
        assert info.sheets[0].name == "Sheet1"

    def test_get_sheet_info(self, sample_ods: Path) -> None:
        engine = OdsEngine()
        info = engine.get_sheet_info(sample_ods, "Sheet1")
        assert info.row_count == 4
        assert info.col_count == 3
        assert info.headers == ["Name", "Value", "Rate"]


# ============================================================
# Grading
# ============================================================

class TestGrader:
    def test_identical_files_pass(self, sample_ods: Path) -> None:
        result = grade_spreadsheet(str(sample_ods), str(sample_ods))
        assert result.status == GradeStatus.PASS
        assert result.score == 1.0
        assert result.matched_cells == result.total_cells

    def test_modified_file_fails(self, sample_ods: Path, tmp_path: Path) -> None:
        modified = tmp_path / "modified.ods"
        shutil.copy2(sample_ods, modified)
        engine = OdsEngine()
        engine.write_cell(modified, "Sheet1", "B2", 999.0)

        result = grade_spreadsheet(str(modified), str(sample_ods))
        assert result.status in (GradeStatus.PARTIAL, GradeStatus.FAIL)
        assert result.score < 1.0
        assert len(result.mismatches) > 0

    def test_tolerance_works(self, sample_ods: Path, tmp_path: Path) -> None:
        modified = tmp_path / "close.ods"
        shutil.copy2(sample_ods, modified)
        engine = OdsEngine()
        engine.write_cell(modified, "Sheet1", "B2", 100.005)

        result = grade_spreadsheet(
            str(modified), str(sample_ods),
            GradingConfig(numeric_tolerance=0.01),
        )
        assert result.status == GradeStatus.PASS
        assert result.score == 1.0

    def test_strict_tolerance_catches_diff(self, sample_ods: Path, tmp_path: Path) -> None:
        modified = tmp_path / "off.ods"
        shutil.copy2(sample_ods, modified)
        engine = OdsEngine()
        engine.write_cell(modified, "Sheet1", "B2", 100.5)

        result = grade_spreadsheet(
            str(modified), str(sample_ods),
            GradingConfig(numeric_tolerance=0.01),
        )
        assert result.score < 1.0


# ============================================================
# MCP models and tool dispatch
# ============================================================

class TestMCPModels:
    def test_tool_call_request_valid(self) -> None:
        req = ToolCallRequest(tool_name="read_cell", arguments={"file_path": "test.ods"})
        assert req.tool_name == "read_cell"

    def test_tool_call_response_success(self) -> None:
        resp = ToolCallResponse(success=True, result={"value": 42})
        assert resp.success is True
        assert resp.error is None

    def test_tool_call_response_error(self) -> None:
        resp = ToolCallResponse(success=False, error="File not found")
        assert resp.success is False
        assert resp.error == "File not found"

    def test_tool_definition_structure(self) -> None:
        from mcp_server import TOOL_REGISTRY
        assert len(TOOL_REGISTRY) == 17
        tool_names = [t.name for t in TOOL_REGISTRY]
        assert "read_cell" in tool_names
        assert "take_screenshot" in tool_names
        assert "execute_python" in tool_names

    def test_all_tools_have_handlers(self) -> None:
        from mcp_server import TOOL_REGISTRY, TOOL_HANDLERS
        for tool in TOOL_REGISTRY:
            assert tool.name in TOOL_HANDLERS, f"No handler for tool: {tool.name}"


# ============================================================
# Task state machine
# ============================================================

class TestTaskLifecycle:
    def test_initial_state(self) -> None:
        state = TaskState(task_id="test-1")
        assert state.status == TaskStatus.PENDING
        assert state.started_at is None
        assert state.files_modified == []

    def test_running_state(self) -> None:
        import time
        state = TaskState(
            task_id="test-2",
            status=TaskStatus.RUNNING,
            started_at=time.time(),
            timeout_seconds=300,
        )
        assert state.status == TaskStatus.RUNNING

    def test_timeout_detection(self) -> None:
        state = TaskState(
            task_id="test-3",
            status=TaskStatus.RUNNING,
            started_at=1.0,
            timeout_seconds=10,
        )
        import time
        elapsed = time.time() - state.started_at
        assert elapsed > state.timeout_seconds

    def test_completed_state(self) -> None:
        state = TaskState(task_id="test-4", status=TaskStatus.COMPLETED)
        assert state.status == TaskStatus.COMPLETED

    def test_file_tracking(self) -> None:
        state = TaskState(task_id="test-5", status=TaskStatus.RUNNING)
        state.files_modified.append("test.ods")
        assert "test.ods" in state.files_modified

    def test_status_transitions(self) -> None:
        for status in TaskStatus:
            state = TaskState(task_id="test", status=status)
            assert state.status == status
