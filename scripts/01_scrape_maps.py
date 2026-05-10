"""
阶段 2 主路径：用 Playwright 抓取 Google Maps 共享列表 → my_places.csv
================================================================

使用步骤：
  1. 在 Google Maps（手机或电脑）打开你的列表
  2. 点 "Share" → 复制链接（形如 https://maps.app.goo.gl/XXXX）
  3. 跑：python scripts/01_scrape_maps.py --url "<上一步的链接>"

输出：data/my_places.csv（5 列：name, address, lat, lng, maps_url）

可选参数：
  --show-browser     显示浏览器窗口（调试用）
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
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# 视口坐标（/@lat,lng,zoom）：通常是地图中心，不一定是地点坐标
_VIEWPORT_RE = re.compile(r"/@(-?\d+\.\d{3,}),(-?\d+\.\d{3,})")
# 地点真实坐标，编码在 data= 参数里：!3d<lat>!4d<lng>
_DATA_COORD_RE = re.compile(r"!3d(-?\d+\.\d{4,})!4d(-?\d+\.\d{4,})")

# 共享列表的地点按钮选择器（Google Maps 用 button 而非 <a href> 渲染列表项）
_BTN_SEL = "button.SMP2wb"
_NAME_SEL = "div.fontHeadlineSmall"


def parse_latlng(url: str) -> tuple[Optional[float], Optional[float]]:
    """从 Google Maps URL 解析经纬度。
    优先取 data= 参数里的 !3d...!4d...（地点坐标），
    兜底取 /@lat,lng（视口中心，共享列表点击后可能不准）。
    """
    if not url:
        return None, None
    m = _DATA_COORD_RE.search(url)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = _VIEWPORT_RE.search(url)
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


# ── 路径 A：基于 <a href="/maps/place/"> 链接（旧版 / 搜索结果页）──

async def scroll_until_loaded(page: Page, max_scrolls: int = 40) -> int:
    """自动滚动加载所有 place 链接（适用于搜索结果 / 旧版列表）。"""
    try:
        await page.wait_for_selector('a[href*="/maps/place/"]', timeout=15000)
    except Exception:
        return 0

    print("🔍 自动定位滚动容器中...")
    last_count = -1
    stable_rounds = 0
    for i in range(max_scrolls):
        await page.evaluate(
            """() => {
                const links = document.querySelectorAll('a[href*="/maps/place/"]');
                if (!links.length) { window.scrollBy(0, 800); return; }
                let el = links[0].parentElement;
                while (el) {
                    const cs = getComputedStyle(el);
                    const oy = cs.overflowY;
                    if (el.scrollHeight - el.clientHeight > 50 &&
                        (oy === 'auto' || oy === 'scroll')) {
                        el.scrollBy(0, Math.max(800, el.clientHeight * 0.8));
                        return;
                    }
                    el = el.parentElement;
                }
                window.scrollBy(0, 800);
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
    return last_count if last_count > 0 else 0


async def collect_links(page: Page, max_items: Optional[int] = None) -> list[dict]:
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
            key = url.split("?")[0].split("/data=")[0]
            if key in seen:
                continue
            seen.add(key)
            aria = await link.get_attribute("aria-label") or ""
            name = aria.strip().split("\n")[0] or f"未命名_{i}"
            lat, lng = parse_latlng(url)
            rows.append({"name": name, "address": "", "lat": lat, "lng": lng, "maps_url": url})
            if max_items and len(rows) >= max_items:
                break
        except Exception as e:
            print(f"  ⚠️ 第 {i} 项跳过: {e}")
    return rows


async def enrich_one(page: Page, row: dict) -> dict:
    """打开详情页补齐 lat/lng/address。"""
    if not row["maps_url"]:
        return row
    try:
        await page.goto(row["maps_url"], wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1500)
        if row["lat"] is None:
            row["lat"], row["lng"] = parse_latlng(page.url)
        for sel in ('button[data-item-id="address"]', 'div[data-item-id="address"]',
                    'button[aria-label*="Address"]'):
            el = await page.query_selector(sel)
            if el:
                txt = (await el.inner_text()).strip()
                if txt:
                    row["address"] = txt.split("\n")[0]
                    break
    except Exception as e:
        print(f"  ⚠️ 补全 {row.get('name')} 失败: {e}")
    return row


# ── 路径 B：基于按钮点击（共享列表 / 自定义列表）──

async def scroll_shared_list(page: Page, max_scrolls: int = 60) -> int:
    """滚动共享列表左侧面板直到所有地点按钮加载完毕。"""
    try:
        await page.wait_for_selector(_BTN_SEL, timeout=20000)
    except Exception:
        return 0

    print("🔍 滚动共享列表，加载全部地点…")
    last_count = -1
    stable_rounds = 0
    for _ in range(max_scrolls):
        await page.evaluate(
            """(sel) => {
                const btns = document.querySelectorAll(sel);
                if (!btns.length) { window.scrollBy(0, 800); return; }
                let el = btns[0].parentElement;
                while (el) {
                    const cs = getComputedStyle(el);
                    const oy = cs.overflowY;
                    if (el.scrollHeight - el.clientHeight > 50 &&
                        (oy === 'auto' || oy === 'scroll')) {
                        el.scrollBy(0, Math.max(600, el.clientHeight * 0.8));
                        return;
                    }
                    el = el.parentElement;
                }
                window.scrollBy(0, 800);
            }""",
            _BTN_SEL,
        )
        await page.wait_for_timeout(800)
        count = await page.locator(_BTN_SEL).count()
        if count == last_count:
            stable_rounds += 1
            if stable_rounds >= 4:
                break
        else:
            stable_rounds = 0
        last_count = count
    return last_count if last_count > 0 else 0


async def collect_by_clicking(page: Page, max_items: Optional[int] = None) -> list[dict]:
    """点击每个地点按钮 → 从详情页 URL 提取坐标 + 地址，然后回到列表。"""
    n_loaded = await scroll_shared_list(page)
    print(f"📍 共加载 {n_loaded} 个地点按钮")

    # 提前收集所有名字（点击后 DOM 可能失效，不能延迟取）
    btns = page.locator(_BTN_SEL)
    n = min(await btns.count(), max_items or 9999)
    names: list[str] = []
    for i in range(n):
        try:
            name_el = btns.nth(i).locator(_NAME_SEL).first
            names.append((await name_el.inner_text()).strip() or f"未命名_{i}")
        except Exception:
            names.append(f"未命名_{i}")

    rows: list[dict] = []
    for i, name in enumerate(tqdm(names, desc="点击抓取")):
        row: dict = {"name": name, "address": "", "lat": None, "lng": None, "maps_url": ""}
        try:
            btn = page.locator(_BTN_SEL).nth(i)
            await btn.scroll_into_view_if_needed()
            await btn.click()

            # 等 URL 变成详情页（含 /@lat,lng）
            for _ in range(20):
                await page.wait_for_timeout(500)
                if parse_latlng(page.url)[0] is not None:
                    break

            url = page.url
            row["maps_url"] = url
            row["lat"], row["lng"] = parse_latlng(url)

            # 补地址
            for sel in ('button[data-item-id="address"]', 'div[data-item-id="address"]',
                        'button[aria-label*="Address"]'):
                el = await page.query_selector(sel)
                if el:
                    txt = (await el.inner_text()).strip()
                    if txt:
                        row["address"] = txt.split("\n")[0]
                        break

            # 回到列表（浏览器后退）
            await page.go_back()
            await page.wait_for_timeout(1200)
            # 等待按钮重新出现，最多 15 秒
            await page.wait_for_selector(_BTN_SEL, timeout=15000)

        except Exception as e:
            print(f"  ⚠️ [{name}] 失败: {e}")
            try:
                await page.go_back()
                await page.wait_for_timeout(1200)
                await page.wait_for_selector(_BTN_SEL, timeout=10000)
            except Exception:
                pass

        rows.append(row)

    return rows


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

        # 路径 A：先找 <a href> 链接（搜索结果 / 旧格式）
        n_links = await scroll_until_loaded(page)
        rows = await collect_links(page, max_items=max_items)

        if rows:
            print("📋 进入详情页补地址（每家约 2 秒）...")
            for row in tqdm(rows, desc="补地址"):
                await enrich_one(page, row)
        else:
            # 路径 B：共享列表，用点击方式
            print("🔄 未检测到 place 链接，改用共享列表点击方式…")
            rows = await collect_by_clicking(page, max_items=max_items)

        await browser.close()

    df = pd.DataFrame(rows)
    df.to_csv(output, index=False, quoting=csv.QUOTE_ALL)
    print(f"\n✅ 写入 {output}（{len(df)} 行）")
    summary(df)


def summary(df: pd.DataFrame) -> None:
    n = len(df)
    if n == 0:
        print("⚠️ 没有抓到任何数据")
        return
    n_coord = df[["lat", "lng"]].dropna().shape[0]
    n_addr = (df["address"].fillna("").str.len() > 0).sum()
    print("\n📊 数据质量报告：")
    print(f"  · 有经纬度:  {n_coord}/{n}  ({n_coord/n:.0%})")
    print(f"  · 有地址:    {n_addr}/{n}  ({n_addr/n:.0%})")
    if n_coord < n:
        missing = df[df["lat"].isna()]["name"].tolist()
        print(f"  ⚠️ 缺坐标：{missing}")


def main():
    ap = argparse.ArgumentParser(description="Scrape Google Maps shared list → CSV")
    ap.add_argument("--url", required=True, help="Google Maps 共享列表 URL")
    ap.add_argument("--output", type=Path, default=DATA_DIR / "my_places.csv")
    ap.add_argument("--show-browser", action="store_true", help="显示浏览器窗口（调试用）")
    ap.add_argument("--max-items", type=int, default=None, help="只抓前 N 项")
    args = ap.parse_args()
    try:
        asyncio.run(run(args.url, args.output, headless=not args.show_browser,
                        max_items=args.max_items))
    except KeyboardInterrupt:
        print("\n⛔ 用户中止")
        sys.exit(1)


if __name__ == "__main__":
    main()
