"""Experiment 8 screenshot helper (Playwright, 1400x900).

Usage
  python scripts/exp8_screenshots.py web       <URL>                 # cloud_health, cloud_swagger_docs
  python scripts/exp8_screenshots.py swagger   <URL> <case> [...]    # case: 200 | unsupported | 422 | 503
  python scripts/exp8_screenshots.py terminal  <txt> <png> "<title>" # render saved terminal text to PNG
  python scripts/exp8_screenshots.py github    <repo URL>            # github_readme_exp8.png (public page)

Playwright does not render the browser address bar, so every web screenshot gets a small
caption bar with the URL that was opened. Output goes to docs/screenshots/.
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
VIEWPORT = {"width": 1400, "height": 900}

SWAGGER_CASES = {
    "200": ("cloud_swagger_ask_200.png", "How many days do I have to return an unused item?"),
    "unsupported": ("cloud_swagger_ask_unsupported.png", "Do you sell car insurance?"),
    "422": ("cloud_swagger_ask_422.png", ""),
    "503": ("cloud_swagger_ask_503.png", "How do I return an item?"),
}


def add_caption(page: Page, text: str) -> None:
    """Fixed caption bar (stands in for the address bar that Playwright does not draw)."""
    page.evaluate(
        """(text) => {
            const bar = document.createElement('div');
            bar.textContent = text;
            Object.assign(bar.style, {position: 'fixed', top: '0', left: '0', right: '0', zIndex: 99999,
              background: '#1f2937', color: '#f9fafb', font: '13px/1 monospace', padding: '7px 12px'});
            document.body.appendChild(bar);
            document.body.style.paddingTop = '28px';
        }""",
        text,
    )


def shot_web(page: Page, base: str) -> None:
    for path, name in (("/health", "cloud_health.png"), ("/docs", "cloud_swagger_docs.png")):
        url = base + path
        page.goto(url, wait_until="networkidle")
        if path == "/docs":
            page.wait_for_selector(".opblock-summary")
        add_caption(page, f"GET {url}")
        page.screenshot(path=str(OUT / name))
        print("saved", OUT / name)


def shot_swagger(page: Page, base: str, case: str) -> None:
    name, question = SWAGGER_CASES[case]
    url = base + "/docs"
    page.goto(url, wait_until="networkidle")
    block = page.locator(".opblock-post").filter(has_text="/ask").first
    block.locator(".opblock-summary").click()
    block.get_by_role("button", name="Try it out").click()
    body = block.locator("textarea.body-param__text")
    body.fill('{\n  "question": ' + (f'"{question}"' if case != "422" else '""') + "\n}")
    block.get_by_role("button", name="Execute").click()
    block.locator(".live-responses-table .response-col_status").first.wait_for(timeout=120_000)
    page.wait_for_timeout(500)
    # Put "Server response" (code, body, headers) at the top of the viewport.
    block.locator(".live-responses-table").first.evaluate(
        "el => { el.scrollIntoView({block: 'start'}); window.scrollBy(0, -70); }")
    add_caption(page, f"{url}  —  POST /ask  (case: {case})")
    page.screenshot(path=str(OUT / name))
    print("saved", OUT / name)


def shot_terminal(page: Page, txt: str, png: str, title: str) -> None:
    text = Path(txt).read_text(encoding="utf-8")
    page.set_content(f"""<!doctype html><html><body style="margin:0;background:#0d1117">
      <div style="background:#30363d;color:#c9d1d9;font:13px monospace;padding:8px 14px">● ● ●&nbsp;&nbsp; {html.escape(title)}</div>
      <pre style="margin:0;padding:14px;color:#e6edf3;font:13px/1.45 'DejaVu Sans Mono',monospace;white-space:pre-wrap;word-break:break-all">{html.escape(text)}</pre>
      </body></html>""")
    page.screenshot(path=png, full_page=True)
    print("saved", png)


def shot_github(page: Page, repo_url: str) -> None:
    page.goto(repo_url, wait_until="domcontentloaded")
    anchor = page.locator("h2:has-text('Deployment (Experiment 8)')").first
    anchor.wait_for(timeout=60_000)
    page.wait_for_timeout(2000)          # let GitHub finish its own scroll restoration first
    anchor.evaluate("el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 40)")
    page.wait_for_timeout(800)
    add_caption(page, repo_url)
    page.screenshot(path=str(OUT / "github_readme_exp8.png"))
    print("saved", OUT / "github_readme_exp8.png")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    cmd, args = argv[0], argv[1:]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        if cmd == "web":
            shot_web(page, args[0].rstrip("/"))
        elif cmd == "swagger":
            for case in args[1:]:
                shot_swagger(page, args[0].rstrip("/"), case)
        elif cmd == "terminal":
            shot_terminal(page, args[0], args[1], args[2])
        elif cmd == "github":
            shot_github(page, args[0])
        else:
            print(__doc__)
            return 2
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
