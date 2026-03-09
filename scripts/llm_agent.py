"""
LLM-powered agent that solves spreadsheet tasks via MCP.

Uses GPT-4o-mini to autonomously discover tools, read data, compute
answers, and write results — all through the MCP server API.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.request import Request, urlopen

from openai import OpenAI

MCP_URL = "http://localhost:8080"
MAX_TURNS = 20

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


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


def mcp_get(path: str) -> dict | list:
    req = Request(f"{MCP_URL}{path}")
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def build_openai_tools(mcp_tools: list[dict]) -> list[dict]:
    """Convert MCP tool definitions to OpenAI function-calling format."""
    openai_tools = []
    for t in mcp_tools:
        properties = {}
        required = []
        for p in t["parameters"]:
            properties[p["name"]] = {
                "type": p["type"] if p["type"] != "array" else "array",
                "description": p["description"],
            }
            if p["type"] == "array":
                properties[p["name"]]["items"] = {"type": "string"}
            if p.get("required", True):
                required.append(p["name"])
        openai_tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
    return openai_tools


SYSTEM_PROMPT = """You are an AI agent working inside a sandboxed environment with a spreadsheet.
Your task: Using the provided cash flow spreadsheet, calculate the required reserves for each
period using linear interpolation between known rate points. Add a new column 'Required_Reserve'
that applies the interpolated reserve rate to each period's cash flow amount.

Use the MCP tools to interact with the spreadsheet:
1. list_files to find the spreadsheet
2. get_sheet_info to understand the structure  
3. read_range to read ALL the data in one call
4. Compute the interpolated rates yourself — for each period, find the two nearest known
   rate points and linearly interpolate. Required_Reserve = cash_flow * interpolated_rate.
   Round to 2 decimal places.
5. write_cell to write the "Required_Reserve" header in D1
6. write_range to write ALL computed values in one call starting at D2.
   The values param must be a 2D array like [[val1],[val2],...].
7. read_range to verify a few results
8. Say TASK_COMPLETE when done

Be efficient. Read and write in bulk using ranges, not individual cells.
When you're finished writing all results, say TASK_COMPLETE in your message."""


def main() -> None:
    print("=== LLM Agent: Banking Reserve Calculation ===\n")

    mcp_post("/task/start", {"task_id": "banking_reserve", "timeout_seconds": 300})

    mcp_tools = mcp_get("/tools")
    openai_tools = build_openai_tools(mcp_tools)
    print(f"[init] Discovered {len(mcp_tools)} MCP tools")

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": "Start the task. The spreadsheet is in /workspace."})

    for turn in range(MAX_TURNS):
        print(f"\n--- Turn {turn + 1} ---")

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=openai_tools,
            tool_choice="auto",
            temperature=0,
        )

        msg = response.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if msg.content:
            print(f"[llm] {msg.content[:200]}")

        if not msg.tool_calls:
            if msg.content and "TASK_COMPLETE" in msg.content:
                print("\n[agent] Task complete signal received.")
                mcp_post("/task/complete", {})
                break
            print("[agent] No tool calls, ending.")
            break

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            fn_args = json.loads(tc.function.arguments)
            print(f"[tool] {fn_name}({json.dumps(fn_args)[:120]})")

            try:
                result = mcp_call(fn_name, fn_args)
                result_str = json.dumps(result)
                if len(result_str) > 2000:
                    result_str = result_str[:2000] + "...(truncated)"
                print(f"  → success={result.get('success')}")
            except Exception as e:
                result_str = json.dumps({"error": str(e)})
                print(f"  → error: {e}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str,
            })

    print("\n=== LLM Agent finished! ===")


if __name__ == "__main__":
    main()
