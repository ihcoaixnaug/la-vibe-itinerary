"""
Takeout CSV → my_places.csv（无需 Playwright，用 Nominatim 地理编码）
=======================================================================
适用场景：
  - 你有 Google Takeout 导出的 Saved/Default list.csv（无经纬度）
  - 不想/无法安装 Playwright 浏览器驱动
  - 使用 OpenStreetMap Nominatim 免费 API（1 req/s，无需 key）

使用：
  python scripts/01c_geocode_takeout.py \\
      --input "data/Takeout/Saved/Default list.csv" \\
      --city "Los Angeles, CA"

输出：data/my_places.csv（上传到 app 的格式：name, lat, lng, address）
"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "la-vibe-itinerary/1.0 (guanxiaochi99@gmail.com)"}

# 大洛杉矶地区边界框（viewbox 参数）
LA_BBOX = "-119.0,33.5,-117.0,34.8"  # min_lng, min_lat, max_lng, max_lat
LA_LAT_RANGE = (33.5, 34.8)
LA_LNG_RANGE = (-119.0, -117.0)


def _in_la(lat: float, lng: float) -> bool:
    return LA_LAT_RANGE[0] <= lat <= LA_LAT_RANGE[1] and LA_LNG_RANGE[0] <= lng <= LA_LNG_RANGE[1]


def geocode(name: str, city: str) -> tuple[float | None, float | None, str]:
    """用 Nominatim 查经纬度，只接受在大洛杉矶范围内的结果。"""
    queries = [f"{name}, {city}", f"{name}, Los Angeles, CA", name]
    for query in queries:
        try:
            resp = requests.get(
                NOMINATIM_URL,
                params={
                    "q": query,
                    "format": "json",
                    "limit": 10,
                    "addressdetails": 0,
                    "countrycodes": "us",
                },
                headers=HEADERS,
                timeout=10,
            )
            for r in resp.json():
                lat, lng = float(r["lat"]), float(r["lon"])
                if _in_la(lat, lng):
                    return lat, lng, r.get("display_name", "")
        except Exception as e:
            print(f"  ⚠️ 查询失败 [{name}]: {e}")
        time.sleep(1.1)
    return None, None, ""


def main(input_csv: Path, output_csv: Path, city: str):
    if not input_csv.exists():
        raise SystemExit(f"❌ 找不到文件：{input_csv}")

    df = pd.read_csv(input_csv)
    print(f"📂 读到 {len(df)} 行，列：{list(df.columns)}")

    title_col = next((c for c in ("Title", "Name", "title", "name") if c in df.columns), None)
    if not title_col:
        raise SystemExit(f"❌ 找不到店名列，实际列：{list(df.columns)}")

    rows: list[dict] = []
    for _, r in tqdm(df.iterrows(), total=len(df), desc="地理编码"):
        name = str(r[title_col]).strip()
        if not name or name.lower() == "nan":
            continue
        lat, lng, addr = geocode(name, city)
        rows.append({"name": name, "lat": lat, "lng": lng, "address": addr})
        time.sleep(1.1)  # 确保满足 1 req/s 限制

    out = pd.DataFrame(rows)
    n = len(out)
    n_ok = out[["lat", "lng"]].dropna().shape[0]
    n_fail = n - n_ok

    out.to_csv(output_csv, index=False, quoting=csv.QUOTE_ALL)
    print(f"\n✅ 写入 {output_csv}")
    print(f"   成功：{n_ok}/{n}，失败：{n_fail}/{n}")
    if n_fail:
        failed = out[out["lat"].isna()]["name"].tolist()
        print(f"   未找到坐标：{failed}")
        print("   → 手动补充：打开 maps.google.com，搜索店名，URL 中 @34.xxx,-118.xxx 即为坐标")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=DATA_DIR / "my_places.csv")
    ap.add_argument("--city", default="Los Angeles, CA", help="城市上下文，提高匹配精度")
    args = ap.parse_args()
    main(args.input.expanduser(), args.output, args.city)
