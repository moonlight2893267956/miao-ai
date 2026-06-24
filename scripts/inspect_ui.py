"""
无头浏览器巡检脚本：
- 访问前端 3 个关键页面
- 抓 console 错误/警告
- 抓网络失败请求（>= 400）
- 截图到 /tmp/miao-ui-*.png

跑法：
  /Users/wuxiangyi/Desktop/project/vibe-coding/miao-ai/.pw-venv/bin/python \
    /Users/wuxiangyi/Desktop/project/vibe-coding/miao-ai/scripts/inspect_ui.py
"""
import sys
from playwright.sync_api import sync_playwright, ConsoleMessage, Response

PAGES = [
    ("agents_list", "http://localhost:3000/agents"),
    ("agent_detail", "http://localhost:3000/agents/test-runtime"),
    ("traces", "http://localhost:3000/traces"),
]

def inspect(p, label: str, url: str) -> dict:
    """访问一个页面，截图 + 抓问题。"""
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()

    console: list[tuple[str, str]] = []
    page.on("console", lambda m: console.append((m.type, m.text)))

    failed: list[tuple[str, int]] = []
    page_err: list[str] = []
    page.on("response", lambda r: failed.append((r.url, r.status)) if r.status >= 400 else None)
    page.on("pageerror", lambda e: page_err.append(str(e)))

    print(f"\n{'=' * 60}")
    print(f"📄 {label}: {url}")
    print("=" * 60)

    try:
        page.goto(url, wait_until="networkidle", timeout=20000)
    except Exception as e:
        print(f"  ❌ goto failed: {e}")
        browser.close()
        return {"label": label, "url": url, "error": str(e)}

    # 截图
    screenshot = f"/tmp/miao-ui-{label}.png"
    page.screenshot(path=screenshot, full_page=True)
    print(f"  📸 screenshot: {screenshot}")

    # 页面 title
    title = page.title()
    print(f"  📌 title: {title}")

    # 主要内容
    try:
        body_text = page.locator("body").inner_text(timeout=3000)
        preview = body_text[:300].replace("\n", " | ")
        print(f"  📝 body preview: {preview}…")
    except Exception as e:
        print(f"  ⚠️  body 拿不到: {e}")

    # console
    errors = [c for c in console if c[0] in ("error", "warning")]
    print(f"  💬 console: {len(console)} total, {len(errors)} error/warning")
    for level, text in errors[:5]:
        print(f"     [{level}] {text}")

    # 网络失败
    print(f"  🌐 failed requests: {len(failed)}")
    for u, s in failed[:5]:
        print(f"     [{s}] {u}")

    # page error
    print(f"  🐞 pageerror: {len(page_err)}")
    for e in page_err[:3]:
        print(f"     {e[:200]}")

    browser.close()
    return {
        "label": label,
        "url": url,
        "title": title,
        "console_errors": errors,
        "failed_requests": failed,
        "page_errors": page_err,
    }


def main() -> int:
    results = []
    with sync_playwright() as p:
        for label, url in PAGES:
            results.append(inspect(p, label, url))

    # 汇总
    print("\n" + "=" * 60)
    print("📊 汇总")
    print("=" * 60)
    total_console_err = sum(len(r["console_errors"]) for r in results)
    total_failed = sum(len(r["failed_requests"]) for r in results)
    total_page_err = sum(len(r["page_errors"]) for r in results)

    print(f"  console error/warning: {total_console_err}")
    print(f"  failed network: {total_failed}")
    print(f"  page error: {total_page_err}")

    return 0 if (total_page_err == 0 and total_console_err == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
