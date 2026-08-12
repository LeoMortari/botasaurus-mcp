# 🤖 Botasaurus MCP Container

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-blue?logo=python" alt="Python 3.11">
  <img src="https://img.shields.io/badge/Chromium-148-green?logo=googlechrome" alt="Chromium 148">
  <img src="https://img.shields.io/badge/MCP-Server-purple?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIj48cGF0aCBkPSJNMTIgMmwzIDdoN2wtNS41IDQuNUwxOCAyMWwtNi00LjVMNiAyMWwxLjUtNy41TDIgOWg3eiIvPjwvc3ZnPg==" alt="MCP Server">
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker" alt="Docker Ready">
</p>

<p align="center">
  <strong>Control an anti-detection Chromium browser via MCP</strong><br>
  <em>Bypass Cloudflare, Datadome, reCAPTCHA — 100% undetectable</em>
</p>

---

## ✨ What is this?

Docker container that exposes a **real Chromium browser** with anti-bot techniques for AI agents to control via MCP protocol.

**Capabilities:**
- 🛡️ Bypass anti-bot systems (Cloudflare, Datadome, reCAPTCHA)
- 🖱️ Navigate, click, fill forms, log in
- 📊 Extract data with CSS selectors
- 📸 Take screenshots to "see" the page
- 📥 Download files (PDFs, images, etc.)
- 💾 Persistent session (cookies kept between calls)
- 🔒 Anonymous and undetectable profile

---

## 🚀 Quick Start

```bash
# 1. Clone and start
git clone <repo>
cd botasaurus-mcp
docker compose up -d

# 2. Check status
docker compose logs -f

# 3. Test with MCP Inspector
npx @modelcontextprotocol/inspector
# Connect to: http://localhost:1405/mcp
```

**Done!** Now any MCP agent can control the browser.

---

## 🎯 Available Tools

44 tools total. All accept an optional `session_id` (default `"default"`) so you
can run multiple isolated browser sessions in parallel — see [AGENTS.md](./AGENTS.md)
for full params/returns of every tool.

**Sessions**

| Tool | What it does |
|------|--------------|
| `session_create` | Create an isolated session (own profile/driver) |
| `session_list` | List active sessions |
| `session_close` | Close a session and free its Chromium process |

**Core browsing**

| Tool | What it does |
|------|--------------|
| `browser_navigate` | Navigate to URL (anti-bot stealth) |
| `browser_click` | Click element (CSS selector) |
| `browser_type` | Type text into input |
| `browser_get_text` | Extract text from element |
| `browser_get_all_text` | Extract text from all elements |
| `browser_screenshot` | Take screenshot (base64 PNG) |
| `browser_scroll` | Scroll page |
| `browser_wait` | Wait for element to appear |
| `browser_run_js` | Execute JavaScript |
| `browser_get_html` | Get page HTML |
| `browser_get_url` | Get current URL |
| `browser_get_download` | Wait and get download |
| `browser_close` | Close browser session |

**Interaction**

| Tool | What it does |
|------|--------------|
| `browser_go_back` / `browser_go_forward` | Navigate browser history |
| `browser_reload` | Reload the page |
| `browser_press_key` | Press a keyboard key |
| `browser_select_option` | Select a dropdown option |
| `browser_upload_file` | Upload a file to an input |
| `browser_hover` | Hover over an element |
| `browser_exists` | Cheap existence check (no wait) |
| `browser_get_attribute` | Read an element attribute |
| `browser_new_tab` / `browser_switch_tab` / `browser_list_tabs` | Multi-tab control |

**Extraction**

| Tool | What it does |
|------|--------------|
| `browser_extract_table` | Parse a `<table>` into JSON rows |
| `browser_extract_links` | Extract `{text, href}` for links |
| `browser_extract_metadata` | Extract Open Graph / JSON-LD / meta tags |
| `browser_extract_by_xpath` | Extract text via XPath |

**Anti-detection**

| Tool | What it does |
|------|--------------|
| `browser_detect_challenge` | Detect Cloudflare/anti-bot blocks |
| `browser_get_network_requests` | Inspect recent XHR/fetch requests |
| `browser_set_user_agent` | Override user agent (best-effort) |
| `browser_google_get` | Navigate simulating a Google referrer |

**Cookies & profile**

| Tool | What it does |
|------|--------------|
| `browser_get_cookies` / `browser_set_cookies` / `browser_clear_cookies` | Manage cookies |
| `browser_export_profile` / `browser_import_profile` | Save/restore a login profile outside the container |

**Direct HTTP & debug**

| Tool | What it does |
|------|--------------|
| `http_get` | Plain HTTP GET, no browser (fast when JS isn't needed) |
| `browser_get_console_logs` | Read console logs (best-effort) |
| `browser_pdf_export` | Export page as PDF (best-effort) |

---

## 📖 Usage Example

**Flow: Login + Data Extraction**

```python
# 1. Navigate
browser_navigate("https://site.com/login")

# 2. Fill form
browser_type("#email", "user@email.com")
browser_type("#password", "mypassword")
browser_click("button[type=submit]")

# 3. Wait for login
browser_wait(".dashboard", timeout=10)

# 4. Verify visually
browser_screenshot()

# 5. Extract data
browser_get_text(".balance")
browser_get_all_text("table tr td")
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────┐
│         Container: botasaurus           │
│                                         │
│  ┌─────────────┐    ┌───────────────┐  │
│  │ MCP Server  │    │ REST API      │  │
│  │ :1405       │    │ :5846         │  │
│  │ (SSE)       │    │ (Debug/UI)    │  │
│  └──────┬──────┘    └───────┬───────┘  │
│         │                   │          │
│         └───────┬───────────┘          │
│                 ▼                      │
│  ┌─────────────────────────────────┐  │
│  │  Chromium 148 + Botasaurus      │  │
│  │  • Up to MAX_SESSIONS drivers   │  │
│  │  • 1 profile per session        │  │
│  │  • block_images=True            │  │
│  └─────────────────────────────────┘  │
│                                         │
│  Xvfb :99 (virtual display)           │
│  [Optional] VNC :6080                 │
└─────────────────────────────────────────┘
```

---

## ⚙️ Configuration

**Environment variables** (`docker-compose.yml`):

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_VNC` | `false` | Enable VNC to see browser live |
| `PORT` | `5846` | REST API port |
| `TZ` | `America/Sao_Paulo` | Timezone |
| `MAX_SESSIONS` | `4` | Max concurrent browser sessions (each is a full Chromium process) |

**Enable VNC** (watch browser in real-time):
```bash
ENABLE_VNC=true docker compose up -d
# Access: http://localhost:6080
```

---

## 🔌 Integration

### MCP StreamableHTTP (recommended)

Connect any MCP agent:

```yaml
mcp_servers:
  botasaurus:
    url: http://localhost:1405/mcp
```

### REST API (debug)

```bash
curl http://localhost:5846/
# Web UI with task history
```

---

## 📚 Documentation

- **[AGENTS.md](./AGENTS.md)** — Complete technical guide for AIs (architecture, implementation, troubleshooting)
- **[README.md](./README.md)** — This file (overview for humans)

---

## 🐛 Troubleshooting

**Container won't start?**
```bash
docker compose logs -f
```

**Chromium crashed?**
```bash
# Close and reopen browser
browser_close()
# Next call creates new driver automatically
```

**Tool timeout?**
```bash
# Increase timeout
browser_wait(selector, timeout=15)
```

---

## 📦 Stack

- **Python 3.11** + Botasaurus (anti-detection)
- **MCP SDK** (FastMCP server)
- **Chromium 148** + chromedriver
- **Xvfb** (virtual display)
- **Docker** (containerization)

---

## 📄 License

This project is for internal use. Adjust as needed.

---

<p align="center">
  <strong>Built for AI agents that need to browse like humans</strong>
</p>
