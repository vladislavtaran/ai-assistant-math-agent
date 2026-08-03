#!/usr/bin/env python3
"""MCP server exposing this project's deterministic tools.

Wraps the same exact-computation tools the web assistant uses — SymPy math,
timezone-aware date/time, and subnet/DNS helpers — as Model Context Protocol
tools, so any MCP client (Claude Desktop, Claude Code, Cursor, …) can call them.

Only deterministic, self-contained tools are exposed: no LLM, no API key, no
network quota — the compute is local and free. (The RAG `portfolio_search` tool
is intentionally left out; it needs the embeddings API.)

Transports:
  * stdio (default)          — for local use by an MCP client
  * streamable-http (hosted) — set MCP_HTTP=1 to serve on 127.0.0.1:$MCP_PORT/mcp
"""
import os
import re
import sys
import socket
import ipaddress
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from mcp.server import MCPServer

# reuse the project's SymPy math agent (imported in-process; needs sympy).
# Look for mathagent.py in: $MATH_AGENT_DIR, the repo's server/ (dev layout),
# then next to this file (deploy copies it alongside).
_here = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.environ.get("MATH_AGENT_DIR"),
              os.path.join(os.path.dirname(_here), "server"), _here):
    if _cand and os.path.exists(os.path.join(_cand, "mathagent.py")):
        sys.path.insert(0, _cand)
        break
import mathagent  # noqa: E402

server = MCPServer(
    name="chrome-net-ua-tools",
    version="1.0.0",
    instructions=(
        "Deterministic tools from vladyslav.taran's assistant (chrome.net.ua): "
        "exact mathematics (SymPy), the current time in any timezone, and subnet "
        "(CIDR) / DNS helpers. Results are computed locally, never guessed."
    ),
)


# ---- math (SymPy) ---------------------------------------------------------
def _nice(s: str) -> str:
    return str(s).replace("**", "^")


def _format_math(t: dict) -> str:
    if not t.get("ok"):
        return "could not compute (%s)" % t.get("error", "error")
    op = t.get("operation")
    if op == "solve":
        var, sols = t.get("variable", "x"), t.get("solutions", [])
        body = "no solution" if not sols else "; ".join("%s = %s" % (var, _nice(s)) for s in sols)
        ap = t.get("approx", [])
        extra = "  (≈ %s)" % ", ".join(ap) if ap and ap != t.get("solutions") else ""
        return "%s  →  %s%s" % (_nice(t.get("equation", "")), body, extra)
    if op == "evaluate":
        exact, num = _nice(t.get("exact", "")), t.get("numeric")
        extra = "  ≈ %s" % num if num and num != t.get("exact") else ""
        return "%s = %s%s" % (_nice(t.get("input", "")), exact, extra)
    if op == "differentiate":
        return "d/d%s ( %s ) = %s" % (t.get("variable", "x"), _nice(t.get("input", "")), _nice(t.get("result", "")))
    if op == "integrate":
        return "∫ %s d%s = %s" % (_nice(t.get("input", "")), t.get("variable", "x"), _nice(t.get("result", "")))
    return "%s → %s" % (_nice(t.get("input", "")), _nice(t.get("result", "")))


@server.tool()
def math_solve(operation: str, expression: str, variable: str = "") -> str:
    """Do exact mathematics with SymPy (computed, never guessed).

    operation: one of solve, evaluate, differentiate, integrate, simplify, factor, expand.
    expression: the expression; use * for multiply and ^ for power, keep = for equations.
    variable: optional variable name, e.g. x.
    """
    try:
        result = mathagent.run(operation, expression, variable or None)
        result["ok"] = True
    except Exception as e:
        result = {"ok": False, "error": str(e)[:200]}
    return _format_math(result)


# ---- date / time (zoneinfo) ----------------------------------------------
_TZ_ALIASES = {
    "utc": "UTC", "gmt": "UTC", "kyiv": "Europe/Kyiv", "kiev": "Europe/Kyiv",
    "london": "Europe/London", "uk": "Europe/London", "new york": "America/New_York",
    "nyc": "America/New_York", "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles",
    "tokyo": "Asia/Tokyo", "berlin": "Europe/Berlin", "paris": "Europe/Paris",
    "sydney": "Australia/Sydney", "india": "Asia/Kolkata",
}


@server.tool()
def datetime_now(timezone_name: str = "UTC") -> str:
    """Current date and time in a given timezone (IANA name or common city).

    timezone_name: an IANA zone or common city, e.g. Europe/Kyiv, Asia/Tokyo, UTC.
    """
    name = _TZ_ALIASES.get((timezone_name or "UTC").strip().lower(), (timezone_name or "UTC").strip())
    try:
        tzinfo = ZoneInfo(name)
    except Exception:
        return 'unknown timezone "%s" — use an IANA name like Europe/Kyiv' % timezone_name
    now = datetime.now(tzinfo)
    off = now.strftime("%z")
    off = "%s:%s" % (off[:3], off[3:]) if off else ""
    return "%s in %s (UTC%s)" % (now.strftime("%A, %d %B %Y, %H:%M:%S"), name, off)


# ---- network (ipaddress / socket, read-only) -----------------------------
@server.tool()
def network_cidr(value: str) -> str:
    """Subnet (CIDR) arithmetic: network, broadcast, mask, host count, range.

    value: a network in CIDR notation, e.g. 192.168.1.0/26 or 10.0.0.0/8.
    """
    try:
        net = ipaddress.ip_network(value.strip(), strict=False)
    except Exception as e:
        return "invalid network: %s" % (str(e)[:120])
    hosts = net.num_addresses
    usable = max(hosts - 2, 0) if net.version == 4 and net.prefixlen < 31 else hosts
    return ("%s → network %s, broadcast %s, netmask %s, %d addresses (%d usable), range %s–%s"
            % (net.with_prefixlen, net.network_address, net[-1], net.netmask,
               hosts, usable, net[0], net[-1]))


@server.tool()
def network_dns(hostname: str) -> str:
    """Resolve a hostname to its A / AAAA (IPv4 / IPv6) addresses (read-only lookup).

    hostname: a domain name, e.g. example.com.
    """
    host = (hostname or "").strip()
    if not host or "/" in host or " " in host:
        return "invalid hostname"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return "DNS lookup failed for %s (%s)" % (host, e)
    addrs = sorted({i[4][0] for i in infos})
    v4 = [a for a in addrs if ":" not in a]
    v6 = [a for a in addrs if ":" in a]
    parts = []
    if v4:
        parts.append("A: " + ", ".join(v4))
    if v6:
        parts.append("AAAA: " + ", ".join(v6))
    return "%s → %s" % (host, "; ".join(parts) or "no records")


def main():
    if os.environ.get("MCP_HTTP") == "1":
        import anyio
        from mcp.server.transport_security import TransportSecuritySettings
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("MCP_PORT", "8095"))
        # When served behind a reverse proxy the Host header is the public domain,
        # which the default DNS-rebinding protection (localhost-only) would reject
        # with 421. Allow the configured public host(s)/origin(s) explicitly.
        sec = None
        hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
        origins = [o.strip() for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]
        if hosts or origins:
            sec = TransportSecuritySettings(allowed_hosts=hosts or ["*"],
                                            allowed_origins=origins or ["*"])
        anyio.run(lambda: server.run_streamable_http_async(
            host=host, port=port, streamable_http_path="/mcp", transport_security=sec))
    else:
        server.run()  # stdio


if __name__ == "__main__":
    main()
