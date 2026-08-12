# Botasaurus MCP Container — Agent Guide

## What is this?

Docker container running Botasaurus (anti-detection web scraping framework) with MCP server interface. Enables AI agents to control a real Chromium browser with stealth capabilities.

## Purpose

- Bypass anti-bot systems (Cloudflare, Datadome, reCAPTCHA)
- Navigate, interact with, and extract data from websites
- Download files and take screenshots
- Execute JavaScript in page context
- Maintain persistent sessions via cookies

## Architecture

**Two services in one container:**
1. **MCP Server** (port 1405) — Primary interface for AI agents via SSE transport
2. **REST API + Web UI** (port 5846) — Debug interface and task history

**Core components:**
- `app.py` — Single Python file containing all logic
- FastMCP server with 13 browser control tools
- Botasaurus driver with `reuse_driver=True` (persistent browser session)
- Chromium 148 + chromedriver on Xvfb display :99
- Optional VNC access on port 6080 (when `ENABLE_VNC=true`)

## File Structure

```
botasaurus/
├── app.py                  # Main application (driver, MCP tools, REST server)
├── docker-compose.yml      # Container config (ports, volumes, env vars)
├── Dockerfile              # Build: Chromium + Python dependencies
├── entrypoint.sh           # Startup: Xvfb, optional VNC, then app.py
├── requirements.txt        # Python packages
└── README.md               # User documentation (Portuguese)
```

## app.py Structure

Single-file architecture. Key sections:

1. **Driver setup** (lines ~1-50)
   - `@browser(reuse_driver=True)` decorator
   - Profile: `hermes` (persistent cookies in `/app/profiles/`)
   - Config: `block_images=True`, `tiny_profile=True`

2. **Browser action dispatcher** (lines ~50-100)
   - `browser_action(driver, data)` routes MCP tool calls to driver methods
   - Handles: navigate, click, type, get_text, screenshot, scroll, wait, run_js, etc.

3. **MCP tools** (lines ~100-180)
   - 13 tools registered via `@mcp.tool()`
   - All return JSON strings
   - Tools: browser_navigate, browser_click, browser_type, browser_get_text, browser_get_all_text, browser_screenshot, browser_scroll, browser_wait, browser_run_js, browser_get_html, browser_get_url, browser_get_download, browser_close

4. **REST server** (lines ~180-192)
   - `@request` scraper for basic URL fetching
   - Registered via `Server.add_scraper(scrape_url)`

5. **Main entry** (lines ~194-199)
   - Starts botasaurus-server on port 5846 (background thread)
   - Starts FastMCP StreamableHTTP server on port 1405 (main thread)

## Stack

- **Python 3.11**
- **botasaurus** (anti-detection scraping framework)
- **mcp** (Model Context Protocol SDK, FastMCP server)
- **Chromium 148** + chromedriver
- **Xvfb** (virtual display)
- **Starlette/uvicorn** (SSE transport)

## Running

```bash
# Start container
docker compose up -d

# View logs
docker compose logs -f

# Test MCP connection
npx @modelcontextprotocol/inspector
# Connect to: http://localhost:1405/mcp
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ENABLE_VNC` | `false` | Enable x11vnc + noVNC on port 6080 |
| `PORT` | `5846` | REST server port |
| `TZ` | `America/Sao_Paulo` | Timezone |
| `DISPLAY` | `:99` | Xvfb display |

## Key Implementation Details

### Driver Persistence
- `reuse_driver=True` keeps Chromium open between tool calls
- First call: ~2-3s startup
- Subsequent calls: instant (reuses same browser)
- `browser_close()` closes browser; next call creates new one
- Cookies persist via `tiny_profile` in `/app/profiles/hermes/`

### Stealth Features
- `driver.google_get(url)` — navigates via Google referrer (simulates real user)
- Anti-detection techniques built into Botasaurus
- Non-headless mode (required for some anti-bot bypasses)
- Xvfb provides virtual display

### Downloads
- Chromium downloads to `/app/downloads/`
- `browser_get_download(timeout=30)` monitors folder for new files
- Ignores temp files (`.crdownload`, `.tmp`)
- Returns file as base64

### Screenshots
- `browser_screenshot()` returns PNG as base64
- Viewport: 1280x800
- Use `browser_scroll()` + screenshot for full page coverage

## MCP Tools Quick Reference

All tools return JSON strings. Agent must parse JSON.

| Tool | Params | Returns |
|------|--------|---------|
| `browser_navigate` | `url` | `{"current_url": "..."}` |
| `browser_click` | `selector` | `{"ok": true}` |
| `browser_type` | `selector`, `text` | `{"ok": true}` |
| `browser_get_text` | `selector` | `{"text": "..."}` or `{"text": "(empty)"}` |
| `browser_get_all_text` | `selector` | `{"count": N, "items": [...]}` |
| `browser_screenshot` | — | `{"screenshot_b64": "...", "mime": "image/png", "bytes": N}` |
| `browser_scroll` | `amount` (default: 500) | `{"ok": true}` |
| `browser_wait` | `selector`, `timeout` (default: 8) | `{"found": true/false}` |
| `browser_run_js` | `code` | `{"result": ...}` |
| `browser_get_html` | `max_length` (default: 10000) | `{"html": "...", "truncated": bool, "total_len": N}` |
| `browser_get_url` | — | `{"url": "..."}` |
| `browser_get_download` | `timeout` (default: 30) | `{"filename": "...", "content_b64": "..."}` or error |
| `browser_close` | — | `{"ok": true}` |

## Development Notes

### Modifying MCP Tools
- Add new tool: create function with `@mcp.tool()` decorator
- Tool function should call `browser_action(driver, (action_name, params))`
- Return value must be JSON-serializable (will be converted to JSON string)

### Modifying Driver Behavior
- Driver config in `@browser()` decorator (top of app.py)
- Key options: `reuse_driver`, `tiny_profile`, `block_images`, `profile`

### Testing Changes
- Rebuild container: `docker compose build`
- Restart: `docker compose up -d`
- Check logs: `docker compose logs -f`

## Common Issues

**"FastMCP.run() got unexpected keyword argument 'host'"**
- Fixed: `host` and `port` moved to `FastMCP()` constructor in newer mcp versions
- Current code: `mcp = FastMCP("botasaurus", host="0.0.0.0", port=1405)`

**Chromium crashes / "cannot open display"**
- Check Xvfb is running: `docker compose exec scraper ps aux | grep Xvfb`
- Restart manually: `docker compose exec scraper Xvfb :99 -screen 0 1280x800x24 -ac &`

**Tool timeout errors**
- Increase timeout in `browser_wait(selector, timeout=10)`
- Site may be slow or anti-bot triggered
- Use `browser_screenshot()` to debug

## Dependencies

See `requirements.txt`:
- `botasaurus>=4.0.0` — core framework
- `mcp>=1.0.0` — MCP SDK (FastMCP)
- `requests`, `python-dotenv`, `psutil` — utilities

## Volumes

- `/app/downloads/` — downloaded files
- `/app/profiles/` — persistent browser profiles (cookies)
- `/app/output/` — scraping results

## Network

Container runs on `dokploy-network` (Docker network for Dokploy deployment).

Ports exposed:
- `1405` — MCP StreamableHTTP
- `5846` — REST API + Web UI
- `6080` — noVNC (optional, when `ENABLE_VNC=true`)
