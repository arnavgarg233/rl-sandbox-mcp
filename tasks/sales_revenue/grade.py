"""
Grading script for the sales revenue calculation task.

Usage:
    python grade.py <agent_output_path> <oracle_path>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "mcp_server"))

from grader import GradingConfig, grade_spreadsheet


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python grade.py <agent_output> <oracle_file>")
        sys.exit(1)

    agent_output = str(Path(sys.argv[1]).resolve())
    oracle_file = str(Path(sys.argv[2]).resolve())

    task_json = Path(__file__).parent / "task.json"
    config = GradingConfig()
    if task_json.exists():
        task_data = json.loads(task_json.read_text())
        grading_cfg = task_data.get("grading", {})
        config = GradingConfig(**grading_cfg)

    result = grade_spreadsheet(agent_output, oracle_file, config)

    print("=" * 60)
    print(f"GRADING RESULT: {result.status.value.upper()}")
    print(f"Score: {result.score:.2%}")
    print(f"Matched: {result.matched_cells}/{result.total_cells} cells")
    print("=" * 60)

    if result.mismatches:
        print(f"\nFirst {len(result.mismatches)} mismatches:")
        for m in result.mismatches[:10]:
            print(f"  {m.cell}: expected={m.expected}, got={m.actual} ({m.reason})")

    print(f"\n{result.notes}")


if __name__ == "__main__":
    main()
