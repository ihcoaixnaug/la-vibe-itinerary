"""
阶段 4：地理聚类 + 簇内路径优化
================================================================
读取 data/enriched_places.csv → DBSCAN 聚类 → TSP/贪心 排序 → data/routes.json

算法：
  · DBSCAN（haversine 度量）：把地理上接近的店自动分组，无需预设簇数
  · 簇内路径优化：≤8 家用暴力 TSP（最优解）；>8 家用最近邻贪心（足够好）
  · Outlier（孤立点）合并到 "其他独立点" 簇

为什么 DBSCAN 而不是 K-means？
  · 不用预设 K（用户随手收藏的店分布是任意的）
  · 能识别孤立点（一家店在郊区 ≠ 强制塞进其他簇）
  · 对密度差异敏感，符合"商圈 vs 散点"的真实模式

用法：
  python scripts/03_cluster_routes.py
  python scripts/03_cluster_routes.py --eps-km 1.5     # 更紧凑的簇
  python scripts/03_cluster_routes.py --min-samples 3   # 簇至少 3 家店才成立
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from itertools import permutations
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两点之间的球面距离（km）。"""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = rlat2 - rlat1
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def dbscan_cluster(coords: np.ndarray, eps_km: float, min_samples: int) -> np.ndarray:
    """用 haversine 度量的 DBSCAN，返回每个点的簇标签（-1 表示孤立点）。"""
    coords_rad = np.radians(coords)
    eps_rad = eps_km / EARTH_RADIUS_KM
    db = DBSCAN(eps=eps_rad, min_samples=min_samples, metric="haversine")
    return db.fit_predict(coords_rad)


def optimize_route(places: list[dict]) -> tuple[list[int], float]:
    """
    簇内最短打卡顺序。
    返回：(下标序列, 总距离 km)
    """
    n = len(places)
    if n <= 1:
        return list(range(n)), 0.0

    # 距离矩阵
    dist = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_km(
                places[i]["lat"], places[i]["lng"],
                places[j]["lat"], places[j]["lng"],
            )
            dist[i, j] = dist[j, i] = d

    # 暴力 TSP（簇 ≤ 8）：8! = 40320，毫秒级
    if n <= 8:
        best_path = None
        best_dist = float("inf")
        for perm in permutations(range(1, n)):
            path = (0,) + perm
            d = sum(dist[path[i], path[i + 1]] for i in range(n - 1))
            if d < best_dist:
                best_dist = d
                best_path = list(path)
        return best_path, best_dist

    # 最近邻贪心（簇 > 8）
    visited = [0]
    unvisited = set(range(1, n))
    total = 0.0
    while unvisited:
        current = visited[-1]
        nearest = min(unvisited, key=lambda j: dist[current, j])
        total += dist[current, nearest]
        visited.append(nearest)
        unvisited.remove(nearest)
    return visited, total


LA_ZIP_TO_NEIGHBORHOOD = {
    # DTLA core
    "90012": "DTLA / Chinatown", "90013": "DTLA", "90014": "DTLA",
    "90015": "DTLA", "90017": "DTLA", "90021": "DTLA Arts District",
    # Koreatown / Mid-Wilshire
    "90004": "Hancock Park", "90005": "Koreatown", "90006": "Koreatown",
    "90019": "Mid-City", "90020": "Koreatown",
    # Hollywood corridor
    "90028": "Hollywood", "90038": "Hollywood",
    "90036": "Fairfax / La Brea",
    "90048": "Beverly Grove", "90069": "West Hollywood",
    # Eastside
    "90026": "Echo Park / Silver Lake",
    "90027": "Los Feliz",
    "90029": "East Hollywood",
    # Westside
    "90024": "Westwood", "90025": "West LA", "90034": "Palms",
    "90064": "West LA", "90067": "Century City",
    # Beach cities
    "90291": "Venice", "90292": "Marina del Rey",
    "90401": "Santa Monica", "90403": "Santa Monica",
    "90404": "Santa Monica", "90405": "Santa Monica",
    # Beverly Hills
    "90210": "Beverly Hills", "90211": "Beverly Hills", "90212": "Beverly Hills",
    # Other
    "90057": "Westlake / MacArthur Park", "90232": "Culver City",
    "90035": "Pico-Robertson", "90046": "West Hollywood",
}


def _extract_zip(addr: str) -> str:
    """从地址末尾抓出 5 位 zip code。"""
    for tok in str(addr).split():
        cleaned = "".join(c for c in tok if c.isdigit())
        if len(cleaned) == 5:
            return cleaned
    return ""


def name_cluster(places: list[dict]) -> str:
    """从地址里推断片区名：先 zip 精确查表，再降级到 city，最后用占位。"""
    # 1) 优先用 zip → neighborhood 表
    zip_hits = []
    for p in places:
        z = _extract_zip(p.get("address", ""))
        nbhd = LA_ZIP_TO_NEIGHBORHOOD.get(z)
        if nbhd:
            zip_hits.append(nbhd)
    if zip_hits:
        return Counter(zip_hits).most_common(1)[0][0]

    # 2) 降级：取最常见的城市名
    cities = []
    for p in places:
        parts = [s.strip() for s in str(p.get("address", "")).split(",")]
        if len(parts) >= 3:
            cities.append(parts[1])
    if cities:
        return Counter(cities).most_common(1)[0][0]
    return ""


def parse_list_field(val) -> list:
    """CSV 里 list 字段被存成 "['a','b','c']" 字符串，还原成 list。"""
    if isinstance(val, list):
        return val
    if pd.isna(val) or not val:
        return []
    if isinstance(val, str):
        val = val.strip()
        if val.startswith("["):
            try:
                import ast
                return ast.literal_eval(val)
            except Exception:
                return []
    return []


def main(args):
    df = pd.read_csv(args.input)
    df = df.dropna(subset=["lat", "lng"]).reset_index(drop=True)
    print(f"📂 输入：{len(df)} 家店（含经纬度）")

    coords = df[["lat", "lng"]].values
    labels = dbscan_cluster(coords, eps_km=args.eps_km, min_samples=args.min_samples)
    df["cluster_id"] = labels

    n_clusters = len(set(labels) - {-1})
    n_outliers = int((labels == -1).sum())
    print(f"🌐 聚类结果：eps={args.eps_km}km, min_samples={args.min_samples}")
    print(f"   {n_clusters} 个有效簇 + {n_outliers} 个孤立点")

    clusters_out = []

    # 处理每个簇
    for cid in sorted(set(labels) - {-1}):
        sub = df[df.cluster_id == cid].reset_index(drop=True)
        places_raw = sub.to_dict("records")

        # 还原 list 字段
        for p in places_raw:
            for f in ["cuisine_tags", "must_try_dishes", "best_for", "best_time_slots", "dietary_friendly"]:
                p[f] = parse_list_field(p.get(f))

        order_idx, total_km = optimize_route(places_raw)
        ordered = [{**places_raw[i], "visit_order": rank} for rank, i in enumerate(order_idx)]

        clusters_out.append({
            "id": int(cid),
            "name": name_cluster(places_raw) or f"区域 {cid + 1}",
            "size": len(ordered),
            "center": {
                "lat": float(sub.lat.mean()),
                "lng": float(sub.lng.mean()),
            },
            "total_distance_km": round(total_km, 2),
            "estimated_walk_minutes": int(total_km * 12),       # 5 km/h 步行
            "estimated_drive_minutes": int(total_km * 3 + len(ordered) * 5),  # 含停车换乘
            "places": ordered,
        })

    # 孤立点单独成"散点簇"
    outliers_df = df[df.cluster_id == -1]
    if len(outliers_df) > 0:
        outliers = outliers_df.to_dict("records")
        for p in outliers:
            for f in ["cuisine_tags", "must_try_dishes", "best_for", "best_time_slots", "dietary_friendly"]:
                p[f] = parse_list_field(p.get(f))
        clusters_out.append({
            "id": -1,
            "name": "其他独立点",
            "size": len(outliers),
            "center": {
                "lat": float(outliers_df.lat.mean()),
                "lng": float(outliers_df.lng.mean()),
            },
            "total_distance_km": 0.0,
            "estimated_walk_minutes": 0,
            "estimated_drive_minutes": len(outliers) * 15,
            "places": outliers,
        })

    output = {
        "meta": {
            "total_places": len(df),
            "n_clusters": n_clusters,
            "n_outliers": n_outliers,
            "eps_km": args.eps_km,
            "min_samples": args.min_samples,
        },
        "clusters": clusters_out,
    }

    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 写入：{args.output}")

    # 终端摘要
    print("\n📊 行程方案（按簇大小排序）：")
    print("─" * 78)
    for c in sorted(clusters_out, key=lambda x: -x["size"]):
        print(
            f"  簇 {c['id']:>2} | {c['name']:<22} | "
            f"{c['size']:>2} 家店 | 总路径 {c['total_distance_km']:>5.2f} km | "
            f"步行 ~{c['estimated_walk_minutes']:>2} 分 / 开车 ~{c['estimated_drive_minutes']:>2} 分"
        )
        for p in c["places"][:5]:
            order = p.get("visit_order", "·")
            print(f"        {order} → {p['name']}")
        if len(c["places"]) > 5:
            print(f"        ... 还有 {len(c['places']) - 5} 家")
        print()


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DATA / "enriched_places.csv")
    ap.add_argument("--output", type=Path, default=DATA / "routes.json")
    ap.add_argument("--eps-km", type=float, default=2.0, help="簇内最大邻接距离 (km)")
    ap.add_argument("--min-samples", type=int, default=2, help="形成簇的最少点数")
    return ap.parse_args()


if __name__ == "__main__":
    main(parse_args())
