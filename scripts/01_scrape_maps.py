"""
阶段 2 主路径：用 Playwright 抓取 Google Maps 共享列表 → my_places.csv
================================================================

为什么是"共享列表 URL"模式？
  - 不用登录，避免 Google 反自动化检测
  - 链接稳定，DOM 选择器最规范
  - 简历可以写："独立设计 Playwright 自动化方案，绕过登录直接采集公开 LBS 数据"

使用步骤：
  1. 在 Google Maps（手机或电脑）打开你的 "想去 LA" 列表
  2. 点 "Share" → 复制链接（形如 https://maps.app.goo.gl/XXXX）
  3. 跑：python scripts/01_scrape_maps.py --url "<上一步的链接>"

输出：data/my_places.csv（5 列：name, address, lat, lng, maps_url）

可选参数：
  --show-browser     显示浏览器窗口（调试用，看脚本在干什么）
  --max-items N      只抓前 N 个（快速测试用）
  --output PATH      自定义输出路径
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
from playwright.async_api import async_playwright, Page
from tqdm.asyncio import tqdm

# ---- 项目目录 ----
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# ---- 正则：从 Google Maps URL 提取经纬度 ----
# 例如 https://www.google.com/maps/place/Sun+Nong+Dan/@34.0625,-118.3008,17z/...
LATLNG_RE = re.compile(r"/@(-?\d+\.\d{3,}),(-?\d+\.\d{3,})")


def parse_latlng(url: str) -> tuple[Optional[float], Optional[float]]:
    """从 URL 中抽出 (lat, lng)，找不到就返回 (None, None)。"""
    if not url:
        return None, None
    m = LATLNG_RE.search(url)
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


async def scroll_until_loaded(page: Page, max_scrolls: int = 40) -> int:
    """自动发现滚动容器并加载所有店铺（兼容共享列表 / 搜索结果 / 自定义列表）。"""
    # 1) 先等任意 place 链接出现
    try:
        await page.wait_for_selector('a[href*="/maps/place/"]', timeout=20000)
    except Exception:
        snap = DATA_DIR / "debug_no_links.png"
        await page.screenshot(path=str(snap), full_page=True)
        html_path = DATA_DIR / "debug_page.html"
        html_path.write_text(await page.content(), encoding="utf-8")
        print(
            f"⚠️ 20 秒内未找到任何 place 链接。\n"
            f"   截图已保存：{snap}\n"
            f"   HTML 已保存：{html_path}\n"
            f"   把这两个文件给我看，我会改选择器。"
        )
        return 0

    # 2) 在浏览器里动态找出包含最多 place 链接的滚动容器
    print("🔍 自动定位滚动容器中...")
    last_count = -1
    stable_rounds = 0

    for i in range(max_scrolls):
        scrolled_ok = await page.evaluate(
            """() => {
                const links = document.querySelectorAll('a[href*="/maps/place/"]');
                if (!links.length) { window.scrollBy(0, 800); return false; }
                // 从第一个 link 往上找真正可滚动的祖先
                let el = links[0].parentElement;
                while (el) {
                    const cs = getComputedStyle(el);
                    const overflowY = cs.overflowY;
                    if (el.scrollHeight - el.clientHeight > 50 &&
                        (overflowY === 'auto' || overflowY === 'scroll')) {
                        el.scrollBy(0, Math.max(800, el.clientHeight * 0.8));
                        return true;
                    }
                    el = el.parentElement;
                }
                window.scrollBy(0, 800);
                return false;
            }"""
        )
        await page.wait_for_timeout(800)
        count = await page.locator('a[href*="/maps/place/"]').count()
        if count == last_count:
            stable_rounds += 1
            if stable_rounds >= 4:
                break
        else:
            stable_rounds = 0
        last_count = count
        if i == 0 and not scrolled_ok:
            print("  ⚠️ 没找到独立滚动容器，改用 window 滚动")

    return last_count if last_count > 0 else 0


async def collect_links(page: Page, max_items: Optional[int] = None) -> list[dict]:
    """从全页收集所有 Google Maps place 链接（自动去重）。"""
    locators = page.locator('a[href*="/maps/place/"]')
    raw_total = await locators.count()
    print(f"📍 全页 place 链接 {raw_total} 个，去重中...")

    seen: set[str] = set()
    rows: list[dict] = []
    for i in range(raw_total):
        link = locators.nth(i)
        try:
            url = await link.get_attribute("href")
            if not url:
                continue
            # 用 /place/<NAME>/ 部分作去重 key
            key = url.split("?")[0].split("/data=")[0]
            if key in seen:
                continue
            seen.add(key)

            aria = await link.get_attribute("aria-label") or ""
            name = aria.strip().split("\n")[0] or f"未命名_{i}"
            lat, lng = parse_latlng(url)
            rows.append(
                {
                    "name": name,
                    "address": "",
                    "lat": lat,
                    "lng": lng,
                    "maps_url": url,
                }
            )
            if max_items and len(rows) >= max_items:
                break
        except Exception as e:
            print(f"  ⚠️ 第 {i} 项跳过: {e}")
    return rows


async def enrich_one(page: Page, row: dict) -> dict:
    """打开每家店的详情页，补齐 lat/lng/address。"""
    if not row["maps_url"]:
        return row
    try:
        await page.goto(row["maps_url"], wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1500)

        # 经纬度兜底（可能首次只在最终 URL 里）
        if row["lat"] is None:
            lat, lng = parse_latlng(page.url)
            row["lat"], row["lng"] = lat, lng

        # 地址（多个选择器兜底，因为 Google 改 DOM 频繁）
        for sel in (
            'button[data-item-id="address"]',
            'div[data-item-id="address"]',
            'button[aria-label*="Address"]',
        ):
            el = await page.query_selector(sel)
            if el:
                txt = (await el.inner_text()).strip()
                if txt:
                    row["address"] = txt.split("\n")[0]
                    break
    except Exception as e:
        print(f"  ⚠️ 补全 {row.get('name')} 失败: {e}")
    return row


async def run(list_url: str, output: Path, headless: bool, max_items: Optional[int]):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(
            locale="en-US",
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        print(f"🌐 打开列表：{list_url}")
        await page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        n = await scroll_until_loaded(page)
        print(f"✅ 滚动完成，共加载 {n} 个店铺")

        rows = await collect_links(page, max_items=max_items)

        print("📋 进入详情页补地址（每家约 2 秒）...")
        for row in tqdm(rows):
            await enrich_one(page, row)

        await browser.close()

    df = pd.DataFrame(rows)
    df.to_csv(output, index=False, quoting=csv.QUOTE_ALL)
    print(f"\n✅ 写入 {output}（{len(df)} 行）")
    summary(df)


def summary(df: pd.DataFrame) -> None:
    n = len(df)
    n_coord = df[["lat", "lng"]].dropna().shape[0]
    n_addr = (df["address"].fillna("").str.len() > 0).sum()
    print("\n📊 数据质量报告：")
    print(f"  · 有经纬度:  {n_coord}/{n}  ({n_coord/n:.0%})")
    print(f"  · 有地址:    {n_addr}/{n}  ({n_addr/n:.0%})")
    if n_coord < n:
        print(
            f"  ⚠️ 有 {n - n_coord} 行缺经纬度，"
            f"进入阶段 4 (地理聚类) 前需要补全。"
        )


def main():
    ap = argparse.ArgumentParser(description="Scrape Google Maps shared list → CSV")
    ap.add_argument("--url", required=True, help="Google Maps 共享列表 URL")
    ap.add_argument(
        "--output",
        type=Path,
        default=DATA_DIR / "my_places.csv",
        help="输出 CSV 路径（默认 data/my_places.csv）",
    )
    ap.add_argument("--show-browser", action="store_true", help="显示浏览器窗口（调试用）")
    ap.add_argument("--max-items", type=int, default=None, help="只抓前 N 项")
    args = ap.parse_args()

    try:
        asyncio.run(
            run(
                args.url,
                args.output,
                headless=not args.show_browser,
                max_items=args.max_items,
            )
        )
    except KeyboardInterrupt:
        print("\n⛔ 用户中止")
        sys.exit(1)


if __name__ == "__main__":
    main()
