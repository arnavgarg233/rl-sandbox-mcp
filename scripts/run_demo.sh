#!/bin/bash
set -e
cd "$(dirname "$0")/.."
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"

echo ""
echo "============================================================"
echo "  MCP Sandbox — End-to-End Demo"
echo "============================================================"
echo ""

echo "[1/5] Building Docker image..."
docker compose build --quiet
echo "    Done."
echo ""

echo "[2/5] Running sandbox lifecycle (17 automated tool checks)..."
echo ""
python3 scripts/sandbox_manager.py run --task banking_reserve --demo
echo ""

echo "[3/5] Starting fresh container for demo agent..."
cp tasks/banking_reserve/starting_files/cash_flows.ods shared/
docker compose up -d 2>&1 | grep -v "^$"
echo "    Waiting for MCP server..."
for i in $(seq 1 15); do
    if curl -s http://localhost:8080/health > /dev/null 2>&1; then
        echo "    MCP server is ready."
        break
    fi
    sleep 2
done
echo ""

echo "[4/5] Demo agent solving task via MCP..."
echo ""
python3 scripts/demo_agent.py
echo ""

echo "[5/5] Grading agent output against oracle..."
echo ""
python3 tasks/banking_reserve/grade.py shared/cash_flows.ods tasks/banking_reserve/oracle/expected_reserves.ods
echo ""

echo "Cleaning up..."
docker compose down 2>&1 | grep -v "^$"
echo ""
echo "============================================================"
echo "  Demo complete."
echo "============================================================"
echo ""
