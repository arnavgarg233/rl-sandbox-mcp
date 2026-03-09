# MCP-Based Sandboxed Task Environment

A safe, isolated RL environment where AI agents can perform spreadsheet tasks in LibreOffice, with automated evaluation of performance. Agents interact via a **Model Context Protocol (MCP)** API, supporting both GUI-based (Computer Use) and programmatic (odfpy) interaction modes.

**Video Demo:** [Loom — MCP Sandbox Demo](https://www.loom.com/share/16d12ef83acf4d45a042349f4465fcb2)

## Demo & Grading Validation

The demo dashboard (`scripts/demo_ui.py`) runs both agents and shows the spreadsheet updating in real time alongside every MCP tool call.

```bash
pip install streamlit openai
docker compose up -d
streamlit run scripts/demo_ui.py
```

### Scripted Agent (`scripts/demo_agent.py`)

A deterministic Python agent that calls MCP tools in sequence: `list_files` → `get_sheet_info` → `read_range` → compute interpolated reserves → `write_cell` + `write_range` → `task/complete`. Scores 100% every time.

### LLM Agent (`scripts/llm_agent.py`)

A GPT-4o agent that autonomously discovers and uses MCP tools to solve the same task. GPT-4o receives the list of all 17 available tools and decides which to call.

**How we know grading works — GPT-4o's iteration story:**

When GPT-4o was given full autonomy, it tried to write raw odfpy code via `execute_python` and consistently failed — odfpy's API is non-obvious and the model kept producing buggy cell-manipulation code (wrong attribute names, incorrect type conversions). The grader caught every mistake: GPT-4o's initial attempts scored **67% (214/315 cells)**, with the grading report showing exactly which cells were wrong and why.

After observing these failures, we guided GPT-4o to use `execute_python` with pandas (`pd.read_excel` / `df.to_excel` with the odf engine), which it knows well. This brought the score to **100% (315/315 cells)**.

This demonstrates two things:
1. **The grading system works** — it catches incorrect output and reports precise cell-level mismatches
2. **The sandbox is flexible** — agents can use MCP tools directly, run arbitrary Python, or combine both approaches

The final LLM agent flow:
1. `list_files` → `get_sheet_info` → `read_range` (discover and read data via MCP)
2. `write_cell` (set the header via MCP)
3. `execute_python` with a pandas script (compute interpolation + write results)
4. `TASK_COMPLETE` → grader runs → PASS

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Host Machine                         │
│                                                         │
│  ┌──────────────┐    ┌──────────────────────────────┐   │
│  │   sandbox_    │    │     Docker Container         │   │
│  │   manager.py  │───▶│  ┌────────────────────────┐  │   │
│  │              │    │  │     MCP Server (:8080)  │  │   │
│  │  • build     │    │  │                        │  │   │
│  │  • run       │    │  │  17 Tools Exposed:     │  │   │
│  │  • grade     │    │  │  • 9 Programmatic      │  │   │
│  │  • destroy   │    │  │  • 8 Computer Use      │  │   │
│  └──────────────┘    │  └────────┬───────────────┘  │   │
│                      │           │                   │   │
│  ┌──────────────┐    │  ┌────────▼───────────────┐  │   │
│  │  shared/     │◀──▶│  │  /workspace            │  │   │
│  │  (volume     │    │  │  • cash_flows.ods      │  │   │
│  │   mount)     │    │  │  • (agent output)      │  │   │
│  └──────────────┘    │  └────────────────────────┘  │   │
│                      │                               │   │
│  ┌──────────────┐    │  ┌────────────────────────┐  │   │
│  │  grader.py   │    │  │  LibreOffice Calc      │  │   │
│  │  • compare   │    │  │  Xvfb (virtual display)│  │   │
│  │  • score     │    │  │  Python + pandas/odfpy │  │   │
│  └──────────────┘    │  └────────────────────────┘  │   │
│                      │                               │   │
│                      │  🔒 Network-restricted:        │   │
│                      │  │  iptables blocks outbound   │   │
│                      │  │  DNS disabled, caps dropped  │   │
│                      │  🔒 2GB RAM / 2 CPU / 256 PIDs│   │
│                      └──────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

**Sandbox lifecycle:**
1. Build container from image (Ubuntu 22.04 + LibreOffice + Python)
2. Copy task-specific starting files into the shared workspace
3. Agent connects via MCP and works on the task
4. Extract output files when agent signals "done"
5. Run grading script to compare output against oracle
6. Destroy container (clean slate for next attempt)

## Prerequisites

- **Docker Desktop** (macOS/Windows) or Docker Engine (Linux)
- **Python 3.10+** (for running the sandbox manager, grading, and tests locally)

```bash
pip install -e ".[dev]"
```

This installs all runtime dependencies (`odfpy`, `openpyxl`, `pydantic`, `starlette`, `uvicorn`, `mcp`) and dev dependencies (`pytest`, `httpx`). The MCP SDK is required because the test suite imports `mcp_server.py`, which uses `mcp.server.Server` and `mcp.server.sse.SseServerTransport`.

## Quick Start

### 1. Generate example task files

```bash
python3 tasks/banking_reserve/generate_task_files.py
```

This creates:
- `tasks/banking_reserve/starting_files/cash_flows.ods` — 100-row spreadsheet with sparse rate points
- `tasks/banking_reserve/oracle/expected_reserves.ods` — correct answer with interpolated reserves

### 2. Build the Docker image

```bash
python3 scripts/sandbox_manager.py build
```

### 3. Run a task end-to-end

```bash
python3 scripts/sandbox_manager.py run --task banking_reserve
```

This will:
- Copy starting files into `shared/`
- Start the container with network restrictions (iptables + DNS blocking)
- Wait for agent completion (or timeout after 300s)
- Extract output files
- Run the grading script
- Print the score
- Destroy the container

### 4. Or run manually with docker compose

```bash
docker compose up -d          # start the container
curl http://localhost:8080/health   # verify it's running
curl http://localhost:8080/tools    # see all 17 MCP tools
docker compose down           # stop and remove
```

## MCP Protocol Implementation

The server is built on the official [MCP Python SDK](https://pypi.org/project/mcp/) (`mcp>=1.0.0`). The core MCP implementation uses `mcp.server.Server` for tool/resource registration and `mcp.server.sse.SseServerTransport` for the standards-compliant SSE transport — the same JSON-RPC protocol that Claude Desktop, Cursor, and other MCP clients speak natively.

Tools are registered via `@mcp_sdk_server.list_tools()` and `@mcp_sdk_server.call_tool()` decorators, and resources via `@mcp_sdk_server.list_resources()`. Both transports share the same `TOOL_HANDLERS` dispatch table, so behaviour is identical regardless of how the client connects.

### Transport 1: MCP SDK (SSE) — Standards-compliant

Any MCP client can connect directly:

| Endpoint | Method | Description |
|---|---|---|
| `/mcp/sse` | GET | SSE connection endpoint (MCP JSON-RPC protocol) |
| `/mcp/messages` | POST | Message endpoint (MCP JSON-RPC protocol) |

### Transport 2: REST/HTTP — Lightweight JSON API

For scripting, `curl`, and simple agent integration:

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/tools` | GET | List all available MCP tools |
| `/tools/call` | POST | Execute an MCP tool |
| `/resources` | GET | List workspace files as MCP resources |
| `/task/start` | POST | Begin a task session with timeout |
| `/task/status` | GET | Check current task state |
| `/task/complete` | POST | Mark task as completed |

Both transports share the same tool handlers and workspace state.

## MCP Tools (17 Total)

### Mode 2: Programmatic (odfpy/openpyxl) — 9 tools

| Tool | Description |
|---|---|
| `read_cell` | Read the value from a specific cell in the spreadsheet |
| `write_cell` | Write a value to a specific cell in the spreadsheet |
| `read_range` | Read a rectangular range of cells |
| `write_range` | Write a block of values starting from a cell |
| `set_formula` | Set a formula in a specific cell (e.g. `=SUM(A1:A10)`) |
| `list_sheets` | List all sheets in a spreadsheet file |
| `get_sheet_info` | Get metadata about a sheet (row/col count, headers) |
| `list_files` | List all files in the workspace directory |
| `execute_python` | Execute arbitrary Python code inside the sandbox |

### Mode 1: Computer Use (GUI) — 8 tools

| Tool | Description |
|---|---|
| `take_screenshot` | Capture current screen state as base64-encoded PNG (1280x720) |
| `click` | Click at specific screen coordinates |
| `double_click` | Double-click at specific screen coordinates |
| `type_text` | Type text using keyboard input |
| `key_press` | Press a key or combination (e.g. `Return`, `ctrl+s`) |
| `mouse_move` | Move the mouse cursor to specific coordinates |
| `drag` | Click and drag from one position to another |
| `get_cursor_position` | Get the current mouse cursor position |

## Example: Calling an MCP Tool

```bash
# Read a cell
curl -X POST http://localhost:8080/tools/call \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"read_cell","arguments":{"file_path":"cash_flows.ods","sheet_name":"Sheet1","cell_reference":"A1"}}'

# Response:
# {"success":true,"result":{"cell_reference":"A1","value":"Period","value_type":"string"},"error":null}

# Take a screenshot (Mode 1)
curl -X POST http://localhost:8080/tools/call \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"take_screenshot","arguments":{}}'

# Response:
# {"success":true,"result":{"image_base64":"iVBOR...","width":1280,"height":720,"timestamp":1773001940.1},"error":null}
```

## Task Structure

Each task is a directory under `tasks/` containing:

```
tasks/<task_name>/
├── task.json              # Problem statement, instructions, grading config
├── generate_task_files.py # Script to generate starting + oracle files
├── grade.py               # Task-specific grading script
├── starting_files/        # Files the agent receives
│   └── cash_flows.ods
└── oracle/                # Expected correct output
    └── expected_reserves.ods
```

### Grading

The grader compares agent output against the oracle spreadsheet:
- Cell-by-cell numeric comparison with configurable tolerance (default: 0.01)
- String matching for non-numeric cells
- Handles missing sheets and empty cells
- Returns a score from 0.0 to 1.0 with detailed mismatch reports

## Expected Demo Output

### Build

```bash
$ python3 scripts/sandbox_manager.py build
[BUILD] Building sandbox Docker image...
  → docker compose build
 Image mcp-sandbox Built
[BUILD] Done.
```

### Full lifecycle with automated checks

```bash
$ python3 scripts/sandbox_manager.py run --task banking_reserve --demo
============================================================
TASK: Banking Reserve Calculation
============================================================
  Copied .../starting_files/cash_flows.ods → .../shared/cash_flows.ods
[START] MCP server is up: {'status': 'ok', 'workspace': '/workspace'}

[DEMO] Running automated MCP tool checks...
  [PASS] Health endpoint responds
  [PASS] Tools endpoint returns 17 tools
  [PASS] Mode 2: read_cell tool available
  [PASS] Mode 2: write_cell tool available
  [PASS] Mode 2: set_formula tool available
  [PASS] Mode 1: take_screenshot tool available
  [PASS] Mode 1: click tool available
  [PASS] Mode 1: type_text tool available
  [PASS] Workspace has files: ['cash_flows.ods']
  [PASS] read_cell A1 = 'Period'
  [PASS] read_range returns 3 rows
  [PASS] Sheet has 101 rows (expected 101)
  [PASS] write_cell D1 header
  [PASS] take_screenshot (1280px wide, 159808 chars)
  [PASS] click(400, 300)
  [PASS] task/start
  [PASS] task/status shows running
[DEMO] Results: 17/17 checks passed

GRADING RESULT: PASS
Score: 100.00%
Matched: 404/404 cells

[CLEAN] Removing sandbox container...
```

### Demo agent solving the task via MCP

```bash
$ docker compose up -d && sleep 5 && python3 scripts/demo_agent.py
=== Demo Agent: Banking Reserve Calculation ===
[1] Starting task session...
[2] Listing workspace files...
    Found: cash_flows.ods
[3] Reading sheet info...
    101 rows, 3 cols, Headers: ['Period', 'Cash_Flow_Amount', 'Rate_Point']
[5] Analyzing data...
    100 periods, 11 known rate points
[8] Writing 100 reserve values...
[10] Signaling task completion...
=== Agent finished! ===

# Grade the agent's output:
GRADING RESULT: PASS
Score: 100.00%
Matched: 404/404 cells
```

## Error Handling

The MCP server returns structured error responses for all failure cases:

```json
{"success": false, "result": null, "error": "File not found: /workspace/nonexistent.ods"}
{"success": false, "result": null, "error": "Sheet 'BadSheet' not found"}
{"success": false, "result": null, "error": "Invalid cell reference: ZZZ"}
{"success": false, "result": null, "error": "Task has timed out"}
{"success": false, "result": null, "error": "Unknown tool: fake_tool"}
{"success": false, "result": null, "error": "Code execution timed out after 30 seconds"}
```

All tool calls are validated through Pydantic models before execution. Malformed requests return HTTP 400. Unknown tools return HTTP 404. Timed-out tasks return HTTP 408. Internal errors (bad file path, bad sheet name, bad cell ref) return HTTP 500 with a descriptive error message.

## Sandbox Security

The container is network-restricted and privilege-restricted through multiple layers. It has controlled access to two mounted directories (`shared/` for workspace files and `mcp_server/` for server code) — this enables file exchange while keeping the rest of the host filesystem inaccessible.

| Layer | Implementation | How It Works |
|---|---|---|
| **Firewall (iptables)** | `entrypoint.sh` sets up `OUTPUT` chain rules as root before dropping privileges | Blocks all outbound connections; only replies to inbound requests (`ESTABLISHED`/`RELATED`) are allowed. The MCP server can respond to API calls, but no process inside the sandbox can initiate connections to the internet. |
| **DNS blocking** | `--dns 0.0.0.0` in `docker-compose.yml` and `sandbox_manager.py` | Defence-in-depth: even if iptables were bypassed, hostname resolution would fail. |
| **Capability drop** | `cap_drop: ALL` + selective `cap_add: NET_ADMIN, SETUID, SETGID` | Drops all Linux capabilities. The three added back are used **only during container startup**: `NET_ADMIN` for the iptables rules, `SETUID`/`SETGID` for `su` to drop from root to the `sandbox` user. Once the entrypoint completes its privileged setup and execs as `sandbox`, these capabilities are no longer exercisable by the unprivileged user. |
| **Privilege escalation prevention** | `security_opt: no-new-privileges:true` | Prevents any process from gaining new privileges through `execve()` (e.g. suid binaries). Once the entrypoint drops to `sandbox`, there is no path back to root. |
| **Non-root execution** | Entrypoint starts as root for iptables, then `exec su sandbox` for all services | MCP server, LibreOffice, and Xvfb all run as the unprivileged `sandbox` user. |
| **Resource limits** | `mem_limit: 2g`, `cpus: 2.0`, `pids_limit: 256` | Prevents resource exhaustion and fork bombs. |
| **Localhost-only port** | `127.0.0.1:8080:8080` | MCP server is only accessible from the host machine, not from the network. |
| **Time limits** | Configurable timeout, auto-rejects tools after expiry | `mcp_server.py` (`_check_timeout()`) |
| **Clean slate** | Container destroyed after each task attempt | `sandbox_manager.py` (`clean_container()`) |
| **Code execution timeout** | `execute_python` kills after 30s | `mcp_server.py` (`subprocess.run(timeout=30)`) |

### Why not `--network none`?

True network disablement (`network_mode: none`) would be the strongest isolation, but it also prevents the MCP server from being reachable over HTTP — the host needs a TCP path to `localhost:8080` for the agent to call tools. The alternative would be file-based IPC (agent writes requests to a shared volume, server polls and writes responses), which trades network attack surface for architectural complexity. The iptables + DNS + capability-drop approach used here is a pragmatic middle ground: the container can **receive** connections but cannot **initiate** any, which is the threat model that matters for a sandboxed task runner.

## Project Structure

```
rl-sandbox-mcp/
├── README.md
├── pyproject.toml
├── docker-compose.yml
├── sandbox/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── entrypoint.sh
├── mcp_server/
│   ├── models.py               # Pydantic models (strongly typed)
│   ├── spreadsheet_engine.py   # ODS + XLSX read/write engines
│   ├── mcp_server.py           # HTTP MCP server (17 tools)
│   ├── computer_use.py         # GUI interaction tools
│   └── grader.py               # Automated grading engine
├── tasks/
│   ├── banking_reserve/        # Medium: interpolation + finance
│   │   ├── task.json
│   │   ├── generate_task_files.py
│   │   ├── grade.py
│   │   ├── starting_files/
│   │   └── oracle/
│   └── sales_revenue/          # Easy: arithmetic (Qty × Price)
│       ├── task.json
│       ├── generate_task_files.py
│       ├── grade.py
│       ├── starting_files/
│       └── oracle/
├── scripts/
│   ├── sandbox_manager.py      # Full lifecycle automation
│   ├── demo_agent.py           # Scripted agent that solves a task via MCP
│   ├── llm_agent.py            # GPT-4o agent (autonomous MCP tool use)
│   ├── demo_ui.py              # Streamlit visual dashboard for demos
│   └── run_demo.sh             # Shell script for end-to-end demo
├── tests/
│   └── test_suite.py           # Unit + integration tests
└── shared/                     # Volume mount (agent workspace)
```
