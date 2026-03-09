"""
MCP Server for the sandboxed spreadsheet task environment.

Exposes spreadsheet tools (read/write cells, ranges, formulas) and sandbox
utilities (list files, execute Python) over two transports:

  1. **MCP SDK (SSE)** — standards-compliant MCP transport at ``/mcp/sse``
     that any MCP client (Claude Desktop, Cursor, etc.) can connect to.
  2. **REST/HTTP** — lightweight JSON API at ``/tools/call`` for simple
     curl-based and agent-script integration.

Both transports share the same tool handlers and workspace state.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from mcp.server import Server as McpSdkServer
from mcp.server.sse import SseServerTransport
from mcp.types import Resource as McpResource
from mcp.types import TextContent, Tool as McpTool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from computer_use import (
    ClickRequest,
    DoubleClickRequest,
    DragRequest,
    KeyPressRequest,
    MouseMoveRequest,
    TypeTextRequest,
    click,
    double_click,
    drag,
    get_cursor_position,
    key_press,
    mouse_move,
    take_screenshot,
    type_text,
)
from models import (
    ExecutePythonRequest,
    ExecutePythonResponse,
    GetSheetInfoRequest,
    ListFilesResponse,
    ListSheetsRequest,
    ReadCellRequest,
    ReadRangeRequest,
    SetFormulaRequest,
    SetFormulaResponse,
    TaskState,
    TaskStatus,
    ToolCallRequest,
    ToolCallResponse,
    ToolDefinition,
    ToolParameter,
    WriteCellRequest,
    WriteRangeRequest,
)
from spreadsheet_engine import _resolve_path, get_engine

logger = logging.getLogger(__name__)

WORKSPACE = Path("/workspace")
TASK_TIMEOUT = int(os.getenv("TASK_TIMEOUT", "300"))

current_task: TaskState | None = None


# ---------------------------------------------------------------------------
# Tool definitions (used by both REST and MCP SDK transports)
# ---------------------------------------------------------------------------

TOOL_REGISTRY: list[ToolDefinition] = [
    ToolDefinition(
        name="read_cell",
        description="Read the value from a specific cell in the spreadsheet",
        parameters=[
            ToolParameter(name="file_path", type="string", description="Path to spreadsheet file"),
            ToolParameter(name="sheet_name", type="string", description="Name of the sheet"),
            ToolParameter(name="cell_reference", type="string", description="Cell reference, e.g. 'A1'"),
        ],
    ),
    ToolDefinition(
        name="write_cell",
        description="Write a value to a specific cell in the spreadsheet",
        parameters=[
            ToolParameter(name="file_path", type="string", description="Path to spreadsheet file"),
            ToolParameter(name="sheet_name", type="string", description="Name of the sheet"),
            ToolParameter(name="cell_reference", type="string", description="Cell reference, e.g. 'A1'"),
            ToolParameter(name="value", type="string", description="Value to write"),
        ],
    ),
    ToolDefinition(
        name="read_range",
        description="Read a rectangular range of cells from the spreadsheet",
        parameters=[
            ToolParameter(name="file_path", type="string", description="Path to spreadsheet file"),
            ToolParameter(name="sheet_name", type="string", description="Name of the sheet"),
            ToolParameter(name="start_cell", type="string", description="Top-left cell, e.g. 'A1'"),
            ToolParameter(name="end_cell", type="string", description="Bottom-right cell, e.g. 'D10'"),
        ],
    ),
    ToolDefinition(
        name="write_range",
        description="Write a block of values starting from a cell",
        parameters=[
            ToolParameter(name="file_path", type="string", description="Path to spreadsheet file"),
            ToolParameter(name="sheet_name", type="string", description="Name of the sheet"),
            ToolParameter(name="start_cell", type="string", description="Top-left cell to start writing"),
            ToolParameter(name="values", type="array", description="2D array of values to write"),
        ],
    ),
    ToolDefinition(
        name="set_formula",
        description="Set a formula in a specific cell",
        parameters=[
            ToolParameter(name="file_path", type="string", description="Path to spreadsheet file"),
            ToolParameter(name="sheet_name", type="string", description="Name of the sheet"),
            ToolParameter(name="cell_reference", type="string", description="Cell reference, e.g. 'B5'"),
            ToolParameter(name="formula", type="string", description="Formula string, e.g. '=SUM(A1:A10)'"),
        ],
    ),
    ToolDefinition(
        name="list_sheets",
        description="List all sheets in a spreadsheet file",
        parameters=[
            ToolParameter(name="file_path", type="string", description="Path to spreadsheet file"),
        ],
    ),
    ToolDefinition(
        name="get_sheet_info",
        description="Get metadata about a sheet (row/col count, headers)",
        parameters=[
            ToolParameter(name="file_path", type="string", description="Path to spreadsheet file"),
            ToolParameter(name="sheet_name", type="string", description="Name of the sheet"),
        ],
    ),
    ToolDefinition(
        name="list_files",
        description="List all files in the workspace directory",
        parameters=[],
    ),
    ToolDefinition(
        name="execute_python",
        description="Execute arbitrary Python code inside the sandbox",
        parameters=[
            ToolParameter(name="code", type="string", description="Python code to execute"),
        ],
    ),
    # --- Mode 1: Computer Use (GUI) tools ---
    ToolDefinition(
        name="take_screenshot",
        description="Capture the current screen state as a base64-encoded PNG image",
        parameters=[],
    ),
    ToolDefinition(
        name="click",
        description="Click at a specific screen coordinate",
        parameters=[
            ToolParameter(name="x", type="integer", description="X coordinate"),
            ToolParameter(name="y", type="integer", description="Y coordinate"),
            ToolParameter(name="button", type="integer", description="Mouse button: 1=left, 2=middle, 3=right", required=False),
        ],
    ),
    ToolDefinition(
        name="double_click",
        description="Double-click at a specific screen coordinate",
        parameters=[
            ToolParameter(name="x", type="integer", description="X coordinate"),
            ToolParameter(name="y", type="integer", description="Y coordinate"),
        ],
    ),
    ToolDefinition(
        name="type_text",
        description="Type text using keyboard input at the current cursor position",
        parameters=[
            ToolParameter(name="text", type="string", description="Text to type"),
        ],
    ),
    ToolDefinition(
        name="key_press",
        description="Press a key or key combination (e.g. 'Return', 'Tab', 'ctrl+s', 'alt+F4')",
        parameters=[
            ToolParameter(name="key", type="string", description="Key or combo to press"),
        ],
    ),
    ToolDefinition(
        name="mouse_move",
        description="Move the mouse cursor to specific screen coordinates",
        parameters=[
            ToolParameter(name="x", type="integer", description="X coordinate"),
            ToolParameter(name="y", type="integer", description="Y coordinate"),
        ],
    ),
    ToolDefinition(
        name="drag",
        description="Click and drag from one position to another",
        parameters=[
            ToolParameter(name="start_x", type="integer", description="Start X coordinate"),
            ToolParameter(name="start_y", type="integer", description="Start Y coordinate"),
            ToolParameter(name="end_x", type="integer", description="End X coordinate"),
            ToolParameter(name="end_y", type="integer", description="End Y coordinate"),
        ],
    ),
    ToolDefinition(
        name="get_cursor_position",
        description="Get the current mouse cursor position on screen",
        parameters=[],
    ),
]


# ---------------------------------------------------------------------------
# Shared tool handlers (used by both REST and MCP SDK transports)
# ---------------------------------------------------------------------------

def _handle_read_cell(args: dict) -> ToolCallResponse:
    req = ReadCellRequest(**args)
    path = _resolve_path(req.file_path)
    engine = get_engine(path)
    result = engine.read_cell(path, req.sheet_name, req.cell_reference)
    return ToolCallResponse(success=True, result=result.model_dump())


def _handle_write_cell(args: dict) -> ToolCallResponse:
    req = WriteCellRequest(**args)
    path = _resolve_path(req.file_path)
    engine = get_engine(path)
    result = engine.write_cell(path, req.sheet_name, req.cell_reference, req.value)
    _track_file_modified(req.file_path)
    return ToolCallResponse(success=True, result=result.model_dump())


def _handle_read_range(args: dict) -> ToolCallResponse:
    req = ReadRangeRequest(**args)
    path = _resolve_path(req.file_path)
    engine = get_engine(path)
    result = engine.read_range(path, req.sheet_name, req.start_cell, req.end_cell)
    return ToolCallResponse(success=True, result=result.model_dump())


def _handle_write_range(args: dict) -> ToolCallResponse:
    req = WriteRangeRequest(**args)
    path = _resolve_path(req.file_path)
    engine = get_engine(path)
    result = engine.write_range(path, req.sheet_name, req.start_cell, req.values)
    _track_file_modified(req.file_path)
    return ToolCallResponse(success=True, result=result.model_dump())


def _handle_set_formula(args: dict) -> ToolCallResponse:
    req = SetFormulaRequest(**args)
    path = _resolve_path(req.file_path)
    engine = get_engine(path)
    engine.set_formula(path, req.sheet_name, req.cell_reference, req.formula)
    _track_file_modified(req.file_path)
    return ToolCallResponse(
        success=True,
        result=SetFormulaResponse(
            success=True, cell_reference=req.cell_reference, formula=req.formula
        ).model_dump(),
    )


def _handle_list_sheets(args: dict) -> ToolCallResponse:
    req = ListSheetsRequest(**args)
    path = _resolve_path(req.file_path)
    engine = get_engine(path)
    result = engine.list_sheets(path)
    return ToolCallResponse(success=True, result=result.model_dump())


def _handle_get_sheet_info(args: dict) -> ToolCallResponse:
    req = GetSheetInfoRequest(**args)
    path = _resolve_path(req.file_path)
    engine = get_engine(path)
    result = engine.get_sheet_info(path, req.sheet_name)
    return ToolCallResponse(success=True, result=result.model_dump())


def _handle_list_files(_args: dict) -> ToolCallResponse:
    files = []
    for p in WORKSPACE.rglob("*"):
        if p.is_file():
            files.append(str(p.relative_to(WORKSPACE)))
    return ToolCallResponse(
        success=True,
        result=ListFilesResponse(files=sorted(files)).model_dump(),
    )


def _handle_execute_python(args: dict) -> ToolCallResponse:
    req = ExecutePythonRequest(**args)
    try:
        result = subprocess.run(
            [sys.executable, "-c", req.code],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(WORKSPACE),
        )
        resp = ExecutePythonResponse(
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=result.returncode,
        )
        return ToolCallResponse(success=result.returncode == 0, result=resp.model_dump())
    except subprocess.TimeoutExpired:
        return ToolCallResponse(
            success=False,
            error="Code execution timed out after 30 seconds",
        )


def _handle_take_screenshot(_args: dict) -> ToolCallResponse:
    result = take_screenshot()
    return ToolCallResponse(success=True, result=result.model_dump())


def _handle_click(args: dict) -> ToolCallResponse:
    req = ClickRequest(**args)
    result = click(req)
    return ToolCallResponse(success=True, result=result.model_dump())


def _handle_double_click(args: dict) -> ToolCallResponse:
    req = DoubleClickRequest(**args)
    result = double_click(req)
    return ToolCallResponse(success=True, result=result.model_dump())


def _handle_type_text(args: dict) -> ToolCallResponse:
    req = TypeTextRequest(**args)
    result = type_text(req)
    return ToolCallResponse(success=True, result=result.model_dump())


def _handle_key_press(args: dict) -> ToolCallResponse:
    req = KeyPressRequest(**args)
    result = key_press(req)
    return ToolCallResponse(success=True, result=result.model_dump())


def _handle_mouse_move(args: dict) -> ToolCallResponse:
    req = MouseMoveRequest(**args)
    result = mouse_move(req)
    return ToolCallResponse(success=True, result=result.model_dump())


def _handle_drag(args: dict) -> ToolCallResponse:
    req = DragRequest(**args)
    result = drag(req)
    return ToolCallResponse(success=True, result=result.model_dump())


def _handle_get_cursor_position(_args: dict) -> ToolCallResponse:
    result = get_cursor_position()
    return ToolCallResponse(success=True, result=result.model_dump())


TOOL_HANDLERS = {
    "read_cell": _handle_read_cell,
    "write_cell": _handle_write_cell,
    "read_range": _handle_read_range,
    "write_range": _handle_write_range,
    "set_formula": _handle_set_formula,
    "list_sheets": _handle_list_sheets,
    "get_sheet_info": _handle_get_sheet_info,
    "list_files": _handle_list_files,
    "execute_python": _handle_execute_python,
    "take_screenshot": _handle_take_screenshot,
    "click": _handle_click,
    "double_click": _handle_double_click,
    "type_text": _handle_type_text,
    "key_press": _handle_key_press,
    "mouse_move": _handle_mouse_move,
    "drag": _handle_drag,
    "get_cursor_position": _handle_get_cursor_position,
}


def _track_file_modified(file_path: str) -> None:
    global current_task
    if current_task and file_path not in current_task.files_modified:
        current_task.files_modified.append(file_path)


def _check_timeout() -> bool:
    if current_task and current_task.started_at:
        elapsed = time.time() - current_task.started_at
        if elapsed > current_task.timeout_seconds:
            current_task.status = TaskStatus.TIMED_OUT
            current_task.error = f"Task timed out after {current_task.timeout_seconds}s"
            return True
    return False


# ===================================================================
# Transport 1: MCP SDK (SSE) — standards-compliant MCP protocol
# ===================================================================

_PARAM_TYPE_MAP = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "array": "array",
    "boolean": "boolean",
}


def _tool_def_to_mcp_tool(td: ToolDefinition) -> McpTool:
    """Convert an internal ToolDefinition to the MCP SDK's Tool type."""
    properties: dict = {}
    required: list[str] = []
    for p in td.parameters:
        properties[p.name] = {
            "type": _PARAM_TYPE_MAP.get(p.type, "string"),
            "description": p.description,
        }
        if p.required:
            required.append(p.name)
    return McpTool(
        name=td.name,
        description=td.description,
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": required,
        },
    )


mcp_sdk_server = McpSdkServer("mcp-sandbox")
sse_transport = SseServerTransport("/mcp/messages")


@mcp_sdk_server.list_tools()
async def mcp_list_tools() -> list[McpTool]:
    return [_tool_def_to_mcp_tool(td) for td in TOOL_REGISTRY]


@mcp_sdk_server.call_tool()
async def mcp_call_tool(name: str, arguments: dict) -> list[TextContent]:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
    try:
        result = handler(arguments)
        return [TextContent(type="text", text=json.dumps(result.model_dump()))]
    except Exception as exc:
        return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]


@mcp_sdk_server.list_resources()
async def mcp_list_resources() -> list[McpResource]:
    resources: list[McpResource] = []
    for p in WORKSPACE.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(WORKSPACE))
            resources.append(
                McpResource(
                    uri=f"file://{rel}",
                    name=rel,
                    mimeType=_guess_mime(p),
                )
            )
    return resources


async def handle_sse(scope, receive, send):
    """ASGI handler that bridges the MCP SDK server to the SSE transport."""
    async with sse_transport.connect_sse(scope, receive, send) as (read_stream, write_stream):
        await mcp_sdk_server.run(
            read_stream, write_stream, mcp_sdk_server.create_initialization_options()
        )


# ===================================================================
# Transport 2: REST/HTTP — lightweight JSON endpoints
# ===================================================================

async def tools_list(request: Request) -> JSONResponse:
    """GET /tools — return the list of available MCP tools."""
    return JSONResponse([t.model_dump() for t in TOOL_REGISTRY])


async def tools_call(request: Request) -> JSONResponse:
    """POST /tools/call — execute an MCP tool."""
    if _check_timeout():
        return JSONResponse(
            ToolCallResponse(success=False, error="Task has timed out").model_dump(),
            status_code=408,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            ToolCallResponse(success=False, error="Malformed JSON in request body").model_dump(),
            status_code=400,
        )

    try:
        call = ToolCallRequest(**body)
    except Exception as exc:
        return JSONResponse(
            ToolCallResponse(
                success=False,
                error=f"Invalid request: must include 'tool_name' and 'arguments'. {exc}",
            ).model_dump(),
            status_code=400,
        )

    handler = TOOL_HANDLERS.get(call.tool_name)
    if handler is None:
        return JSONResponse(
            ToolCallResponse(success=False, error=f"Unknown tool: {call.tool_name}").model_dump(),
            status_code=404,
        )

    try:
        result = handler(call.arguments)
        return JSONResponse(result.model_dump())
    except Exception as exc:
        return JSONResponse(
            ToolCallResponse(success=False, error=str(exc)).model_dump(),
            status_code=500,
        )


async def resources_list(request: Request) -> JSONResponse:
    """GET /resources — list workspace files as MCP resources."""
    resources = []
    for p in WORKSPACE.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(WORKSPACE))
            resources.append({
                "uri": f"file://{rel}",
                "name": rel,
                "mimeType": _guess_mime(p),
            })
    return JSONResponse(resources)


async def task_start(request: Request) -> JSONResponse:
    """POST /task/start — begin a new task session with timeout tracking."""
    global current_task
    body = await request.json()
    task_id = body.get("task_id", f"task-{int(time.time())}")
    timeout = body.get("timeout_seconds", TASK_TIMEOUT)
    current_task = TaskState(
        task_id=task_id,
        status=TaskStatus.RUNNING,
        started_at=time.time(),
        timeout_seconds=timeout,
    )
    return JSONResponse(current_task.model_dump())


async def task_status(request: Request) -> JSONResponse:
    """GET /task/status — check current task state."""
    if current_task is None:
        return JSONResponse({"error": "No active task"}, status_code=404)
    _check_timeout()
    return JSONResponse(current_task.model_dump())


async def task_complete(request: Request) -> JSONResponse:
    """POST /task/complete — mark the current task as completed."""
    global current_task
    if current_task is None:
        return JSONResponse({"error": "No active task"}, status_code=404)
    current_task.status = TaskStatus.COMPLETED
    result = current_task.model_dump()
    marker = WORKSPACE / ".task_complete"
    marker.write_text(current_task.task_id)
    logger.info("Task '%s' completed, marker written to %s", current_task.task_id, marker)
    current_task = None
    return JSONResponse(result)


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "workspace": str(WORKSPACE)})


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    mime_map = {
        ".ods": "application/vnd.oasis.opendocument.spreadsheet",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv",
        ".json": "application/json",
        ".py": "text/x-python",
        ".txt": "text/plain",
    }
    return mime_map.get(suffix, "application/octet-stream")


# ===================================================================
# Starlette app — mounts both REST and MCP SDK transports
# ===================================================================

app = Starlette(
    routes=[
        # REST/HTTP transport
        Route("/health", health, methods=["GET"]),
        Route("/tools", tools_list, methods=["GET"]),
        Route("/tools/call", tools_call, methods=["POST"]),
        Route("/resources", resources_list, methods=["GET"]),
        Route("/task/start", task_start, methods=["POST"]),
        Route("/task/status", task_status, methods=["GET"]),
        Route("/task/complete", task_complete, methods=["POST"]),
        # MCP SDK SSE transport
        Route("/mcp/sse", handle_sse),
        Mount("/mcp/messages", app=sse_transport.handle_post_message),
    ],
)


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8080"))
    logger.info("Starting MCP server on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)
