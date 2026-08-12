import os, json, base64, time, threading, uuid, re, io, zipfile, requests, uvicorn
from typing import Optional
from bottle import run as bottle_run
from starlette.middleware.cors import CORSMiddleware

from botasaurus.browser import Driver, Wait
from botasaurus_driver.cdp import input_ as cdp_input, page as cdp_page, network as cdp_network, runtime as cdp_runtime
from botasaurus_server.server import Server
from botasaurus_server.executor import executor
from botasaurus.request import request, Request
from botasaurus.soupify import soupify
from mcp.server.fastmcp import FastMCP

DOWNLOAD_DIR = "/app/downloads"
PROFILES_DIR = os.path.join(os.getcwd(), "profiles")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(PROFILES_DIR, exist_ok=True)

MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "4"))
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

sessions: dict = {}
sessions_lock = threading.Lock()
session_profiles: dict = {}


def _validate_name(name, kind="session_id"):
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(f"invalid {kind}: must match ^[A-Za-z0-9_-]{{1,64}}$ (got {name!r})")


def _attach_request_logger(session_id, driver):
    log = sessions[session_id]["requests_log"]

    def on_response(request_id, response, event):
        try:
            log.append({
                "url": response.url,
                "status": response.status,
                "type": getattr(event, "type", None),
            })
            if len(log) > 500:
                del log[: len(log) - 500]
        except Exception:
            pass

    try:
        driver.after_response_received(on_response)
    except Exception:
        pass


def _attach_console_logger(session_id, driver):
    log = sessions[session_id]["console_log"]

    def on_console(event):
        try:
            args = [a.value if a.value is not None else a.description for a in (event.args or [])]
            log.append({"type": event.type_, "text": " ".join(str(a) for a in args)})
            if len(log) > 500:
                del log[: len(log) - 500]
        except Exception:
            pass

    try:
        driver.run_cdp_command(cdp_runtime.enable())
        driver._tab.add_handler(cdp_runtime.ConsoleAPICalled, on_console)
    except Exception:
        pass


def _new_driver(profile: str) -> Driver:
    return Driver(
        profile=profile,
        tiny_profile=True,
        block_images=True,
        headless=False,
    )


def get_or_create_session(session_id: str):
    """Returns (entry, error). Lazily creates the 'default' session for backcompat."""
    with sessions_lock:
        entry = sessions.get(session_id)
        if entry:
            return entry, None
        if session_id != "default":
            return None, {"error": f"session '{session_id}' not found. Create it first with session_create."}
        if len(sessions) >= MAX_SESSIONS:
            return None, {"error": f"max sessions ({MAX_SESSIONS}) reached"}
        try:
            driver = _new_driver("hermes")
        except Exception as e:
            return None, {"error": f"{type(e).__name__}: {e}"}
        entry = {"driver": driver, "lock": threading.Lock(), "profile": "hermes", "tabs": {}, "requests_log": [], "console_log": []}
        sessions[session_id] = entry
        session_profiles[session_id] = "hermes"
        _attach_request_logger(session_id, driver)
        _attach_console_logger(session_id, driver)
        return entry, None


def session_create_impl(session_id: str, profile_name: Optional[str] = None):
    try:
        _validate_name(session_id, "session_id")
        if profile_name:
            _validate_name(profile_name, "profile_name")
    except ValueError as e:
        return {"error": str(e)}
    with sessions_lock:
        if session_id in sessions:
            return {"error": f"session '{session_id}' already exists"}
        if len(sessions) >= MAX_SESSIONS:
            return {"error": f"max sessions ({MAX_SESSIONS}) reached"}
        profile = profile_name or session_id
        try:
            driver = _new_driver(profile)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
        sessions[session_id] = {"driver": driver, "lock": threading.Lock(), "profile": profile, "tabs": {}, "requests_log": [], "console_log": []}
        session_profiles[session_id] = profile
        _attach_request_logger(session_id, driver)
        _attach_console_logger(session_id, driver)
    return {"session_id": session_id, "profile": profile, "ok": True}


def session_list_impl():
    with sessions_lock:
        return {"sessions": [
            {"session_id": sid, "profile": e["profile"], "open_tabs": len(e["tabs"])}
            for sid, e in sessions.items()
        ]}


def session_close_impl(session_id: str):
    with sessions_lock:
        entry = sessions.pop(session_id, None)
    if not entry:
        return {"error": f"session '{session_id}' not found"}
    with entry["lock"]:
        try:
            entry["driver"].close()
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
    return {"ok": True}


def _safe_extract(zf: zipfile.ZipFile, target_dir: str):
    target_dir = os.path.realpath(target_dir)
    for member in zf.namelist():
        member_path = os.path.realpath(os.path.join(target_dir, member))
        if member_path != target_dir and not member_path.startswith(target_dir + os.sep):
            raise ValueError(f"unsafe zip member path: {member}")
    zf.extractall(target_dir)


def export_profile_impl(session_id: str):
    try:
        _validate_name(session_id, "session_id")
    except ValueError as e:
        return {"error": str(e)}
    with sessions_lock:
        if session_id in sessions:
            return {"error": f"session '{session_id}' is still open; call session_close first to flush the profile to disk"}
    profile = session_profiles.get(session_id)
    if not profile:
        return {"error": f"no known profile for session '{session_id}'"}
    profile_path = os.path.join(PROFILES_DIR, profile)
    if not os.path.isdir(profile_path):
        return {"error": f"profile directory not found: {profile_path}"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(profile_path):
            for f in files:
                fp = os.path.join(root, f)
                zf.write(fp, os.path.relpath(fp, profile_path))
    return {"profile_b64": base64.b64encode(buf.getvalue()).decode()}


def import_profile_impl(profile_b64: str, profile_name: str, session_id: Optional[str] = None):
    try:
        _validate_name(profile_name, "profile_name")
        if session_id:
            _validate_name(session_id, "session_id")
    except ValueError as e:
        return {"error": str(e)}
    target_dir = os.path.join(PROFILES_DIR, profile_name)
    os.makedirs(target_dir, exist_ok=True)
    try:
        raw = base64.b64decode(profile_b64)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            _safe_extract(zf, target_dir)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if session_id:
        session_profiles[session_id] = profile_name
    return {"ok": True}


def _dispatch(entry: dict, method: str, params: dict):
    driver: Driver = entry["driver"]

    if method == "navigate":
        driver.google_get(params["url"])
        return {"current_url": driver.current_url}

    if method == "click":
        driver.click(params["selector"])
        return {"ok": True}

    if method == "type":
        driver.type(params["selector"], params["text"])
        return {"ok": True}

    if method == "get_text":
        text = driver.get_text(params["selector"])
        return {"text": text or "(empty)"}

    if method == "get_all_text":
        els = driver.select_all(params["selector"])
        texts = [e.text for e in els if e and e.text]
        return {"count": len(texts), "items": texts}

    if method == "screenshot":
        fp = os.path.join(DOWNLOAD_DIR, "_screenshot.png")
        driver.save_screenshot(fp)
        with open(fp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return {"screenshot_b64": b64, "mime": "image/png", "bytes": len(b64)}

    if method == "scroll":
        driver.run_js(f"window.scrollBy(0, {params['amount']})")
        return {"ok": True}

    if method == "wait":
        t = params.get("timeout", 8)
        if t <= 4:
            w = Wait.SHORT
        elif t <= 8:
            w = Wait.LONG
        else:
            w = Wait.VERY_LONG
        el = driver.wait_for_element(params["selector"], wait=w)
        return {"found": el is not None}

    if method == "run_js":
        code = params["code"].strip()
        if not code.startswith("return") and ";" not in code and "\n" not in code:
            code = "return " + code
        result = driver.run_js(code)
        return {"result": result}

    if method == "get_html":
        html = driver.page_source
        ml = params.get("max_length", 10000)
        truncated = len(html) > ml
        return {"html": html[:ml], "truncated": truncated, "total_len": len(html)}

    if method == "get_url":
        return {"url": driver.current_url}

    if method == "get_download":
        before = set(os.listdir(DOWNLOAD_DIR))
        deadline = time.time() + params.get("timeout", 30)
        while time.time() < deadline:
            time.sleep(1)
            after = set(os.listdir(DOWNLOAD_DIR))
            new = after - before
            new = [f for f in new if not f.startswith("_") and not f.endswith((".crdownload", ".tmp"))]
            if new:
                fname = sorted(new, key=lambda f: os.path.getmtime(os.path.join(DOWNLOAD_DIR, f)))[-1]
                fpath = os.path.join(DOWNLOAD_DIR, fname)
                with open(fpath, "rb") as f:
                    return {"filename": fname, "content_b64": base64.b64encode(f.read()).decode()}
        return {"filename": None, "error": "No download within timeout"}

    if method == "go_back":
        driver.run_js("window.history.back();")
        time.sleep(0.5)
        return {"current_url": driver.current_url}

    if method == "go_forward":
        driver.run_js("window.history.forward();")
        time.sleep(0.5)
        return {"current_url": driver.current_url}

    if method == "reload":
        driver.run_js("window.location.reload();")
        return {"ok": True}

    if method == "press_key":
        try:
            key = params["key"]
            driver.run_cdp_command(cdp_input.dispatch_key_event(type_="keyDown", key=key, code=key))
            driver.run_cdp_command(cdp_input.dispatch_key_event(type_="keyUp", key=key, code=key))
            return {"ok": True}
        except Exception as e:
            return {"error": f"press_key not supported by this driver: {type(e).__name__}: {e}"}

    if method == "select_option":
        driver.select_option(params["selector"], value=params.get("value"))
        return {"ok": True}

    if method == "upload_file":
        driver.upload_file(params["selector"], params["file_path"])
        return {"ok": True}

    if method == "hover":
        driver.select("html").move_mouse_to_element(params["selector"])
        return {"ok": True}

    if method == "exists":
        el = driver.select(params["selector"], wait=None)
        return {"exists": el is not None}

    if method == "get_attribute":
        val = driver.get_attribute(params["selector"], params["attribute"])
        return {"value": val}

    if method == "new_tab":
        tab = driver.open_link_in_new_tab(params.get("url") or "about:blank")
        tab_id = uuid.uuid4().hex[:8]
        entry["tabs"][tab_id] = tab
        return {"tab_id": tab_id}

    if method == "switch_tab":
        tab = entry["tabs"].get(params["tab_id"])
        if not tab:
            return {"error": f"tab '{params['tab_id']}' not found"}
        driver.switch_to_tab(tab)
        return {"ok": True}

    if method == "list_tabs":
        tabs = [
            {"tab_id": tid, "url": getattr(tab, "url", None), "title": getattr(tab, "title", None)}
            for tid, tab in entry["tabs"].items()
        ]
        return {"tabs": tabs}

    if method == "extract_table":
        soup = soupify(driver)
        table = soup.select_one(params["selector"])
        if not table:
            return {"rows": []}
        header_cells = table.select("thead th")
        headers = [th.get_text(strip=True) for th in header_cells] if header_cells else None
        body_rows = table.select("tbody tr") or table.find_all("tr")
        rows = []
        for tr in body_rows:
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if headers and len(headers) == len(cells):
                rows.append(dict(zip(headers, cells)))
            else:
                rows.append(cells)
        return {"rows": rows}

    if method == "extract_links":
        soup = soupify(driver)
        scope = soup.select_one(params["selector"]) if params.get("selector") else soup
        links = [{"text": a.get_text(strip=True), "href": a["href"]} for a in (scope.find_all("a", href=True) if scope else [])]
        return {"links": links}

    if method == "extract_metadata":
        soup = soupify(driver)
        og, meta = {}, {}
        for tag in soup.find_all("meta"):
            name = tag.get("property") or tag.get("name")
            content = tag.get("content")
            if not name or content is None:
                continue
            if name.startswith("og:"):
                og[name[3:]] = content
            else:
                meta[name] = content
        json_ld = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                json_ld.append(json.loads(script.string or ""))
            except Exception:
                pass
        return {"og": og, "json_ld": json_ld, "meta": meta}

    if method == "extract_by_xpath":
        code = (
            "var r = document.evaluate(%s, document, null, XPathResult.STRING_TYPE, null);"
            "return r.stringValue;" % json.dumps(params["xpath"])
        )
        result = driver.run_js(code)
        return {"text": result}

    if method == "detect_challenge":
        try:
            blocked = bool(driver.is_bot_detected())
        except Exception:
            blocked = False
        ctype = "none"
        try:
            if driver.is_bot_detected_by_cloudflare():
                ctype = "cloudflare"
            elif blocked:
                by = driver.get_bot_detected_by()
                ctype = str(by) if by else "unknown"
        except Exception:
            pass
        return {"blocked": blocked, "type": ctype}

    if method == "get_network_requests":
        f = params.get("filter")
        items = [r for r in entry["requests_log"] if not f or r.get("type") == f]
        return {"requests": items[-200:]}

    if method == "set_user_agent":
        try:
            driver.run_cdp_command(cdp_network.set_user_agent_override(user_agent=params["user_agent"]))
            return {"ok": True}
        except Exception as e:
            return {"error": f"runtime user-agent override not supported by this driver: {type(e).__name__}: {e}"}

    if method == "google_get":
        driver.google_get(params["url"], accept_google_cookies=params.get("accept_google_cookies", False))
        return {"current_url": driver.current_url}

    if method == "get_cookies":
        return {"cookies": driver.get_cookies()}

    if method == "set_cookies":
        driver.add_cookies(params["cookies"])
        return {"ok": True}

    if method == "clear_cookies":
        driver.delete_cookies()
        return {"ok": True}

    if method == "get_console_logs":
        return {"logs": entry["console_log"][-200:]}

    if method == "pdf_export":
        try:
            result = driver.run_cdp_command(cdp_page.print_to_pdf())
            data = result[0] if isinstance(result, (tuple, list)) and result else None
            if not data:
                raise RuntimeError("no data returned by Page.printToPDF")
            return {"pdf_b64": data}
        except Exception as e:
            return {"error": f"pdf export not supported by this driver: {type(e).__name__}: {e}"}

    return {"error": f"Unknown method: {method}"}


def browser_action(session_id: str, method: str, params: dict):
    entry, err = get_or_create_session(session_id)
    if err:
        return err
    with entry["lock"]:
        try:
            return _dispatch(entry, method, params)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


mcp = FastMCP("botasaurus", host="0.0.0.0", port=1405)



@mcp.tool()
def session_create(session_id: str, profile_name: Optional[str] = None) -> str:
    """Create an isolated browser session with its own driver/profile."""
    return json.dumps(session_create_impl(session_id, profile_name), ensure_ascii=False)


@mcp.tool()
def session_list() -> str:
    """List active browser sessions."""
    return json.dumps(session_list_impl(), ensure_ascii=False)


@mcp.tool()
def session_close(session_id: str) -> str:
    """Close a browser session and free its resources."""
    return json.dumps(session_close_impl(session_id), ensure_ascii=False)



@mcp.tool()
def browser_navigate(url: str, session_id: str = "default") -> str:
    """Navigate to URL via Google referrer (anti-bot stealth)."""
    return json.dumps(browser_action(session_id, "navigate", {"url": url}), ensure_ascii=False)


@mcp.tool()
def browser_click(selector: str, session_id: str = "default") -> str:
    """Click element matching CSS selector."""
    return json.dumps(browser_action(session_id, "click", {"selector": selector}), ensure_ascii=False)


@mcp.tool()
def browser_type(selector: str, text: str, session_id: str = "default") -> str:
    """Type text into an input field."""
    return json.dumps(browser_action(session_id, "type", {"selector": selector, "text": text}), ensure_ascii=False)


@mcp.tool()
def browser_get_text(selector: str, session_id: str = "default") -> str:
    """Get visible text from first matching element."""
    return json.dumps(browser_action(session_id, "get_text", {"selector": selector}), ensure_ascii=False)


@mcp.tool()
def browser_get_all_text(selector: str, session_id: str = "default") -> str:
    """Get visible text from ALL matching elements."""
    return json.dumps(browser_action(session_id, "get_all_text", {"selector": selector}), ensure_ascii=False)


@mcp.tool()
def browser_screenshot(session_id: str = "default") -> str:
    """Take screenshot of current page. Returns base64 PNG."""
    return json.dumps(browser_action(session_id, "screenshot", {}), ensure_ascii=False)


@mcp.tool()
def browser_scroll(amount: int = 500, session_id: str = "default") -> str:
    """Scroll page by pixels. Positive=down, Negative=up."""
    return json.dumps(browser_action(session_id, "scroll", {"amount": amount}), ensure_ascii=False)


@mcp.tool()
def browser_wait(selector: str, timeout: int = 8, session_id: str = "default") -> str:
    """Wait for element to appear. Returns {'found': true/false}."""
    return json.dumps(browser_action(session_id, "wait", {"selector": selector, "timeout": timeout}), ensure_ascii=False)


@mcp.tool()
def browser_run_js(code: str, session_id: str = "default") -> str:
    """Execute arbitrary JavaScript in the page."""
    return json.dumps(browser_action(session_id, "run_js", {"code": code}), ensure_ascii=False)


@mcp.tool()
def browser_get_html(max_length: int = 10000, session_id: str = "default") -> str:
    """Get page HTML (truncated to max_length to save context)."""
    return json.dumps(browser_action(session_id, "get_html", {"max_length": max_length}), ensure_ascii=False)


@mcp.tool()
def browser_get_url(session_id: str = "default") -> str:
    """Get current page URL."""
    return json.dumps(browser_action(session_id, "get_url", {}), ensure_ascii=False)


@mcp.tool()
def browser_get_download(timeout: int = 30, session_id: str = "default") -> str:
    """Wait for download to complete. Returns {filename, content_b64}."""
    return json.dumps(browser_action(session_id, "get_download", {"timeout": timeout}), ensure_ascii=False)


@mcp.tool()
def browser_close(session_id: str = "default") -> str:
    """Close the browser session. Next tool call on 'default' reopens it."""
    return json.dumps(session_close_impl(session_id), ensure_ascii=False)



@mcp.tool()
def browser_go_back(session_id: str = "default") -> str:
    """Navigate back in browser history."""
    return json.dumps(browser_action(session_id, "go_back", {}), ensure_ascii=False)


@mcp.tool()
def browser_go_forward(session_id: str = "default") -> str:
    """Navigate forward in browser history."""
    return json.dumps(browser_action(session_id, "go_forward", {}), ensure_ascii=False)


@mcp.tool()
def browser_reload(session_id: str = "default") -> str:
    """Reload the current page."""
    return json.dumps(browser_action(session_id, "reload", {}), ensure_ascii=False)


@mcp.tool()
def browser_press_key(key: str, session_id: str = "default") -> str:
    """Press a keyboard key (Enter, Tab, Escape, ArrowDown, etc). Best-effort via CDP."""
    return json.dumps(browser_action(session_id, "press_key", {"key": key}), ensure_ascii=False)


@mcp.tool()
def browser_select_option(selector: str, value: str, session_id: str = "default") -> str:
    """Select an option in a <select> dropdown by value."""
    return json.dumps(browser_action(session_id, "select_option", {"selector": selector, "value": value}), ensure_ascii=False)


@mcp.tool()
def browser_upload_file(selector: str, file_path: str, session_id: str = "default") -> str:
    """Upload a file to a file input element."""
    return json.dumps(browser_action(session_id, "upload_file", {"selector": selector, "file_path": file_path}), ensure_ascii=False)


@mcp.tool()
def browser_hover(selector: str, session_id: str = "default") -> str:
    """Move the mouse over an element."""
    return json.dumps(browser_action(session_id, "hover", {"selector": selector}), ensure_ascii=False)


@mcp.tool()
def browser_exists(selector: str, session_id: str = "default") -> str:
    """Check if an element exists, without waiting. Cheaper than browser_wait."""
    return json.dumps(browser_action(session_id, "exists", {"selector": selector}), ensure_ascii=False)


@mcp.tool()
def browser_get_attribute(selector: str, attribute: str, session_id: str = "default") -> str:
    """Get an attribute value from the first matching element."""
    return json.dumps(browser_action(session_id, "get_attribute", {"selector": selector, "attribute": attribute}), ensure_ascii=False)


@mcp.tool()
def browser_new_tab(url: Optional[str] = None, session_id: str = "default") -> str:
    """Open a new tab, optionally navigating to a URL. Returns {'tab_id': '...'}."""
    return json.dumps(browser_action(session_id, "new_tab", {"url": url}), ensure_ascii=False)


@mcp.tool()
def browser_switch_tab(tab_id: str, session_id: str = "default") -> str:
    """Switch focus to a previously opened tab."""
    return json.dumps(browser_action(session_id, "switch_tab", {"tab_id": tab_id}), ensure_ascii=False)


@mcp.tool()
def browser_list_tabs(session_id: str = "default") -> str:
    """List tabs opened via browser_new_tab in this session."""
    return json.dumps(browser_action(session_id, "list_tabs", {}), ensure_ascii=False)



@mcp.tool()
def browser_extract_table(selector: str, session_id: str = "default") -> str:
    """Parse a <table> element into JSON rows."""
    return json.dumps(browser_action(session_id, "extract_table", {"selector": selector}), ensure_ascii=False)


@mcp.tool()
def browser_extract_links(selector: Optional[str] = None, session_id: str = "default") -> str:
    """Extract {text, href} for all links, optionally scoped to a selector."""
    return json.dumps(browser_action(session_id, "extract_links", {"selector": selector}), ensure_ascii=False)


@mcp.tool()
def browser_extract_metadata(session_id: str = "default") -> str:
    """Extract Open Graph tags, JSON-LD, and other <meta> tags from the page."""
    return json.dumps(browser_action(session_id, "extract_metadata", {}), ensure_ascii=False)


@mcp.tool()
def browser_extract_by_xpath(xpath: str, session_id: str = "default") -> str:
    """Extract text using an XPath expression (complements CSS selectors)."""
    return json.dumps(browser_action(session_id, "extract_by_xpath", {"xpath": xpath}), ensure_ascii=False)



@mcp.tool()
def browser_detect_challenge(session_id: str = "default") -> str:
    """Detect whether the page is blocked by an anti-bot challenge (Cloudflare, etc)."""
    return json.dumps(browser_action(session_id, "detect_challenge", {}), ensure_ascii=False)


@mcp.tool()
def browser_get_network_requests(filter: Optional[str] = None, session_id: str = "default") -> str:
    """List recent XHR/fetch requests observed in this session (e.g. filter='xhr')."""
    return json.dumps(browser_action(session_id, "get_network_requests", {"filter": filter}), ensure_ascii=False)


@mcp.tool()
def browser_set_user_agent(user_agent: str, session_id: str = "default") -> str:
    """Override the browser's user agent at runtime. Best-effort via CDP."""
    return json.dumps(browser_action(session_id, "set_user_agent", {"user_agent": user_agent}), ensure_ascii=False)


@mcp.tool()
def browser_google_get(url: str, session_id: str = "default") -> str:
    """Navigate to a URL simulating arrival from a Google search (stealth referrer)."""
    return json.dumps(browser_action(session_id, "google_get", {"url": url}), ensure_ascii=False)



@mcp.tool()
def browser_get_cookies(session_id: str = "default") -> str:
    """Get all cookies for the current session."""
    return json.dumps(browser_action(session_id, "get_cookies", {}), ensure_ascii=False)


@mcp.tool()
def browser_set_cookies(cookies: list, session_id: str = "default") -> str:
    """Add cookies to the current session."""
    return json.dumps(browser_action(session_id, "set_cookies", {"cookies": cookies}), ensure_ascii=False)


@mcp.tool()
def browser_clear_cookies(session_id: str = "default") -> str:
    """Clear all cookies for the current session."""
    return json.dumps(browser_action(session_id, "clear_cookies", {}), ensure_ascii=False)


@mcp.tool()
def browser_export_profile(session_id: str = "default") -> str:
    """Export a session's browser profile as base64. The session must be closed first (session_close)."""
    return json.dumps(export_profile_impl(session_id), ensure_ascii=False)


@mcp.tool()
def browser_import_profile(profile_b64: str, profile_name: str, session_id: Optional[str] = None) -> str:
    """Import a base64 browser profile into profile_name, so it can be used by session_create."""
    return json.dumps(import_profile_impl(profile_b64, profile_name, session_id), ensure_ascii=False)



@mcp.tool()
def http_get(url: str, headers: Optional[dict] = None, max_length: int = 20000) -> str:
    """Plain HTTP GET without a browser. Much faster when the site doesn't need JS."""
    try:
        resp = requests.get(url, headers=headers or {}, timeout=30)
        body = resp.text
        truncated = len(body) > max_length
        return json.dumps({
            "status": resp.status_code,
            "body": body[:max_length],
            "truncated": truncated,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)



@mcp.tool()
def browser_get_console_logs(session_id: str = "default") -> str:
    """Get browser console logs. Best-effort; may be unsupported depending on driver version."""
    return json.dumps(browser_action(session_id, "get_console_logs", {}), ensure_ascii=False)


@mcp.tool()
def browser_pdf_export(session_id: str = "default") -> str:
    """Export the current page as PDF via CDP print-to-PDF. Best-effort."""
    return json.dumps(browser_action(session_id, "pdf_export", {}), ensure_ascii=False)


@request
def scrape_url(request: Request, data):
    response = request.get(data["url"])
    soup = soupify(response)
    title = soup.find("title")
    return {"title": title.text.strip() if title else "", "url": data["url"]}


Server.add_scraper(scrape_url)


if __name__ == "__main__":
    def start_backend():
        executor.load()
        executor.start()
        bottle_run(host="0.0.0.0", port=5846, debug=False)

    t = threading.Thread(target=start_backend, daemon=True)
    t.start()

    print("--- MCP StreamableHTTP server starting on port 1405 ---")
    http_app = mcp.streamable_http_app()
    http_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )
    uvicorn.run(http_app, host="0.0.0.0", port=1405)
