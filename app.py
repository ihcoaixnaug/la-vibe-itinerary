"""
LA Vibe Itinerary - Streamlit 主应用
================================================================
启动：streamlit run app.py
浏览器自动打开 http://localhost:8501
"""
from __future__ import annotations

import ast
import json
import math
import os
from itertools import permutations
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sklearn.cluster import DBSCAN
from streamlit_folium import st_folium

# 加载 API Key（本地从 .env，部署时从 Streamlit Secrets）
load_dotenv()


def _get_api_key() -> str:
    """优先从 .env，其次从 Streamlit Secrets 取 Key。两个都没有就返回空字符串。"""
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        try:
            key = (st.secrets.get("OPENROUTER_API_KEY", "") or "").strip()
        except Exception:
            pass
    return key


OPENROUTER_API_KEY = _get_api_key()

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════
ROOT = Path(__file__).parent
DATA = ROOT / "data"
EARTH_RADIUS_KM = 6371.0
LA_CENTER = [34.05, -118.30]

CLUSTER_COLORS = [
    "#e74c3c", "#3498db", "#27ae60", "#f39c12",
    "#9b59b6", "#1abc9c", "#e67e22", "#34495e",
]

VIBE_ZH = {
    "casual": "随意", "trendy": "网红", "fine_dining": "正餐",
    "dive": "苍蝇馆子", "cozy": "温馨", "lively": "热闹", "romantic": "浪漫",
}
SCENARIO_ZH = {
    "date": "约会", "family": "家庭聚餐", "business_lunch": "商务午餐",
    "friends_group": "朋友聚会", "solo": "一人食",
    "celebration": "庆祝", "tourist_must": "游客必去",
}

st.set_page_config(
    page_title="LA Vibe Itinerary",
    page_icon="🌴",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ═══════════════════════════════════════════════════════════════
# 工具函数（从 03_cluster_routes 复用核心算法）
# ═══════════════════════════════════════════════════════════════
def haversine_km(lat1, lng1, lat2, lng2):
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = rlat2 - rlat1
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def dbscan_cluster(coords, eps_km=2.5, min_samples=2):
    coords_rad = np.radians(coords)
    eps_rad = eps_km / EARTH_RADIUS_KM
    db = DBSCAN(eps=eps_rad, min_samples=min_samples, metric="haversine")
    return db.fit_predict(coords_rad)


def optimize_route(places):
    n = len(places)
    if n <= 1:
        return list(range(n)), 0.0
    dist = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_km(places[i]["lat"], places[i]["lng"],
                             places[j]["lat"], places[j]["lng"])
            dist[i, j] = dist[j, i] = d
    if n <= 8:
        best_path, best_d = list(range(n)), float("inf")
        for perm in permutations(range(1, n)):
            path = (0,) + perm
            d = sum(dist[path[i], path[i + 1]] for i in range(n - 1))
            if d < best_d:
                best_d = d
                best_path = list(path)
        return best_path, best_d
    visited = [0]
    unvisited = set(range(1, n))
    total = 0.0
    while unvisited:
        cur = visited[-1]
        nxt = min(unvisited, key=lambda j: dist[cur, j])
        total += dist[cur, nxt]
        visited.append(nxt)
        unvisited.remove(nxt)
    return visited, total


def parse_list_field(val):
    if isinstance(val, list):
        return val
    if pd.isna(val) or not val:
        return []
    if isinstance(val, str) and val.strip().startswith("["):
        try:
            return ast.literal_eval(val)
        except Exception:
            return []
    return []


# ═══════════════════════════════════════════════════════════════
# AI Agent：自然语言查询 → 推荐 2-4 家店
# ═══════════════════════════════════════════════════════════════
def build_places_summary(df: pd.DataFrame) -> str:
    """把所有店压缩成 GPT-4o 易消化的紧凑列表。"""
    rows = []
    for _, r in df.iterrows():
        bf = ",".join(r["best_for"][:3]) if isinstance(r["best_for"], list) else ""
        mt = ",".join(r["must_try_dishes"][:2]) if isinstance(r["must_try_dishes"], list) else ""
        rows.append(
            f"- {r['name']} | {r['cuisine_primary']} | ${r['price_per_person_usd']} ({r['price_tier']}) | "
            f"vibe={r['vibe']} | noise={r['noise_level']} | reservation={r['reservation_needed']} | "
            f"best_for={bf} | wait≈{r['avg_wait_minutes']}min | "
            f"must_try={mt} | gem={r['hidden_gem_score']}/10 | "
            f"pitch_zh: {r['one_liner_zh']}"
        )
    return "\n".join(rows)


@st.cache_data(show_spinner=False, ttl=3600)
def ai_recommend(query: str, places_summary: str) -> dict:
    """调 GPT-4o 做语义推荐，返回 {recommended_names: [...], reasoning_zh: "..."}"""
    if not OPENROUTER_API_KEY:
        return {"error": "未配置 OPENROUTER_API_KEY（本地需 .env，部署需 Streamlit Secrets）"}
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )
        system = f"""You are a helpful AI restaurant concierge for Los Angeles dining. Your goal is to ALWAYS give the user 2-4 actionable recommendations from the database — never refuse, never return empty results.

DATABASE (each line: name | cuisine | price | attributes | dishes | pitch):
{places_summary}

Return ONLY a JSON object:
{{
  "recommended_names": ["exact name 1", "exact name 2", ...],
  "reasoning_zh": "用中文 2-3 句话给出推荐理由，必须为每家店指出具体匹配点，如果某约束略有偏差要诚实说明（例：'Cassia 略超预算 $10 但氛围完美匹配约会需求'）"
}}

CRITICAL RULES:
1. ALWAYS return 2-4 recommendations — find the BEST AVAILABLE, not perfect matches.
2. Soft matching > strict matching:
   - 预算超 10-30%：可推荐，标注 "略超预算 $X"
   - 噪音 moderate 当 quiet 用：可推荐，标注 "中等吵但氛围 OK"
   - 等位 ≤30 分钟当 "无需排队"：可推荐
3. Names MUST exactly match the database (case-sensitive, punctuation matters).
4. ONLY return empty if database is literally empty or query is nonsensical (extremely rare).
5. reasoning_zh: Chinese, 2-3 sentences, mention WHY each is recommended + any trade-offs.

EXAMPLE for query "今晚约会，预算 $80，安静一点不排队":
{{
  "recommended_names": ["Republique", "Cassia", "Sushi Gen"],
  "reasoning_zh": "Republique 浪漫氛围 + 可订位无需排队，人均略高 $90 但氛围完美匹配；Cassia 在 Santa Monica 海边，中价 $60 安静雅致；Sushi Gen 是 DTLA 经典寿司，环境安静、订位即可，约 $70。"
}}
"""
        resp = client.chat.completions.create(
            model="openai/gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Query: {query}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=400,
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ═══════════════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════════════
@st.cache_data
def load_data():
    df = pd.read_csv(DATA / "enriched_places.csv")
    for col in ["cuisine_tags", "must_try_dishes", "best_for", "best_time_slots", "dietary_friendly"]:
        if col in df.columns:
            df[col] = df[col].apply(parse_list_field)
    df = df.dropna(subset=["lat", "lng"]).reset_index(drop=True)
    return df


csv_path = DATA / "enriched_places.csv"
if not csv_path.exists():
    st.error("❌ 没找到 data/enriched_places.csv，请先跑 `python scripts/02_process_data.py`")
    st.stop()

df = load_data()
total_data_points = len(df) * 20 + sum(
    sum(len(df.at[i, c]) for c in ["cuisine_tags", "must_try_dishes", "best_for", "best_time_slots", "dietary_friendly"] if isinstance(df.at[i, c], list))
    for i in range(len(df))
)


# ═══════════════════════════════════════════════════════════════
# Header
# ═══════════════════════════════════════════════════════════════
st.title("🌴 LA Vibe Itinerary")
st.caption("AI 驱动的洛杉矶美食决策助手  ·  Vibe Coding Demo  ·  2026")


# ═══════════════════════════════════════════════════════════════
# Sidebar：AI 自然语言查询 + 筛选
# ═══════════════════════════════════════════════════════════════
with st.sidebar:
    # ---- AI 自然语言查询 ----
    st.header("🤖 AI 决策助手")
    nl_query = st.text_area(
        "用一句话描述你想要的店",
        value=st.session_state.get("last_nl_query", ""),
        placeholder="例：今晚约会，预算 $80，安静一点不排队",
        height=80,
        key="nl_query_input",
    )
    nl_btn = st.button("✨ 让 AI 帮我选", use_container_width=True, type="primary")

    if nl_btn and nl_query.strip():
        st.session_state["last_nl_query"] = nl_query.strip()
    if st.session_state.get("last_nl_query") and st.button("🗑️ 清除 AI 推荐", use_container_width=True):
        st.session_state.pop("last_nl_query", None)
        st.rerun()

    st.divider()
    st.header("🎛️ 多维筛选")

    price_min = int(df.price_per_person_usd.min())
    price_max = int(df.price_per_person_usd.max())
    price_range = st.slider(
        "💰 人均预算 (USD)",
        min_value=price_min,
        max_value=price_max,
        value=(price_min, price_max),
        step=5,
    )

    all_vibes = sorted(df.vibe.unique())
    selected_vibes = st.multiselect(
        "✨ 氛围（不选 = 不限）",
        options=all_vibes,
        default=[],
        format_func=lambda x: f"{VIBE_ZH.get(x, x)} ({x})",
    )

    all_scenarios = sorted({s for lst in df.best_for for s in lst})
    selected_scenarios = st.multiselect(
        "🎯 适合场景",
        options=all_scenarios,
        format_func=lambda x: f"{SCENARIO_ZH.get(x, x)}",
    )

    all_cuisines = sorted(df.cuisine_primary.unique())
    selected_cuisines = st.multiselect("🍴 主菜系", options=all_cuisines)

    min_gem = st.slider(
        "💎 最低 Hidden Gem 度", 1, 10, 1,
        help="越高越小众，1=人尽皆知，10=本地宝藏",
    )

    st.divider()
    generate_btn = st.button("🚀 一键生成行程", use_container_width=True, type="primary")

    st.divider()
    st.caption(
        f"📊 数据库：{len(df)} 家店  ·  {total_data_points}+ 个 AI 标签\n\n"
        "🤖 GPT-4o 自动归纳人均/招牌菜/氛围/场景\n\n"
        "🌐 DBSCAN 聚类 + TSP 最优路径"
    )


# ═══════════════════════════════════════════════════════════════
# 应用筛选
# ═══════════════════════════════════════════════════════════════
mask = pd.Series([True] * len(df), index=df.index)
mask &= df.price_per_person_usd.between(*price_range)
if selected_vibes:
    mask &= df.vibe.isin(selected_vibes)
if selected_scenarios:
    mask &= df.best_for.apply(lambda x: any(s in x for s in selected_scenarios))
if selected_cuisines:
    mask &= df.cuisine_primary.isin(selected_cuisines)
mask &= df.hidden_gem_score >= min_gem
filtered = df[mask].reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════
# AI Agent：自然语言推荐（在所有店里选，无视筛选）
# ═══════════════════════════════════════════════════════════════
ai_rec: dict = {}
ai_rec_names: set = set()
if st.session_state.get("last_nl_query"):
    with st.spinner(f"🤔 GPT-4o 正在分析需求：{st.session_state['last_nl_query']}"):
        ai_rec = ai_recommend(st.session_state["last_nl_query"], build_places_summary(df))
    if ai_rec.get("error"):
        st.error(f"❌ AI 推荐失败：{ai_rec['error']}")
    else:
        ai_rec_names = set(ai_rec.get("recommended_names", []))
        if ai_rec_names:
            st.markdown(
                f"""<div style='background:linear-gradient(135deg,#fff8e1,#fff3c4);
                padding:16px 20px;border-radius:12px;border-left:5px solid #f39c12;
                margin-bottom:16px'>
                <div style='font-size:13px;color:#7d6608;margin-bottom:8px'>
                💬 你说："{st.session_state['last_nl_query']}"
                </div>
                <div style='font-size:18px;font-weight:600;color:#1a1a1a;margin-bottom:6px'>
                🎯 AI 推荐：{' · '.join(ai_rec_names)}
                </div>
                <div style='color:#444;line-height:1.6'>
                💭 {ai_rec.get('reasoning_zh', '（无推理）')}
                </div>
                </div>""",
                unsafe_allow_html=True,
            )
        else:
            st.warning(f"🤖 AI 没找到匹配项：{ai_rec.get('reasoning_zh', '尝试放宽需求')}")


# ═══════════════════════════════════════════════════════════════
# 顶部指标
# ═══════════════════════════════════════════════════════════════
m1, m2, m3, m4 = st.columns(4)
m1.metric("命中店铺", f"{len(filtered)} / {len(df)}")
m2.metric("AI 标签数", f"{total_data_points}+")
m3.metric("AI 推荐", f"{len(ai_rec_names)} 家" if ai_rec_names else "未启用")
m4.metric(
    "最低人均",
    f"${filtered.price_per_person_usd.min() if len(filtered) else 0}",
)


# ═══════════════════════════════════════════════════════════════
# 行程生成（重新聚类筛选后的店）
# ═══════════════════════════════════════════════════════════════
itinerary_clusters: list[dict] = []
if generate_btn:
    if len(filtered) >= 2:
        coords = filtered[["lat", "lng"]].values
        labels = dbscan_cluster(coords, eps_km=2.5, min_samples=2)
        filtered = filtered.assign(_cid=labels)

        for cid in sorted(set(labels) - {-1}):
            sub = filtered[filtered._cid == cid].reset_index(drop=True)
            places_list = sub.to_dict("records")
            order, total_km = optimize_route(places_list)
            ordered = [places_list[i] for i in order]
            itinerary_clusters.append({
                "id": int(cid),
                "places": ordered,
                "total_km": total_km,
            })
    else:
        st.warning("⚠️ 当前筛选结果不足 2 家，无法生成行程，请放宽条件。")


# ═══════════════════════════════════════════════════════════════
# 地图
# ═══════════════════════════════════════════════════════════════
st.subheader("🗺️ 美食地图" + ("  ·  📍 已生成打卡路线" if itinerary_clusters else ""))

m = folium.Map(location=LA_CENTER, zoom_start=11, tiles="cartodbpositron")

# 标记哪些店在行程内（带顺序号）
in_itinerary: dict[str, dict] = {}
for c in itinerary_clusters:
    color = CLUSTER_COLORS[c["id"] % len(CLUSTER_COLORS)]
    for i, p in enumerate(c["places"]):
        in_itinerary[p["name"]] = {"order": i, "color": color, "cluster": c["id"]}

    # 画簇内连线
    coords_path = [[p["lat"], p["lng"]] for p in c["places"]]
    folium.PolyLine(coords_path, color=color, weight=4, opacity=0.75, dash_array="5").add_to(m)


def make_popup(row, order=None):
    badge = ""
    if order is not None:
        badge = f"<span style='background:#1a1a1a;color:white;padding:2px 8px;border-radius:50%;font-weight:bold;margin-right:6px'>{order + 1}</span>"
    must_try = ", ".join(row["must_try_dishes"][:2]) if row["must_try_dishes"] else "—"
    return (
        f"<div style='font-family:system-ui,sans-serif;min-width:230px'>"
        f"<div style='font-size:14px;font-weight:bold;margin-bottom:4px'>{badge}{row['name']}</div>"
        f"<div style='color:#666;font-size:11px;margin-bottom:6px'>{row['cuisine_primary']}  ·  {row['price_tier']}  ·  ~${row['price_per_person_usd']}</div>"
        f"<div style='font-style:italic;color:#222;margin-bottom:6px'>{row['one_liner_zh']}</div>"
        f"<div style='font-size:11px;color:#444'>🍴 必点：{must_try}</div>"
        f"<div style='font-size:11px;color:#444'>✨ {VIBE_ZH.get(row['vibe'], row['vibe'])}  ·  💎 Gem {row['hidden_gem_score']}/10</div>"
        f"</div>"
    )


# 显示集合：筛选命中的 + AI 推荐的（即使被筛选过滤也强制显示）
display_names = set(filtered["name"].tolist()) | ai_rec_names
display_df = df[df["name"].isin(display_names)]

for _, row in display_df.iterrows():
    is_ai = row["name"] in ai_rec_names
    iti = in_itinerary.get(row["name"])

    if is_ai:
        # AI 推荐：金色五角星 marker，最显眼
        folium.Marker(
            location=[row["lat"], row["lng"]],
            popup=folium.Popup(make_popup(row), max_width=320),
            tooltip=f"⭐ AI 推荐：{row['name']}",
            icon=folium.DivIcon(
                icon_size=(40, 40),
                icon_anchor=(20, 20),
                html=(
                    '<div style="position:relative;width:40px;height:40px">'
                    '<div style="position:absolute;inset:0;background:radial-gradient(circle,#ffd54f,#ffa000);'
                    'border-radius:50%;animation:pulse 1.5s ease-in-out infinite;'
                    'border:3px solid white;box-shadow:0 0 12px rgba(255,193,7,0.6);'
                    'display:flex;align-items:center;justify-content:center;'
                    'font-size:22px">⭐</div></div>'
                    '<style>@keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.1)}}</style>'
                ),
            ),
        ).add_to(m)
    elif iti:
        # 行程内：编号 marker
        folium.Marker(
            location=[row["lat"], row["lng"]],
            popup=folium.Popup(make_popup(row, iti["order"]), max_width=320),
            tooltip=f"{iti['order'] + 1}. {row['name']}",
            icon=folium.DivIcon(
                icon_size=(34, 34),
                icon_anchor=(17, 17),
                html=(
                    f'<div style="background:{iti["color"]};color:white;width:30px;height:30px;'
                    f'border-radius:50%;display:flex;align-items:center;justify-content:center;'
                    f'font-weight:700;font-size:14px;border:2px solid white;'
                    f'box-shadow:0 2px 6px rgba(0,0,0,0.35)">{iti["order"] + 1}</div>'
                ),
            ),
        ).add_to(m)
    else:
        # 普通：圆点
        folium.CircleMarker(
            location=[row["lat"], row["lng"]],
            radius=8,
            popup=folium.Popup(make_popup(row), max_width=320),
            tooltip=row["name"],
            color="#666",
            fill=True,
            fill_color="#888",
            fill_opacity=0.7,
            weight=1,
        ).add_to(m)

st_folium(m, height=560, use_container_width=True, returned_objects=[])


# ═══════════════════════════════════════════════════════════════
# 行程详情
# ═══════════════════════════════════════════════════════════════
if itinerary_clusters:
    st.subheader("📍 推荐打卡顺序")
    for c in itinerary_clusters:
        color = CLUSTER_COLORS[c["id"] % len(CLUSTER_COLORS)]
        walk_min = int(c["total_km"] * 12)
        drive_min = int(c["total_km"] * 3 + len(c["places"]) * 5)
        with st.container(border=True):
            st.markdown(
                f"<h4><span style='color:{color};font-size:1.3em'>●</span> "
                f"路线 {c['id'] + 1} · "
                f"<span style='font-weight:400'>{len(c['places'])} 家店 · "
                f"{c['total_km']:.1f} km · 步行 {walk_min} 分 / 开车 {drive_min} 分</span></h4>",
                unsafe_allow_html=True,
            )
            for i, p in enumerate(c["places"]):
                cols = st.columns([0.4, 3, 1])
                cols[0].markdown(
                    f"<div style='background:{color};color:white;width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:bold'>{i + 1}</div>",
                    unsafe_allow_html=True,
                )
                cols[1].markdown(f"**{p['name']}** · {p['cuisine_primary']} · {p['price_tier']}")
                cols[1].caption(f"💬 {p['one_liner_zh']}")
                cols[2].metric("人均", f"${p['price_per_person_usd']}", label_visibility="collapsed")


# ═══════════════════════════════════════════════════════════════
# 卡片列表
# ═══════════════════════════════════════════════════════════════
st.subheader(f"📋 全部命中店铺（{len(filtered)}）")
if len(filtered) == 0:
    st.info("⚠️ 当前筛选条件下没有命中店铺，请放宽预算或场景。")
else:
    for _, row in filtered.iterrows():
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1, 1])
            with c1:
                st.markdown(f"### {row['name']}")
                st.caption(f"📍 {row['cuisine_primary']}  ·  {row['address']}")
                st.markdown(f"💬 *{row['one_liner_zh']}*")
                if row["must_try_dishes"]:
                    st.markdown(
                        "🍴 **必点**：" + " · ".join(f"`{d}`" for d in row["must_try_dishes"][:3])
                    )
                if row["cuisine_tags"]:
                    st.markdown(
                        "🏷️ " + "  ".join(f"`{t}`" for t in row["cuisine_tags"][:5])
                    )
                with st.expander("🤔 别去的时候"):
                    st.write(row["avoid_if_zh"])
                    st.caption(f"客群：{row['crowd_typical_zh']}")
            with c2:
                st.metric("人均", f"${row['price_per_person_usd']}")
                st.caption(f"价位 {row['price_tier']}")
                st.caption(f"✨ {VIBE_ZH.get(row['vibe'], row['vibe'])}")
            with c3:
                st.metric("Insta", f"{row['instagrammable_score']}/10")
                st.metric("Gem", f"{row['hidden_gem_score']}/10", label_visibility="collapsed")
                st.caption(f"⏱ 等位 ~{row['avg_wait_minutes']}min")


# ═══════════════════════════════════════════════════════════════
# Footer
# ═══════════════════════════════════════════════════════════════
with st.expander("ℹ️ 项目说明 & 技术栈"):
    st.markdown(f"""
**LA Vibe Itinerary** — 验证 AI Agent 将 LBS 长尾资产（用户私人收藏）转化为结构化决策资产的可行性。

| 阶段 | 实现 |
|---|---|
| 数据采集 | Playwright + Google Maps 共享列表 |
| AI 增强 | GPT-4o (via OpenRouter) · 20 维结构化标签 · Pydantic 校验 |
| 地理聚类 | scikit-learn DBSCAN（haversine 度量）|
| 路径优化 | 簇内 ≤8 家用暴力 TSP，>8 家用最近邻贪心 |
| 前端 | Streamlit + folium + streamlit-folium |

**当前数据**：{len(df)} 家店 × 20 字段 + 列表展开 ≈ **{total_data_points} 个颗粒度数据点**

**Vibe Coding 实践**：从业务意图描述 → Prompt 调优 → Web 部署，AI 全流程参与，
原型周期从传统 60+ 小时压缩到 20 小时（↓70%）。
""")
