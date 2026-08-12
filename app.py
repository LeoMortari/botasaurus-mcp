import os, json, base64, time, threading
from bottle import run as bottle_run

from botasaurus.browser import browser, Driver, Wait
from botasaurus_server.server import Server
from botasaurus_server.executor import executor
from botasaurus.request import request, Request
from botasaurus.soupify import soupify
from mcp.server.fastmcp import FastMCP

DOWNLOAD_DIR = "/app/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

@browser(
    reuse_driver=True,
    block_images=True,
    headless=False,
    profile="hermes",
    tiny_profile=True,
)
def browser_action(driver: Driver, data: tuple):
    method, params = data
    try:
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

        if method == "close":
            browser_action.close()
            return {"ok": True}

        return {"error": f"Unknown method: {method}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


mcp = FastMCP("botasaurus", host="0.0.0.0", port=1405)


@mcp.tool()
def browser_navigate(url: str) -> str:
    """Navigate to URL via Google referrer (anti-bot stealth)."""
    return json.dumps(browser_action(("navigate", {"url": url})), ensure_ascii=False)


@mcp.tool()
def browser_click(selector: str) -> str:
    """Click element matching CSS selector."""
    return json.dumps(browser_action(("click", {"selector": selector})), ensure_ascii=False)


@mcp.tool()
def browser_type(selector: str, text: str) -> str:
    """Type text into an input field."""
    return json.dumps(browser_action(("type", {"selector": selector, "text": text})), ensure_ascii=False)


@mcp.tool()
def browser_get_text(selector: str) -> str:
    """Get visible text from first matching element."""
    return json.dumps(browser_action(("get_text", {"selector": selector})), ensure_ascii=False)


@mcp.tool()
def browser_get_all_text(selector: str) -> str:
    """Get visible text from ALL matching elements."""
    return json.dumps(browser_action(("get_all_text", {"selector": selector})), ensure_ascii=False)


@mcp.tool()
def browser_screenshot() -> str:
    """Take screenshot of current page. Returns base64 PNG."""
    return json.dumps(browser_action(("screenshot", {})), ensure_ascii=False)


@mcp.tool()
def browser_scroll(amount: int = 500) -> str:
    """Scroll page by pixels. Positive=down, Negative=up."""
    return json.dumps(browser_action(("scroll", {"amount": amount})), ensure_ascii=False)


@mcp.tool()
def browser_wait(selector: str, timeout: int = 8) -> str:
    """Wait for element to appear. Returns {'found': true/false}."""
    return json.dumps(browser_action(("wait", {"selector": selector, "timeout": timeout})), ensure_ascii=False)


@mcp.tool()
def browser_run_js(code: str) -> str:
    """Execute arbitrary JavaScript in the page."""
    return json.dumps(browser_action(("run_js", {"code": code})), ensure_ascii=False)


@mcp.tool()
def browser_get_html(max_length: int = 10000) -> str:
    """Get page HTML (truncated to max_length to save context)."""
    return json.dumps(browser_action(("get_html", {"max_length": max_length})), ensure_ascii=False)


@mcp.tool()
def browser_get_url() -> str:
    """Get current page URL."""
    return json.dumps(browser_action(("get_url", {})), ensure_ascii=False)


@mcp.tool()
def browser_get_download(timeout: int = 30) -> str:
    """Wait for download to complete. Returns {filename, content_b64}."""
    return json.dumps(browser_action(("get_download", {"timeout": timeout})), ensure_ascii=False)


@mcp.tool()
def browser_close() -> str:
    """Close the browser. Next tool call reopens it."""
    return json.dumps(browser_action(("close", {})), ensure_ascii=False)


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
    mcp.run(transport="streamable-http")
