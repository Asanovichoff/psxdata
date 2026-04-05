#!/usr/bin/env python3
"""
PSX endpoint diagnostic tool.

Probes all 8 dps.psx.com.pk endpoints and writes docs/PSX_ENDPOINTS.md.

Usage:
    python tools/probe_endpoints.py                        # probe all endpoints
    python tools/probe_endpoints.py --endpoint historical  # probe one endpoint
    python tools/probe_endpoints.py --no-playwright        # skip JS endpoints
"""

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

DOCS_DIR = Path(__file__).parent.parent / "docs"

BASE_URL = "https://dps.psx.com.pk"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://dps.psx.com.pk/",
    "X-Requested-With": "XMLHttpRequest",
}

END_DATE = date.today()
START_DATE = END_DATE - timedelta(days=30)


def has_table(html: str) -> bool:
    """Return True if the HTML contains a rendered data table."""
    return "<table" in html.lower() or "<tbody" in html.lower()


def extract_columns(html: str) -> list[str]:
    """Extract column names from the first <th> tags found."""
    soup = BeautifulSoup(html, "lxml")
    headers = [th.get_text(strip=True) for th in soup.find_all("th")]
    return headers if headers else []


def extract_sample_row(html: str) -> list[str]:
    """Extract the first data row from the first table found."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table:
        return []
    rows = table.find_all("tr")
    for row in rows:
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if cells:
            return cells
    return []


def probe_with_requests(url: str, method: str = "GET", data: dict | None = None) -> dict:
    """
    Probe a single endpoint using requests.

    Returns a dict with: status_code, content_type, size_bytes, html,
    response_headers, has_table, columns, sample_row, error.
    """
    result = {
        "status_code": None,
        "content_type": None,
        "size_bytes": 0,
        "html": "",
        "response_headers": {},
        "has_table": False,
        "columns": [],
        "sample_row": [],
        "error": None,
    }
    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        if method == "POST":
            resp = session.post(url, data=data, timeout=30)
        else:
            resp = session.get(url, timeout=30)
        result["status_code"] = resp.status_code
        result["content_type"] = resp.headers.get("Content-Type", "")
        result["size_bytes"] = len(resp.content)
        result["html"] = resp.text
        result["response_headers"] = dict(resp.headers)
        result["has_table"] = has_table(resp.text)
        if result["has_table"]:
            result["columns"] = extract_columns(resp.text)
            result["sample_row"] = extract_sample_row(resp.text)
    except Exception as exc:
        result["error"] = str(exc)
    return result


def probe_with_playwright(url: str) -> dict:
    """
    Probe a JS-rendered endpoint using Playwright headless Chromium.

    Returns same shape as probe_with_requests.
    """
    result = {
        "status_code": None,
        "content_type": "text/html (playwright)",
        "size_bytes": 0,
        "html": "",
        "response_headers": {},
        "has_table": False,
        "columns": [],
        "sample_row": [],
        "error": None,
    }
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(extra_http_headers=HEADERS)
            response = page.goto(url, timeout=30000, wait_until="networkidle")
            result["status_code"] = response.status if response else None
            html = page.content()
            result["html"] = html
            result["size_bytes"] = len(html.encode())
            result["has_table"] = has_table(html)
            if result["has_table"]:
                result["columns"] = extract_columns(html)
                result["sample_row"] = extract_sample_row(html)
            browser.close()
    except Exception as exc:
        result["error"] = str(exc)
    return result


def probe_rate_limit(url: str, method: str = "GET", data: dict | None = None) -> dict:
    """
    Fire 5 rapid requests and report rate limit behavior.

    Returns: {"got_429": bool, "slowdown_detected": bool, "status_codes": list[int]}
    """
    status_codes = []
    times = []
    session = requests.Session()
    session.headers.update(HEADERS)
    for _ in range(5):
        t0 = time.monotonic()
        try:
            if method == "POST":
                resp = session.post(url, data=data, timeout=10)
            else:
                resp = session.get(url, timeout=10)
            status_codes.append(resp.status_code)
        except Exception:
            status_codes.append(0)
        times.append(time.monotonic() - t0)
    avg_time = sum(times) / len(times)
    slowdown = max(times) > avg_time * 3
    return {
        "got_429": 429 in status_codes,
        "slowdown_detected": slowdown,
        "status_codes": status_codes,
    }


def format_headers(headers: dict) -> str:
    """Format selected response headers for the report."""
    interesting = [
        "Content-Type", "X-RateLimit-Limit", "X-RateLimit-Remaining",
        "X-RateLimit-Reset", "Set-Cookie", "Server", "CF-Ray",
    ]
    lines = []
    for key in interesting:
        if key in headers:
            lines.append(f"  - {key}: {headers[key][:120]}")
    if not lines:
        lines.append("  - (no rate-limit or notable headers observed)")
    return "\n".join(lines)


def probe_endpoint(name: str, url: str, method: str, data: dict | None,
                   use_playwright: bool) -> dict:
    """
    Full probe of one endpoint: plain HTTP first, Playwright if JS shell.

    Returns a findings dict ready for report generation.
    """
    print(f"  Probing {name} ({method} {url}) ...", end=" ", flush=True)

    plain = probe_with_requests(url, method=method, data=data)

    rendering_mode = "requests + BeautifulSoup"
    playwright_result = None

    if plain["error"]:
        print(f"ERROR: {plain['error']}")
        return {
            "name": name, "url": url, "method": method,
            "rendering_mode": rendering_mode,
            "status": "error",
            "plain": plain,
            "playwright": None,
            "rate_limit": None,
            "columns": [],
            "sample_row": [],
        }

    if not plain["has_table"] and use_playwright:
        print("JS shell detected, re-fetching with Playwright ...", end=" ", flush=True)
        rendering_mode = "playwright (JS-rendered)"
        playwright_result = probe_with_playwright(url)
        columns = playwright_result["columns"]
        sample_row = playwright_result["sample_row"]
        status = "working" if playwright_result["has_table"] else "partial"
    else:
        columns = plain["columns"]
        sample_row = plain["sample_row"]
        status = "working" if plain["has_table"] else "partial"

    rate_limit = probe_rate_limit(url, method=method, data=data)
    print("done")

    return {
        "name": name,
        "url": url,
        "method": method,
        "rendering_mode": rendering_mode,
        "status": status,
        "plain": plain,
        "playwright": playwright_result,
        "rate_limit": rate_limit,
        "columns": columns,
        "sample_row": sample_row,
    }


def render_endpoint_section(f: dict) -> str:
    """Render one endpoint section for PSX_ENDPOINTS.md."""
    status_icon = {"working": "✓ Working", "partial": "⚠ Partial", "error": "✗ Error"}.get(
        f["status"], "?"
    )

    rate = f["rate_limit"]
    if rate is None:
        rate_text = "- Could not probe (initial request failed)"
    elif rate["got_429"]:
        rate_text = f"- Got 429 during 5-request burst. Status codes: {rate['status_codes']}"
    elif rate["slowdown_detected"]:
        rate_text = f"- Slowdown detected during burst. Status codes: {rate['status_codes']}"
    else:
        rate_text = f"- 5 rapid requests: all returned {rate['status_codes']} — no throttling observed"

    plain = f["plain"]
    pw = f["playwright"]
    active = pw if pw and pw["has_table"] else plain

    columns_text = ", ".join(f["columns"]) if f["columns"] else "(none extracted)"
    sample_text = " | ".join(f["sample_row"]) if f["sample_row"] else "(none extracted)"

    resp_headers_text = format_headers(active.get("response_headers", {}))

    section = f"""
## /{f['name']}

**URL:** {f['url']}
**Method:** {f['method']}
**Rendering:** {f['rendering_mode']}
**Status:** {status_icon}

### Request

- Form data / params: {_request_params(f['name'])}
- Headers sent: User-Agent, Accept, Accept-Language, Referer, X-Requested-With

### Response

- Status code: {active.get('status_code', 'N/A')}
- Content-Type: {active.get('content_type', 'N/A')}
- Response size: {active.get('size_bytes', 0):,} bytes
- Columns observed (from <th> tags): {columns_text}
- Sample row: {sample_text}

### Response Headers

{resp_headers_text}

### Rate Limit Behavior

{rate_text}

### Anomalies

{_anomalies(f)}

---"""
    return section


def _request_params(name: str) -> str:
    if name == "historical":
        return f"symbol=ENGRO, start={START_DATE}, end={END_DATE} (POST form data)"
    return "None (GET)"


def _anomalies(f: dict) -> str:
    notes = []
    plain = f["plain"]
    pw = f["playwright"]

    if plain["error"]:
        notes.append(f"plain HTTP error: {plain['error']}")
    if pw and pw["error"]:
        notes.append(f"Playwright error: {pw['error']}")
    if plain["status_code"] and plain["status_code"] not in (200, 201):
        notes.append(f"Unexpected HTTP status on plain request: {plain['status_code']}")
    if not plain["has_table"] and pw is None:
        notes.append("No HTML table found and Playwright was not attempted (--no-playwright?)")
    if not notes:
        return "- None"
    return "\n".join(f"- {n}" for n in notes)


def write_report(results: list[dict]) -> Path:
    """Write docs/PSX_ENDPOINTS.md from probe results."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DOCS_DIR / "PSX_ENDPOINTS.md"

    status_map = {
        "working": "✓ Working",
        "partial": "⚠ Partial",
        "error": "✗ Error",
    }

    summary_rows = "\n".join(
        f"| /{r['name']:<20} | {r['method']:<6} | {r['rendering_mode']:<35} "
        f"| {status_map.get(r['status'], '?'):<12} | |"
        for r in results
    )

    sections = "\n".join(render_endpoint_section(r) for r in results)

    content = f"""# PSX Endpoints — Live Probe Results

**Last probed:** {date.today()}
**Probe script:** `python tools/probe_endpoints.py`

---

## Summary Table

| Endpoint               | Method | Rendering                            | Status       | Notes |
|------------------------|--------|--------------------------------------|--------------|-------|
{summary_rows}

---
{sections}
"""
    output_path.write_text(content, encoding="utf-8")
    return output_path


ENDPOINTS = [
    {
        "name": "historical",
        "url": f"{BASE_URL}/historical",
        "method": "POST",
        "data": {"symbol": "ENGRO", "start": str(START_DATE), "end": str(END_DATE)},
    },
    {"name": "indices", "url": f"{BASE_URL}/indices", "method": "GET", "data": None},
    {"name": "sector-summary", "url": f"{BASE_URL}/sector-summary", "method": "GET", "data": None},
    {"name": "financial-reports", "url": f"{BASE_URL}/financial-reports", "method": "GET", "data": None},
    {"name": "screener", "url": f"{BASE_URL}/screener", "method": "GET", "data": None},
    {"name": "trading-panel", "url": f"{BASE_URL}/trading-panel", "method": "GET", "data": None},
    {"name": "debt-market", "url": f"{BASE_URL}/debt-market", "method": "GET", "data": None},
    {"name": "eligible-scrips", "url": f"{BASE_URL}/eligible-scrips", "method": "GET", "data": None},
]

JS_ENDPOINTS = {"screener", "trading-panel"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe PSX endpoints and write PSX_ENDPOINTS.md")
    parser.add_argument("--endpoint", help="Probe only this endpoint name (e.g. historical)")
    parser.add_argument("--no-playwright", action="store_true", help="Skip JS-rendered endpoints")
    args = parser.parse_args()

    targets = ENDPOINTS
    if args.endpoint:
        targets = [e for e in ENDPOINTS if e["name"] == args.endpoint]
        if not targets:
            print(f"Unknown endpoint: {args.endpoint}")
            print(f"Valid names: {[e['name'] for e in ENDPOINTS]}")
            sys.exit(1)

    use_playwright = not args.no_playwright
    results = []

    print(f"Probing {len(targets)} endpoint(s)...")
    for ep in targets:
        skip_pw = ep["name"] in JS_ENDPOINTS and not use_playwright
        if skip_pw:
            print(f"  Skipping {ep['name']} (--no-playwright)")
            continue
        result = probe_endpoint(
            name=ep["name"],
            url=ep["url"],
            method=ep["method"],
            data=ep.get("data"),
            use_playwright=use_playwright,
        )
        results.append(result)

    if results:
        out = write_report(results)
        print(f"\nReport written to: {out}")
    else:
        print("No endpoints probed.")


if __name__ == "__main__":
    main()
