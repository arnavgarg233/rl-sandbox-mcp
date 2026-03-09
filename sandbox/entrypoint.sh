#!/bin/bash
# ── Network isolation ─────────────────────────────────────────────
# Block all outbound traffic.  Only replies to inbound requests
# (ESTABLISHED/RELATED) are permitted, so the MCP server can respond
# to API calls but no process inside the sandbox can initiate
# connections to the internet.  This runs as root before we drop to
# the unprivileged sandbox user below.
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -j REJECT --reject-with icmp-net-unreachable

# ── Virtual display ───────────────────────────────────────────────
su sandbox -c "Xvfb :99 -screen 0 1280x720x24 &"
sleep 1

if [ -n "$LAUNCH_LIBREOFFICE" ]; then
    for f in /workspace/*.ods /workspace/*.xlsx /workspace/*.csv; do
        if [ -f "$f" ]; then
            su sandbox -c "soffice --calc '$f' &"
            break
        fi
    done
    sleep 3
fi

# ── MCP server (unprivileged) ─────────────────────────────────────
exec su sandbox -c "python3 -u /app/mcp_server.py"
