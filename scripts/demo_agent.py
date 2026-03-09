"""
Demo agent that solves the banking reserve task via MCP API.

This simulates what a real AI agent would do: connect to the MCP server,
read the spreadsheet, figure out the task, compute the answers, and write
them back. Uses only MCP tool calls — no direct file access.
"""
from __future__ import annotations

import json
from urllib.request import Request, urlopen

MCP_URL = "http://localhost:8080"


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


def main() -> None:
    print("=== Demo Agent: Banking Reserve Calculation ===\n")

    # Step 1: Start a task session
    print("[1] Starting task session...")
    mcp_post("/task/start", {"task_id": "banking_reserve", "timeout_seconds": 300})

    # Step 2: Discover what files are available
    print("[2] Listing workspace files...")
    files = mcp_call("list_files")["result"]["files"]
    spreadsheet = [f for f in files if f.endswith(".ods")][0]
    print(f"    Found: {spreadsheet}")

    # Step 3: Inspect the spreadsheet
    print("[3] Reading sheet info...")
    info = mcp_call("get_sheet_info", {"file_path": spreadsheet, "sheet_name": "Sheet1"})["result"]
    print(f"    {info['row_count']} rows, {info['col_count']} cols")
    print(f"    Headers: {info['headers']}")

    # Step 4: Read all the data
    row_count = info["row_count"]
    print(f"[4] Reading all {row_count - 1} data rows...")
    data = mcp_call("read_range", {
        "file_path": spreadsheet,
        "sheet_name": "Sheet1",
        "start_cell": "A1",
        "end_cell": f"C{row_count}",
    })["result"]["values"]

    headers = data[0]
    rows = data[1:]

    # Step 5: Parse data and find known rate points
    print("[5] Analyzing data...")
    periods: list[int] = []
    cash_flows: list[float] = []
    known_rates: dict[int, float] = {}

    for row in rows:
        period = int(float(row[0]))
        cf = float(row[1])
        rate = row[2]
        periods.append(period)
        cash_flows.append(cf)
        if rate is not None and rate != "" and rate != "None":
            known_rates[period] = float(rate)

    print(f"    {len(periods)} periods, {len(known_rates)} known rate points")
    print(f"    Known at periods: {sorted(known_rates.keys())}")

    # Step 6: Interpolate missing rates
    print("[6] Interpolating missing rates...")
    sorted_known = sorted(known_rates.keys())

    def interpolate(period: int) -> float:
        if period in known_rates:
            return known_rates[period]
        below = max((p for p in sorted_known if p < period), default=None)
        above = min((p for p in sorted_known if p > period), default=None)
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

    reserves: list[float] = []
    for i, period in enumerate(periods):
        rate = interpolate(period)
        reserve = round(cash_flows[i] * rate, 2)
        reserves.append(reserve)

    # Step 7: Write the header
    print("[7] Writing 'Required_Reserve' column header...")
    mcp_call("write_cell", {
        "file_path": spreadsheet,
        "sheet_name": "Sheet1",
        "cell_reference": "D1",
        "value": "Required_Reserve",
    })

    # Step 8: Write all reserve values
    print(f"[8] Writing {len(reserves)} reserve values...")
    values_2d = [[r] for r in reserves]
    mcp_call("write_range", {
        "file_path": spreadsheet,
        "sheet_name": "Sheet1",
        "start_cell": "D2",
        "values": values_2d,
    })

    # Step 9: Verify by reading back a few cells
    print("[9] Verifying written data...")
    check = mcp_call("read_range", {
        "file_path": spreadsheet,
        "sheet_name": "Sheet1",
        "start_cell": "A1",
        "end_cell": "D5",
    })["result"]["values"]
    for row in check:
        print(f"    {row}")

    # Step 10: Signal task complete
    print("[10] Signaling task completion...")
    mcp_post("/task/complete", {})

    print("\n=== Agent finished! ===")


if __name__ == "__main__":
    main()
