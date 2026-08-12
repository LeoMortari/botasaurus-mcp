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
1. **MCP Server** (port 1405) — Primary interface for AI agents via StreamableHTTP transport
2. **REST API + Web UI** (port 5846) — Debug interface and task history

**Core components:**
- `app.py` — Single Python file containing all logic
- FastMCP server with 44 tools (browser control, sessions, extraction, cookies, HTTP)
- Multi-session driver pool: `sessions: dict[str, Driver]`, up to `MAX_SESSIONS` (default 4) concurrent isolated browser instances, each with its own profile
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

1. **Session management** (top of file)
   - `sessions: dict[str, dict]` holds one entry per session: `{"driver", "lock", "profile", "tabs", "requests_log"}`
   - `get_or_create_session(session_id)` — looks up a session; lazily creates `"default"` (profile `hermes`) for backcompat with the original single-driver tools
   - `session_create_impl` / `session_list_impl` / `session_close_impl` — backing functions for the P0 session tools
   - `MAX_SESSIONS` (env, default 4) caps concurrent Chromium instances
   - Each session has its own `threading.Lock()` so calls to the *same* session are serialized (Selenium/CDP drivers aren't thread-safe), while different sessions run in parallel
   - No `@browser` decorator anymore — sessions instantiate `botasaurus.browser.Driver(...)` directly, since `reuse_driver=True` only supports a single global driver, not N concurrent ones

2. **Browser action dispatcher**
   - `browser_action(session_id, method, params)` resolves the session, takes its lock, and calls `_dispatch(entry, method, params)`
   - `_dispatch` is the big if/elif chain routing to driver methods (navigate, click, type, extract_table, get_cookies, etc.)

3. **MCP tools**
   - 44 tools registered via `@mcp.tool()`, all return JSON strings, all accept an optional `session_id: str = "default"` (except `session_create`/`session_list`/`session_close`/`http_get`/`browser_import_profile`)
   - See "MCP Tools Quick Reference" below for the full list

4. **Profile export/import**
   - `export_profile_impl` / `import_profile_impl` zip/unzip `/app/profiles/<name>` to/from base64
   - Export requires the session to be **closed** first (`session_close`) so the profile is flushed to disk and not mid-write
   - Zip extraction is guarded against zip-slip (`_safe_extract`); session/profile names are validated against `^[A-Za-z0-9_-]{1,64}$` since they map directly to filesystem paths

5. **REST server**
   - `@request` scraper for basic URL fetching
   - Registered via `Server.add_scraper(scrape_url)`

6. **Main entry**
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
| `MAX_SESSIONS` | `4` | Max concurrent browser sessions. Each session is a full Chromium process (non-headless, via Xvfb) — raise only if you also raise the container's memory/CPU limits in `docker-compose.yml` (currently 2G/1.5 CPU). |

## Key Implementation Details

### Multi-Session Driver Pool
- Every session (including `"default"`) is a separate `Driver` instance with its own Chromium process and profile — no more single global driver
- `session_create(session_id, profile_name?)` creates a new isolated session; `session_close(session_id)` closes it and frees the Chromium process
- Tools called without `session_id` transparently use `"default"` (profile `hermes`), matching the original 13-tool behavior
- First call to a session: ~2-3s startup. Subsequent calls: instant (driver stays open)
- `browser_close(session_id?)` closes a session's browser; the next call to `"default"` reopens it lazily. Non-default sessions must be recreated explicitly via `session_create`
- Cookies persist via `tiny_profile` in `/app/profiles/<profile_name>/`

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

All tools return JSON strings. Agent must parse JSON. Every tool below (except the
four noted) also accepts an optional trailing `session_id` (default `"default"`).

**P0 — sessions**

| Tool | Params | Returns |
|------|--------|---------|
| `session_create` | `session_id`, `profile_name?` | `{"session_id", "profile", "ok": true}` or error (max sessions / duplicate id) |
| `session_list` | — | `{"sessions": [{"session_id", "profile", "open_tabs"}]}` |
| `session_close` | `session_id` | `{"ok": true}` |

**Core (original 13)**

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

**P1 — interaction**

| Tool | Params | Returns |
|------|--------|---------|
| `browser_go_back` / `browser_go_forward` | — | `{"current_url": "..."}` |
| `browser_reload` | — | `{"ok": true}` |
| `browser_press_key` | `key` | `{"ok": true}` or error (best-effort, CDP) |
| `browser_select_option` | `selector`, `value` | `{"ok": true}` |
| `browser_upload_file` | `selector`, `file_path` | `{"ok": true}` |
| `browser_hover` | `selector` | `{"ok": true}` |
| `browser_exists` | `selector` | `{"exists": true/false}` — cheaper than `browser_wait` (no wait) |
| `browser_get_attribute` | `selector`, `attribute` | `{"value": "..."}` |
| `browser_new_tab` | `url?` | `{"tab_id": "..."}` |
| `browser_switch_tab` | `tab_id` | `{"ok": true}` |
| `browser_list_tabs` | — | `{"tabs": [{"tab_id", "url", "title"}]}` |

**P2 — structured extraction**

| Tool | Params | Returns |
|------|--------|---------|
| `browser_extract_table` | `selector` | `{"rows": [...]}` |
| `browser_extract_links` | `selector?` | `{"links": [{"text", "href"}]}` |
| `browser_extract_metadata` | — | `{"og": {...}, "json_ld": [...], "meta": {...}}` |
| `browser_extract_by_xpath` | `xpath` | `{"text": "..."}` |

**P3 — anti-detection**

| Tool | Params | Returns |
|------|--------|---------|
| `browser_detect_challenge` | — | `{"blocked": bool, "type": "cloudflare\|none\|..."}` |
| `browser_get_network_requests` | `filter?` (e.g. `xhr`) | `{"requests": [{"url","status","type"}]}` (buffered since session creation, max 500) |
| `browser_set_user_agent` | `user_agent` | `{"ok": true}` or error (best-effort, CDP) |
| `browser_google_get` | `url`, `accept_google_cookies?` | `{"current_url": "..."}` |

**P4 — cookies and profile**

| Tool | Params | Returns |
|------|--------|---------|
| `browser_get_cookies` | — | `{"cookies": [...]}` |
| `browser_set_cookies` | `cookies` (list) | `{"ok": true}` |
| `browser_clear_cookies` | — | `{"ok": true}` |
| `browser_export_profile` | `session_id` | `{"profile_b64": "..."}` — session must already be closed |
| `browser_import_profile` | `profile_b64`, `profile_name`, `session_id?` | `{"ok": true}` |

**P5 — direct HTTP (no browser)**

| Tool | Params | Returns |
|------|--------|---------|
| `http_get` | `url`, `headers?`, `max_length?` | `{"status", "body", "truncated"}` — plain `requests.get`, no session |

**P6 — debug (best-effort)**

| Tool | Params | Returns |
|------|--------|---------|
| `browser_get_console_logs` | — | `{"logs": [...]}` or error if unsupported |
| `browser_pdf_export` | — | `{"pdf_b64": "..."}` or error if unsupported |

## Development Notes

### Modifying MCP Tools
- Add new tool: create function with `@mcp.tool()` decorator, accept `session_id: str = "default"`
- Tool function should call `browser_action(session_id, action_name, params)`, which resolves the session and routes into `_dispatch`
- New dispatch branches go in `_dispatch(entry, method, params)` (`entry["driver"]` is the session's `Driver`)
- Return value must be JSON-serializable (will be converted to JSON string)

### Modifying Driver Behavior
- Driver config lives in `_new_driver(profile)` (used by both `get_or_create_session` and `session_create_impl`)
- Key options: `tiny_profile`, `block_images`, `profile`, `headless`
- No `reuse_driver` anymore — each session owns one `Driver` instance for its whole lifetime

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

**"max sessions (4) reached"**
- `session_create` refuses to open a 5th concurrent Chromium instance by default
- Close an unused session with `session_close(session_id)`, or raise `MAX_SESSIONS` (and the container's memory/CPU limits in `docker-compose.yml`) if you genuinely need more parallelism

**`browser_export_profile` returns "session is still open"**
- Call `session_close(session_id)` first — export reads the profile directory from disk and needs the driver to have flushed and released it

## Dependencies

See `requirements.txt`:
- `botasaurus>=4.0.0` — core framework
- `mcp>=1.0.0` — MCP SDK (FastMCP)
- `requests`, `python-dotenv`, `psutil` — utilities

## Volumes

- `/app/downloads/` — downloaded files (shared across all sessions)
- `/app/profiles/<profile_name>/` — persistent browser profiles (cookies), one directory per profile; `default` session uses `hermes`
- `/app/output/` — scraping results

## Network

Container runs on `dokploy-network` (Docker network for Dokploy deployment).

Ports exposed:
- `1405` — MCP StreamableHTTP
- `5846` — REST API + Web UI
- `6080` — noVNC (optional, when `ENABLE_VNC=true`)
