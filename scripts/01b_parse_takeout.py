"""
阶段 2 兜底路径：解析 Google Takeout 导出的收藏列表 → my_places.csv
================================================================

什么时候用这个：
  - Playwright 主脚本被 Google 反爬挡住了
  - 你的列表是私人的（不能共享）
  - 想要最稳定可靠的数据来源

使用步骤：
  1. 去 https://takeout.google.com
  2. 点 "Deselect all"，只勾选 "Maps (your places)"
  3. 点 "Next step" → "Create export"，下载 ZIP
  4. 解压到 ~/Downloads/Takeout/
  5. 找到对应列表的 CSV，例如:
     ~/Downloads/Takeout/Saved/Want\\ to\\ go.csv
  6. 跑：
     python scripts/01b_parse_takeout.py \\
       --input "~/Downloads/Takeout/Saved/Want to go.csv"

Takeout CSV 列名通常是：Title, Note, URL, Comment
我们用 Playwright 访问 URL → 从最终 URL 提取经纬度 + 抓地址。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import re
from pathlib import Path
from typing import Optional

import pandas as pd
from playwright.async_api import async_playwright
from tqdm.asyncio import tqdm

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

LATLNG_RE = re.compile(r"/@(-?\d+\.\d{3,}),(-?\d+\.\d{3,})")


def parse_latlng(url: str) -> tuple[Optional[float], Optional[float]]:
    if not url:
        return None, None
    m = LATLNG_RE.search(url)
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


async def resolve(page, url: str) -> dict:
    """加载 URL，从最终重定向 URL 抽出 lat/lng + 地址。"""
    out = {"final_url": url, "lat": None, "lng": None, "address": ""}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1500)
        out["final_url"] = page.url
        out["lat"], out["lng"] = parse_latlng(page.url)
        for sel in (
            'button[data-item-id="address"]',
            'div[data-item-id="address"]',
            'button[aria-label*="Address"]',
        ):
            el = await page.query_selector(sel)
            if el:
                t = (await el.inner_text()).strip()
                if t:
                    out["address"] = t.split("\n")[0]
                    break
    except Exception as e:
        print(f"  ⚠️ 解析失败 {url[:60]}: {e}")
    return out


async def main(input_csv: Path, output_csv: Path):
    if not input_csv.exists():
        raise SystemExit(f"❌ 找不到文件：{input_csv}")

    df = pd.read_csv(input_csv)
    print(f"📂 Takeout CSV 读到 {len(df)} 行")
    print(f"   列名：{list(df.columns)}")

    # 字段名兼容
    title_col = next((c for c in ("Title", "Name", "title") if c in df.columns), None)
    url_col = next((c for c in ("URL", "Url", "url") if c in df.columns), None)
    note_col = next((c for c in ("Note", "Comment", "note") if c in df.columns), None)
    if not (title_col and url_col):
        raise SystemExit(f"❌ CSV 缺少必要列。需要 Title 和 URL，实际是 {list(df.columns)}")

    rows: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="en-US")
        page = await ctx.new_page()

        for _, r in tqdm(df.iterrows(), total=len(df), desc="解析"):
            name = str(r[title_col]).strip()
            url = str(r[url_col]).strip()
            note = str(r[note_col]).strip() if note_col else ""
            if not url or url.lower() == "nan":
                continue
            data = await resolve(page, url)
            rows.append(
                {
                    "name": name,
                    "address": data["address"],
                    "lat": data["lat"],
                    "lng": data["lng"],
                    "maps_url": data["final_url"],
                    "note": note,
                }
            )
        await browser.close()

    out_df = pd.DataFrame(rows)
    out_df.to_csv(output_csv, index=False, quoting=csv.QUOTE_ALL)
    print(f"\n✅ 写入 {output_csv}（{len(out_df)} 行）")

    # 数据质量
    n = len(out_df)
    n_coord = out_df[["lat", "lng"]].dropna().shape[0]
    n_addr = (out_df["address"].fillna("").str.len() > 0).sum()
    print("📊 质量报告：")
    print(f"  · 有经纬度: {n_coord}/{n} ({n_coord/n:.0%})")
    print(f"  · 有地址:   {n_addr}/{n} ({n_addr/n:.0%})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True, help="Takeout 导出的 CSV 路径")
    ap.add_argument("--output", type=Path, default=DATA_DIR / "my_places.csv")
    args = ap.parse_args()
    asyncio.run(main(args.input.expanduser(), args.output))
