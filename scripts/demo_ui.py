"""
Visual demo dashboard for the MCP Sandbox environment.
Run: streamlit run scripts/demo_ui.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

import pandas as pd
import streamlit as st
from urllib.request import Request, urlopen

MCP_URL = "http://localhost:8080"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

st.set_page_config(page_title="MCP Sandbox Demo", layout="wide", page_icon="🔒")

st.markdown("""
<style>
    .block-container { padding-top: 0.5rem; padding-bottom: 0rem; }
    /* tool call log */
    .tool-log { max-height: 70vh; overflow-y: auto; padding: 4px; }
    .tc { padding: 5px 10px; margin: 3px 0; border-radius: 3px; font-family: monospace;
          font-size: 0.85rem; color: #1a1a1a; border-left: 4px solid #888; background: #f8f9fa; }
    .tc-r { border-left-color: #2196F3; }
    .tc-w { border-left-color: #e53935; }
    .tc-x { border-left-color: #f9a825; }
    .tc-ai { border-left-color: #43a047; }
    .grade-pass { background: #e8f5e9; border: 1px solid #43a047; padding: 8px 12px; border-radius: 6px;
                  color: #1a1a1a; font-size: 0.9rem; margin-top: 6px; }
    .grade-other { background: #fff8e1; border: 1px solid #f9a825; padding: 8px 12px; border-radius: 6px;
                   color: #1a1a1a; font-size: 0.9rem; margin-top: 6px; }
    .stButton > button { font-size: 0.85rem !important; }
    /* make st.table bigger */
    .stTable table { font-size: 14px !important; }
    .stTable th { font-size: 14px !important; }
    .stTable td { font-size: 13px !important; }
</style>
""", unsafe_allow_html=True)


def mcp_call(tool_name: str, arguments: dict | None = None) -> dict:
    payload = json.dumps({"tool_name": tool_name, "arguments": arguments or {}}).encode()
    req = Request(f"{MCP_URL}/tools/call", data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def mcp_post(path: str, body: dict) -> dict:
    payload = json.dumps(body).encode()
    req = Request(f"{MCP_URL}{path}", data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def mcp_get(path: str):
    req = Request(f"{MCP_URL}{path}")
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def check_server():
    try:
        return mcp_get("/health").get("status") == "ok"
    except Exception:
        return False


def read_spreadsheet_df() -> pd.DataFrame:
    """Read first 20 data rows as a DataFrame."""
    try:
        info = mcp_call("get_sheet_info", {"file_path": "cash_flows.ods", "sheet_name": "Sheet1"})["result"]
        rows = info["row_count"]
        if rows == 0:
            return pd.DataFrame()
        end_row = min(rows, 21)
        data = mcp_call("read_range", {
            "file_path": "cash_flows.ods", "sheet_name": "Sheet1",
            "start_cell": "A1", "end_cell": f"D{end_row}",
        })["result"]["values"]
        if not data:
            return pd.DataFrame()
        headers = data[0]
        body = data[1:]
        has_d = len(headers) > 3 and headers[3]
        ncols = 4 if has_d else 3
        out_headers = [headers[i] if i < len(headers) and headers[i] else f"Col{i}" for i in range(ncols)]
        out_body = []
        for row in body:
            r = [row[i] if i < len(row) and row[i] is not None else "" for i in range(ncols)]
            out_body.append(r)
        return pd.DataFrame(out_body, columns=out_headers)
    except Exception:
        return pd.DataFrame()


def reset_workspace():
    subprocess.run(["docker", "compose", "down"], capture_output=True, cwd=PROJECT_ROOT)
    shutil.copy("tasks/banking_reserve/starting_files/cash_flows.ods", "shared/cash_flows.ods")
    subprocess.run(["docker", "compose", "up", "-d"], capture_output=True, cwd=PROJECT_ROOT)
    for _ in range(15):
        time.sleep(2)
        if check_server():
            return True
    return False


def reset_spreadsheet_only():
    """Copy fresh spreadsheet without restarting the container."""
    shutil.copy("tasks/banking_reserve/starting_files/cash_flows.ods", "shared/cash_flows.ods")


def tc(tool_name: str, args_str: str, status: str = "success") -> str:
    if "read" in tool_name or "list" in tool_name or "get" in tool_name:
        cls = "tc-r"
    elif "write" in tool_name or "set" in tool_name:
        cls = "tc-w"
    elif "execute" in tool_name:
        cls = "tc-x"
    else:
        cls = "tc-r"
    s = "[OK]" if status == "success" else "[ERR]"
    short_args = f"({args_str})" if args_str else ""
    return f'<div class="tc {cls}"><code>{tool_name}</code>{short_args} <b>{s}</b></div>'


def ai_msg(text: str) -> str:
    return f'<div class="tc tc-ai"><b>GPT-4o:</b> {text}</div>'


SCROLL_JS = '<script>var els=document.querySelectorAll(".tool-log");els.forEach(function(e){e.scrollTop=e.scrollHeight;});</script>'


def run_scripted_agent(sheet_area, log_area):
    logs: list[str] = []

    def refresh():
        df = read_spreadsheet_df()
        if not df.empty:
            sheet_area.table(df)

    def log(html: str):
        logs.append(html)
        log_area.markdown('<div class="tool-log">' + "".join(logs) + "</div>" + SCROLL_JS, unsafe_allow_html=True)

    reset_spreadsheet_only()
    time.sleep(0.3)
    refresh()
    time.sleep(0.5)

    log(tc("POST /task/start", "banking_reserve"))
    mcp_post("/task/start", {"task_id": "banking_reserve", "timeout_seconds": 300})
    time.sleep(0.3)

    log(tc("list_files", ""))
    files = mcp_call("list_files")["result"]["files"]
    spreadsheet = [f for f in files if f.endswith(".ods") and not f.startswith(".")][0]
    time.sleep(0.3)

    log(tc("get_sheet_info", 'sheet="Sheet1"'))
    info = mcp_call("get_sheet_info", {"file_path": spreadsheet, "sheet_name": "Sheet1"})["result"]
    time.sleep(0.3)

    log(tc("read_range", f'A1:C{info["row_count"]}'))
    data = mcp_call("read_range", {
        "file_path": spreadsheet, "sheet_name": "Sheet1",
        "start_cell": "A1", "end_cell": f"C{info['row_count']}",
    })["result"]["values"]
    rows = data[1:]
    time.sleep(0.3)

    periods, cash_flows, known_rates = [], [], {}
    for row in rows:
        if row[0] is None:
            continue
        p = int(float(row[0]))
        periods.append(p)
        cash_flows.append(float(row[1]))
        if row[2] is not None and row[2] != "" and row[2] != "None":
            known_rates[p] = float(row[2])
    sorted_known = sorted(known_rates.keys())

    def interpolate(period):
        if period in known_rates:
            return known_rates[period]
        below = max((p for p in sorted_known if p < period), default=None)
        above = min((p for p in sorted_known if p > period), default=None)
        if below is None:
            return known_rates[above]
        if above is None:
            return known_rates[below]
        frac = (period - below) / (above - below)
        return known_rates[below] + frac * (known_rates[above] - known_rates[below])

    reserves = [round(cash_flows[i] * interpolate(periods[i]), 2) for i in range(len(periods))]
    log(f'<div class="tc" style="border-left-color:#6366f1;">⚙️ Computed {len(reserves)} interpolated reserves</div>')
    time.sleep(0.3)

    log(tc("write_cell", 'D1="Required_Reserve"'))
    mcp_call("write_cell", {"file_path": spreadsheet, "sheet_name": "Sheet1", "cell_reference": "D1", "value": "Required_Reserve"})
    time.sleep(0.5)
    refresh()
    time.sleep(1.0)

    log(tc("write_range", f'D2:D{len(reserves)+1} — {len(reserves)} values'))
    mcp_call("write_range", {"file_path": spreadsheet, "sheet_name": "Sheet1", "start_cell": "D2", "values": [[r] for r in reserves]})
    time.sleep(0.5)
    refresh()
    time.sleep(1.0)

    log(tc("read_range", "D2:D6 — verify"))
    time.sleep(0.3)

    log(tc("POST /task/complete", ""))
    mcp_post("/task/complete", {})

    return grade_output()


def run_llm_agent(sheet_area, log_area):
    from openai import OpenAI
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    logs: list[str] = []

    def refresh():
        df = read_spreadsheet_df()
        if not df.empty:
            sheet_area.table(df)

    def log(html: str):
        logs.append(html)
        log_area.markdown('<div class="tool-log">' + "".join(logs) + "</div>" + SCROLL_JS, unsafe_allow_html=True)

    reset_spreadsheet_only()
    time.sleep(0.3)
    refresh()
    time.sleep(0.5)

    log(tc("POST /task/start", "banking_reserve"))
    mcp_post("/task/start", {"task_id": "banking_reserve_llm", "timeout_seconds": 300})

    mcp_tools = mcp_get("/tools")
    openai_tools = []
    for t in mcp_tools:
        props, req_list = {}, []
        for p in t["parameters"]:
            props[p["name"]] = {"type": p["type"] if p["type"] != "array" else "array", "description": p["description"]}
            if p["type"] == "array":
                props[p["name"]]["items"] = {"type": "string"}
            if p.get("required", True):
                req_list.append(p["name"])
        openai_tools.append({"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": {"type": "object", "properties": props, "required": req_list},
        }})

    system = """You are an AI agent in a sandboxed MCP environment.

TASK: Add column "Required_Reserve" to a spreadsheet.
  Required_Reserve = Cash_Flow_Amount * interpolated_rate, rounded to 2 decimals.
Rate_Point has known rates for some rows, empty for others. Interpolate linearly.

Steps — call one tool per step, no long explanations:
1. list_files
2. get_sheet_info (sheet_name="Sheet1")
3. read_range (A1 through C101)
4. write_cell: D1 = "Required_Reserve"
5. execute_python — use this exact script (replace FILENAME with the actual .ods filename):

import pandas as pd
import numpy as np
df = pd.read_excel('/workspace/FILENAME', engine='odf')
df['Rate_Point'] = pd.to_numeric(df['Rate_Point'], errors='coerce')
df['Rate_Point'] = df['Rate_Point'].interpolate(method='index')
df['Rate_Point'] = df['Rate_Point'].bfill().ffill()
df['Required_Reserve'] = (df['Cash_Flow_Amount'] * df['Rate_Point']).round(2)
df.to_excel('/workspace/FILENAME', engine='odf', index=False)
print(f"Done: {len(df)} rows written")

6. Say TASK_COMPLETE

CRITICAL: Do NOT explain your plan. Be brief. Call tools immediately."""

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": "Start. The spreadsheet is in /workspace."}]

    for turn in range(15):
        resp = client.chat.completions.create(
            model="gpt-4o", messages=messages, tools=openai_tools,
            tool_choice="auto", temperature=0,
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if msg.content:
            text = msg.content[:200].replace("<", "&lt;").replace(">", "&gt;")
            log(ai_msg(text))

        if not msg.tool_calls:
            if msg.content and "TASK_COMPLETE" in msg.content:
                log(tc("POST /task/complete", ""))
                mcp_post("/task/complete", {})
            break

        for tool_call in msg.tool_calls:
            fn = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            args_short = json.dumps(args)[:80].replace("<", "&lt;")

            try:
                result = mcp_call(fn, args)
                result_str = json.dumps(result)
                if len(result_str) > 2000:
                    result_str = result_str[:2000] + "..."
                ok = "success" if result.get("success") else "failed"
                log(tc(fn, args_short, ok))
            except Exception as e:
                result_str = json.dumps({"error": str(e)})
                log(tc(fn, args_short, "failed"))

            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result_str})

            time.sleep(0.3)
            refresh()
            time.sleep(0.3)

    refresh()
    return grade_output()


def grade_output():
    result = subprocess.run(
        [sys.executable, "tasks/banking_reserve/grade.py",
         "shared/cash_flows.ods", "tasks/banking_reserve/oracle/expected_reserves.ods"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    score, matched, status = "?", "?", "?"
    for line in result.stdout.strip().split("\n"):
        if "Score:" in line:
            score = line.split("Score:")[1].strip()
        elif "Matched:" in line:
            matched = line.split("Matched:")[1].strip()
        elif "GRADING RESULT:" in line:
            status = line.split("GRADING RESULT:")[1].strip()
    return score, matched, status


# ─── UI ───────────────────────────────────────────────────────────────

server_ok = check_server()

st.markdown("#### MCP Sandbox — Agent Demo &nbsp; " + ("🟢" if server_ok else "🔴"))

if not server_ok:
    st.error("MCP Server is offline. Run `docker compose up -d`.")
    st.stop()

if st.sidebar.button("🔄 Reset Workspace"):
    with st.spinner("Restarting container..."):
        reset_workspace()
    st.rerun()

st.sidebar.caption("17 MCP tools · iptables isolation · cap_drop ALL · non-root · auto-graded")

tab1, tab2 = st.tabs(["Scripted Agent (Python)", "LLM Agent (GPT-4o)"])

with tab1:
    left, right = st.columns([3, 2])
    with left:
        st.caption("Spreadsheet — cash_flows.ods (first 20 rows)")
        sheet_1 = st.empty()
        df = read_spreadsheet_df()
        if not df.empty:
            sheet_1.table(df)
    with right:
        st.caption("MCP Tool Calls")
        log_1 = st.empty()

    if st.button("▶ Run Scripted Agent", key="scripted", type="primary"):
        score, matched, status = run_scripted_agent(sheet_1, log_1)
        cls = "grade-pass" if status == "PASS" else "grade-other"
        st.markdown(f'<div class="{cls}"><b>GRADE: {status}</b> — Score: {score} — Cells: {matched}</div>', unsafe_allow_html=True)

with tab2:
    left, right = st.columns([3, 2])
    with left:
        st.caption("Spreadsheet — cash_flows.ods (first 20 rows)")
        sheet_2 = st.empty()
        df = read_spreadsheet_df()
        if not df.empty:
            sheet_2.table(df)
    with right:
        st.caption("GPT-4o → MCP Tool Calls")
        log_2 = st.empty()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        st.caption("Set OPENAI_API_KEY env var to enable.")
    else:
        if st.button("▶ Run LLM Agent", key="llm", type="primary"):
            score, matched, status = run_llm_agent(sheet_2, log_2)
            cls = "grade-pass" if status == "PASS" else "grade-other"
            st.markdown(f'<div class="{cls}"><b>GRADE: {status}</b> — Score: {score} — Cells: {matched}</div>', unsafe_allow_html=True)
