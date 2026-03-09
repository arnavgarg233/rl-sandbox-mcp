from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class CellReference(BaseModel):
    sheet_name: str = Field(description="Name of the sheet")
    cell: str = Field(description="Cell reference, e.g. 'A1', 'B5'")


class CellRange(BaseModel):
    sheet_name: str
    start_cell: str = Field(description="Top-left cell, e.g. 'A1'")
    end_cell: str = Field(description="Bottom-right cell, e.g. 'D10'")


class CellValue(BaseModel):
    cell: str
    value: str | int | float | bool | None


class SheetInfo(BaseModel):
    name: str
    row_count: int
    col_count: int


class SpreadsheetInfo(BaseModel):
    file_path: str
    sheets: list[SheetInfo]


class ReadCellRequest(BaseModel):
    file_path: str = Field(description="Path to spreadsheet file in /workspace")
    sheet_name: str
    cell_reference: str = Field(description="Cell reference like 'A1'")


class ReadCellResponse(BaseModel):
    cell_reference: str
    value: str | int | float | bool | None
    value_type: str


class WriteCellRequest(BaseModel):
    file_path: str
    sheet_name: str
    cell_reference: str
    value: str | int | float | bool


class WriteCellResponse(BaseModel):
    success: bool
    cell_reference: str
    new_value: str | int | float | bool


class ReadRangeRequest(BaseModel):
    file_path: str
    sheet_name: str
    start_cell: str
    end_cell: str


class ReadRangeResponse(BaseModel):
    start_cell: str
    end_cell: str
    values: list[list[str | int | float | bool | None]]


class WriteRangeRequest(BaseModel):
    file_path: str
    sheet_name: str
    start_cell: str
    values: list[list[str | int | float | bool | None]]


class WriteRangeResponse(BaseModel):
    success: bool
    cells_written: int


class SetFormulaRequest(BaseModel):
    file_path: str
    sheet_name: str
    cell_reference: str
    formula: str = Field(description="Formula string, e.g. '=SUM(A1:A10)'")


class SetFormulaResponse(BaseModel):
    success: bool
    cell_reference: str
    formula: str


class ListSheetsRequest(BaseModel):
    file_path: str


class GetSheetInfoRequest(BaseModel):
    file_path: str
    sheet_name: str


class GetSheetInfoResponse(BaseModel):
    sheet_name: str
    row_count: int
    col_count: int
    headers: list[str | None]


class ListFilesResponse(BaseModel):
    files: list[str]


class ExecutePythonRequest(BaseModel):
    code: str = Field(description="Python code to execute inside the sandbox")


class ExecutePythonResponse(BaseModel):
    stdout: str
    stderr: str
    return_code: int


class TaskState(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    started_at: float | None = None
    timeout_seconds: int = 300
    files_modified: list[str] = Field(default_factory=list)
    error: str | None = None


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: list[ToolParameter]


class ToolParameter(BaseModel):
    name: str
    type: str
    description: str
    required: bool = True


class ToolCallRequest(BaseModel):
    """Generic MCP tool invocation request.

    ``arguments`` uses dict[str, Any] intentionally: each of the 17 tools has
    a different parameter schema.  The handler immediately validates the raw
    dict into the tool-specific Pydantic model (e.g. ReadCellRequest,
    ClickRequest), so type safety is still enforced at dispatch time.  A
    discriminated union would be possible but adds significant coupling
    between the transport layer and every tool definition.
    """

    tool_name: str
    arguments: dict[str, Any]


class ToolCallResponse(BaseModel):
    success: bool
    result: Any = None
    error: str | None = None
