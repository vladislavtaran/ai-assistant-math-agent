# MCP server — deterministic tools

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes
this project's **deterministic** tools to any MCP client (Claude Desktop, Claude
Code, Cursor, …). It's the same exact-computation logic the web assistant uses,
made reusable over MCP.

**Tools:**

| Tool | What it does |
|------|--------------|
| `math_solve` | exact math via SymPy — solve, evaluate, differentiate, integrate, simplify, factor, expand |
| `datetime_now` | current date/time in any timezone (IANA name or common city) |
| `network_cidr` | subnet arithmetic — network, broadcast, mask, host count, range |
| `network_dns` | resolve a hostname to its A / AAAA addresses (read-only) |

These tools use **no LLM, no API key, and no network quota** — the compute is
local and free, so you can run this yourself at no cost. (The web assistant's
RAG `portfolio_search` tool is intentionally *not* exposed here, because it needs
the embeddings API.)

## Run it locally (stdio)

```bash
cd mcp-server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# quick check (lists tools, then Ctrl-C):
.venv/bin/python server.py    # speaks MCP over stdio
```

### Add it to Claude Desktop
Edit your `claude_desktop_config.json` (Settings → Developer → Edit Config) and add:

```json
{
  "mcpServers": {
    "chrome-net-ua-tools": {
      "command": "/ABSOLUTE/PATH/TO/mcp-server/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/TO/mcp-server/server.py"]
    }
  }
}
```

Restart Claude Desktop — the four tools appear under the 🔌 menu. Ask it e.g.
*"what's 48273 × 19847?"* or *"what time is it in Tokyo?"* and it will call them.

### Add it to Claude Code
```bash
claude mcp add chrome-net-ua-tools -- /ABSOLUTE/PATH/TO/mcp-server/.venv/bin/python /ABSOLUTE/PATH/TO/mcp-server/server.py
```

## Run it over HTTP (hosted)

The same server speaks the **Streamable HTTP** transport when `MCP_HTTP=1`:

```bash
MCP_HTTP=1 MCP_PORT=8095 .venv/bin/python server.py
# MCP endpoint: http://127.0.0.1:8095/mcp
```

The live demo (chrome.net.ua) runs this behind nginx as a systemd service
(`mcp.service`), but the public endpoint is **access-restricted** — it's a
deployed reference, not an open service. To use the tools yourself, run the
stdio server locally as above (it costs nothing).

## Notes

- Requires Python 3.10+. `math_solve` reuses `../server/mathagent.py` (SymPy); the
  deploy copies that file next to `server.py`, and `MATH_AGENT_DIR` can point at
  it explicitly.
- Everything is read-only and side-effect-free.
