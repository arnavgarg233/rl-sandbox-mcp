"""
Sandbox lifecycle manager.

Handles the full lifecycle of a sandboxed task:
  1. Build and start the Docker container
  2. Copy task-specific starting files into the workspace
  3. Wait for the agent to complete (or timeout)
  4. Extract output files
  5. Run the grading script
  6. Destroy the container

Usage:
    python sandbox_manager.py run   --task banking_reserve
    python sandbox_manager.py run   --task banking_reserve --demo
    python sandbox_manager.py build
    python sandbox_manager.py clean
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = PROJECT_ROOT / "tasks"
SHARED_DIR = PROJECT_ROOT / "shared"
OUTPUT_DIR = PROJECT_ROOT / "output"

CONTAINER_NAME = "mcp-sandbox"
IMAGE_NAME = "mcp-sandbox"
MCP_BASE_URL = "http://localhost:8080"


class TaskConfig(BaseModel):
    task_id: str
    title: str
    description: str
    instructions: list[str]
    starting_files: list[str]
    oracle_file: str
    timeout_seconds: int = 300
    difficulty: str = "medium"
    tags: list[str] = Field(default_factory=list)


class RunResult(BaseModel):
    task_id: str
    container_started: bool = False
    files_copied: bool = False
    agent_completed: bool = False
    grading_score: float | None = None
    grading_status: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0


def _run_cmd(
    cmd: list[str],
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    print(f"  → {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
    )


def _mcp_call(tool_name: str, arguments: dict | None = None) -> dict:
    """Call an MCP tool via HTTP and return the parsed response."""
    payload = json.dumps({
        "tool_name": tool_name,
        "arguments": arguments or {},
    }).encode()
    req = Request(
        f"{MCP_BASE_URL}/tools/call",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _mcp_get(path: str) -> dict | list:
    """GET an MCP endpoint and return parsed JSON."""
    req = Request(f"{MCP_BASE_URL}{path}")
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def build_image() -> None:
    """Build the Docker sandbox image via docker compose."""
    print("\n[BUILD] Building sandbox Docker image...")
    _run_cmd(["docker", "compose", "build"], check=True)
    print("[BUILD] Done.")


def clean_container() -> None:
    """Stop and remove the sandbox container if it exists."""
    print("\n[CLEAN] Removing sandbox container...")
    _run_cmd(["docker", "rm", "-f", CONTAINER_NAME], check=False)
    print("[CLEAN] Done.")


def load_task(task_name: str) -> TaskConfig:
    """Load a task definition from the tasks directory."""
    task_dir = TASKS_DIR / task_name
    task_file = task_dir / "task.json"
    if not task_file.exists():
        raise FileNotFoundError(f"Task file not found: {task_file}")

    data = json.loads(task_file.read_text())
    return TaskConfig(**data)


def prepare_workspace(task_name: str, task: TaskConfig) -> None:
    """Copy starting files into the shared workspace directory."""
    SHARED_DIR.mkdir(parents=True, exist_ok=True)

    for f in SHARED_DIR.iterdir():
        if f.is_file():
            f.unlink()

    task_dir = TASKS_DIR / task_name / "starting_files"
    for filename in task.starting_files:
        src = task_dir / filename
        dst = SHARED_DIR / filename
        if not src.exists():
            raise FileNotFoundError(f"Starting file not found: {src}")
        shutil.copy2(src, dst)
        print(f"  Copied {src} → {dst}")


def start_container(task: TaskConfig) -> bool:
    """Start the sandbox container with network isolation."""
    print("\n[START] Launching sandbox container...")
    clean_container()

    result = _run_cmd([
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "--dns", "0.0.0.0",
        "--cap-drop=ALL",
        "--cap-add=NET_ADMIN",
        "--cap-add=SETUID",
        "--cap-add=SETGID",
        "--security-opt=no-new-privileges:true",
        "--pids-limit=256",
        "-v", f"{SHARED_DIR}:/workspace",
        "-v", f"{PROJECT_ROOT / 'mcp_server'}:/app",
        "-p", "127.0.0.1:8080:8080",
        "-e", f"TASK_TIMEOUT={task.timeout_seconds}",
        "-e", "LAUNCH_LIBREOFFICE=1",
        "-e", "DISPLAY=:99",
        "--memory", "2g",
        "--cpus", "2.0",
        IMAGE_NAME,
    ])

    if result.returncode != 0:
        print(f"[START] Failed: {result.stderr}")
        return False

    print("[START] Waiting for MCP server to be ready...")
    for attempt in range(15):
        time.sleep(2)
        try:
            health = _mcp_get("/health")
            print(f"[START] MCP server is up: {health}")
            return True
        except (URLError, ConnectionError, OSError):
            print(f"  Attempt {attempt + 1}/15 — waiting...")

    print("[START] MCP server failed to start in time.")
    return False


def wait_for_agent(task: TaskConfig) -> bool:
    """
    Wait for the agent to signal completion or for timeout.
    In a real setup, the agent would POST to /task/complete.
    Here we poll for the marker file or timeout.
    """
    print(f"\n[WAIT] Waiting for agent (timeout: {task.timeout_seconds}s)...")
    marker = SHARED_DIR / ".task_complete"
    start = time.time()

    while time.time() - start < task.timeout_seconds:
        if marker.exists():
            print("[WAIT] Agent signaled completion.")
            return True
        time.sleep(2)

    print("[WAIT] Timed out.")
    return False


def run_demo_checks(task: TaskConfig) -> bool:
    """Run automated checks against the MCP server to verify everything works."""
    print("\n[DEMO] Running automated MCP tool checks...")
    checks_passed = 0
    checks_total = 0

    def _check(name: str, condition: bool) -> None:
        nonlocal checks_passed, checks_total
        checks_total += 1
        status = "PASS" if condition else "FAIL"
        if condition:
            checks_passed += 1
        print(f"  [{status}] {name}")

    # 1. Health check
    try:
        health = _mcp_get("/health")
        _check("Health endpoint responds", health.get("status") == "ok")
    except Exception as e:
        _check(f"Health endpoint responds ({e})", False)

    # 2. List tools
    try:
        tools = _mcp_get("/tools")
        tool_names = [t["name"] for t in tools]
        _check(f"Tools endpoint returns {len(tools)} tools", len(tools) == 17)
        _check("Mode 2: read_cell tool available", "read_cell" in tool_names)
        _check("Mode 2: write_cell tool available", "write_cell" in tool_names)
        _check("Mode 2: set_formula tool available", "set_formula" in tool_names)
        _check("Mode 1: take_screenshot tool available", "take_screenshot" in tool_names)
        _check("Mode 1: click tool available", "click" in tool_names)
        _check("Mode 1: type_text tool available", "type_text" in tool_names)
    except Exception as e:
        _check(f"Tools endpoint ({e})", False)

    # 3. List files
    try:
        resp = _mcp_call("list_files")
        files = resp.get("result", {}).get("files", [])
        _check(f"Workspace has files: {files}", len(files) > 0)
    except Exception as e:
        _check(f"list_files ({e})", False)

    # 4. Read cell
    try:
        resp = _mcp_call("read_cell", {
            "file_path": task.starting_files[0],
            "sheet_name": "Sheet1",
            "cell_reference": "A1",
        })
        value = resp.get("result", {}).get("value")
        _check(f"read_cell A1 = '{value}'", value == "Period")
    except Exception as e:
        _check(f"read_cell ({e})", False)

    # 5. Read range
    try:
        resp = _mcp_call("read_range", {
            "file_path": task.starting_files[0],
            "sheet_name": "Sheet1",
            "start_cell": "A1",
            "end_cell": "C3",
        })
        rows = resp.get("result", {}).get("values", [])
        _check(f"read_range returns {len(rows)} rows", len(rows) == 3)
    except Exception as e:
        _check(f"read_range ({e})", False)

    # 6. Get sheet info
    try:
        resp = _mcp_call("get_sheet_info", {
            "file_path": task.starting_files[0],
            "sheet_name": "Sheet1",
        })
        row_count = resp.get("result", {}).get("row_count", 0)
        _check(f"Sheet has {row_count} rows (expected 101)", row_count == 101)
    except Exception as e:
        _check(f"get_sheet_info ({e})", False)

    # 7. Write cell
    try:
        resp = _mcp_call("write_cell", {
            "file_path": task.starting_files[0],
            "sheet_name": "Sheet1",
            "cell_reference": "D1",
            "value": "Required_Reserve",
        })
        _check("write_cell D1 header", resp.get("success") is True)
    except Exception as e:
        _check(f"write_cell ({e})", False)

    # 8. Take screenshot (Mode 1) — give Xvfb + LibreOffice time to start
    time.sleep(3)
    try:
        resp = _mcp_call("take_screenshot")
        img_len = len(resp.get("result", {}).get("image_base64", ""))
        width = resp.get("result", {}).get("width", 0)
        _check(f"take_screenshot ({width}px wide, {img_len} chars)", img_len > 1000)
    except Exception as e:
        _check(f"take_screenshot ({e})", False)

    # 9. Click (Mode 1)
    try:
        resp = _mcp_call("click", {"x": 400, "y": 300})
        _check("click(400, 300)", resp.get("success") is True)
    except Exception as e:
        _check(f"click ({e})", False)

    # 10. Task lifecycle
    try:
        payload = json.dumps({"task_id": "demo", "timeout_seconds": 300}).encode()
        req = Request(
            f"{MCP_BASE_URL}/task/start",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=10) as resp:
            task_resp = json.loads(resp.read())
        _check("task/start", task_resp.get("status") == "running")

        status = _mcp_get("/task/status")
        _check("task/status shows running", status.get("status") == "running")
    except Exception as e:
        _check(f"task lifecycle ({e})", False)

    print(f"\n[DEMO] Results: {checks_passed}/{checks_total} checks passed")
    return checks_passed == checks_total


def extract_output(task_name: str) -> Path:
    """Copy output files from the shared workspace to the output directory."""
    print("\n[EXTRACT] Extracting output files...")
    task_output = OUTPUT_DIR / task_name
    task_output.mkdir(parents=True, exist_ok=True)

    for f in SHARED_DIR.iterdir():
        if f.is_file() and not f.name.startswith("."):
            dst = task_output / f.name
            shutil.copy2(f, dst)
            print(f"  Extracted {f.name} → {dst}")

    return task_output


def run_grading_direct(task_name: str, agent_path: str, oracle_path: str) -> tuple[float | None, str | None]:
    """Run grading with explicit file paths."""
    print(f"\n[GRADE] Comparing:\n  Agent:  {agent_path}\n  Oracle: {oracle_path}")
    grade_script = TASKS_DIR / task_name / "grade.py"
    result = _run_cmd(
        [sys.executable, str(grade_script), agent_path, oracle_path],
        check=False,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    for line in result.stdout.split("\n"):
        if "Score:" in line:
            try:
                score_str = line.split("Score:")[1].strip().rstrip("%")
                return float(score_str) / 100, "completed"
            except (ValueError, IndexError):
                pass

    return None, "Could not parse grading output"


def run_grading(task_name: str, output_dir: Path) -> tuple[float | None, str | None]:
    """Run the grading script for the task."""
    print("\n[GRADE] Running grading script...")
    task = load_task(task_name)
    agent_output = output_dir / task.starting_files[0]
    oracle = TASKS_DIR / task_name / "oracle" / task.oracle_file

    if not agent_output.exists():
        print(f"[GRADE] Agent output not found: {agent_output}")
        return None, "Agent output file missing"

    if not oracle.exists():
        print(f"[GRADE] Oracle file not found: {oracle}")
        return None, "Oracle file missing"

    grade_script = TASKS_DIR / task_name / "grade.py"
    result = _run_cmd(
        [sys.executable, str(grade_script), str(agent_output), str(oracle)],
        check=False,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    for line in result.stdout.split("\n"):
        if "Score:" in line:
            try:
                score_str = line.split("Score:")[1].strip().rstrip("%")
                return float(score_str) / 100, "completed"
            except (ValueError, IndexError):
                pass

    return None, "Could not parse grading output"


def run_task(task_name: str, demo: bool = False) -> RunResult:
    """Execute the full sandbox lifecycle for a task."""
    start_time = time.time()
    result = RunResult(task_id=task_name)

    try:
        task = load_task(task_name)
        print(f"\n{'='*60}")
        print(f"TASK: {task.title}")
        print(f"{'='*60}")
        print(f"Description: {task.description}")

        prepare_workspace(task_name, task)
        result.files_copied = True

        if not start_container(task):
            result.error = "Failed to start container"
            return result
        result.container_started = True

        if demo:
            all_passed = run_demo_checks(task)
            result.agent_completed = all_passed
        else:
            agent_done = wait_for_agent(task)
            result.agent_completed = agent_done

        output_dir = extract_output(task_name)

        if demo:
            print("\n[GRADE] Demo mode: grading oracle against itself to verify grader...")
            oracle_path = TASKS_DIR / task_name / "oracle" / task.oracle_file
            score, status = run_grading_direct(task_name, str(oracle_path), str(oracle_path))
        else:
            score, status = run_grading(task_name, output_dir)
        result.grading_score = score
        result.grading_status = status

    except Exception as exc:
        result.error = str(exc)
    finally:
        result.elapsed_seconds = round(time.time() - start_time, 2)
        clean_container()

    print(f"\n{'='*60}")
    print(f"RESULT: {result.model_dump_json(indent=2)}")
    print(f"{'='*60}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Sandbox lifecycle manager")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Run a full task lifecycle")
    run_parser.add_argument("--task", required=True, help="Task directory name")
    run_parser.add_argument(
        "--demo",
        action="store_true",
        help="Run automated MCP checks instead of waiting for an agent",
    )

    sub.add_parser("build", help="Build the Docker image")
    sub.add_parser("clean", help="Remove the sandbox container")

    args = parser.parse_args()

    if args.command == "build":
        build_image()
    elif args.command == "clean":
        clean_container()
    elif args.command == "run":
        run_task(args.task, demo=args.demo)


if __name__ == "__main__":
    main()
