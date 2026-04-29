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
import re
from itertools import permutations
from pathlib import Path
from urllib.parse import quote_plus

import folium
import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sklearn.cluster import DBSCAN
from streamlit_folium import st_folium
import streamlit.components.v1 as components

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
# 适合场景的合法白名单（GPT 偶尔会漂移塞进 brunch/casual/late_night 等其他字段值，过滤掉）
CANONICAL_SCENARIOS = list(SCENARIO_ZH.keys())

# LA 餐厅常见菜系中文映射 · 全覆盖版（没匹配的兜底只显示英文）
CUISINE_ZH = {
    # 韩国
    "Korean BBQ": "韩国烤肉", "Korean": "韩国菜", "Korean Soup": "韩式汤", "Korean Stew": "韩式炖煮",
    # 意大利
    "California Italian": "加州意餐", "Italian": "意大利菜", "Roman Italian": "罗马意餐",
    "Italian-American": "意美融合", "Italian American": "意美融合", "Pizzeria": "比萨店",
    # 法国
    "California-French": "加州法餐", "California French": "加州法餐",
    "French": "法餐", "French Bakery": "法式烘焙", "Patisserie": "法式甜品",
    # 日本
    "Sushi": "寿司", "Hand Roll Sushi": "手卷寿司", "Japanese": "日料",
    "Japanese Kaiseki": "日式怀石", "Modern Japanese": "现代日料", "Ramen": "拉面",
    "Izakaya": "居酒屋",
    # 墨西哥
    "Oaxacan": "瓦哈卡墨菜", "Mexican": "墨西哥菜", "Tacos": "塔可",
    "Tex-Mex": "德州墨菜", "Taqueria": "塔可铺",
    # 泰国 / 东南亚
    "Modern Thai": "现代泰餐", "Thai": "泰国菜", "Southeast Asian": "东南亚菜",
    "Asian Fusion": "亚洲融合", "Indonesian": "印尼菜", "Vietnamese": "越南菜",
    "Asian": "亚洲菜",
    # 中东 / 地中海
    "Israeli": "以色列菜", "Middle Eastern": "中东菜", "Mediterranean": "地中海菜",
    "Lebanese": "黎巴嫩菜", "Israeli Middle Eastern": "以色列中东菜",
    # 美式
    "American": "美式", "New American": "新美式", "Diner": "美式餐厅",
    "Comfort Food": "家常美食", "California Cuisine": "加州菜",
    "California Modern": "加州现代菜", "Modern American": "现代美式",
    # 美式 fast / casual
    "Burger": "汉堡", "Burgers": "汉堡", "Gastropub": "美食酒馆",
    "Hot Dogs": "热狗", "Hot Chicken": "辣鸡", "Nashville Hot Chicken": "纳什维尔辣鸡",
    "Sandwiches": "三明治", "Sandwich": "三明治", "Fried Chicken": "炸鸡",
    "BBQ": "烧烤", "Steakhouse": "牛排馆",
    # 欧洲
    "Spanish": "西班牙菜", "Spanish Tapas": "西班牙小吃", "Tapas": "小吃",
    "Greek": "希腊菜",
    # 烘焙 / 咖啡 / 早午
    "Bakery": "烘焙", "Cafe": "咖啡馆", "Café": "咖啡馆", "Coffee Shop": "咖啡馆",
    "Brunch": "早午餐", "Breakfast": "早餐", "Breakfast & Brunch": "早午餐",
    # 杂项
    "Jewish Deli": "犹太熟食", "Deli": "熟食店",
    "Pizza": "比萨", "Food Hall": "美食广场", "Market": "美食市场",
    "Eclectic": "混搭料理", "International": "国际料理",
    "Vegetarian": "素食", "Vegan": "纯素",
    "Seafood": "海鲜",
    # 12 个之前漏掉的（数据实际出现）
    "American Diner": "美式餐厅",
    "American Gastropub": "美式美食酒馆",
    "Breakfast Sandwiches": "早餐三明治",
    "California": "加州菜",
    "California Bakery": "加州烘焙",
    "California Contemporary": "加州现代菜",
    "California Mediterranean": "加州地中海菜",
    "French Italian": "法意融合",
    "Kaiseki": "怀石料理",
    "Southern Thai": "泰国南部菜",
    "Sushi Handrolls": "手卷寿司",
    "Thai Street Food": "泰式街头小吃",
    # 阶段 13：菜系枚举规范化后新增的标准选项
    "Chinese": "中餐",
    "Indian": "印度菜",
    "Latin American": "拉美菜",
    "Southern American": "美式南方菜",
    "Cajun": "卡真菜",
    "Fusion": "融合菜",
    "Other": "其他",
}


def fmt_en(value: str) -> str:
    """把 'business_lunch' / 'fine_dining' 等下划线英文转为 'Business Lunch' / 'Fine Dining'。
    对已经正确大小写的菜系名（如 'Korean BBQ'）原样返回。"""
    if not isinstance(value, str):
        return str(value)
    if "_" in value:
        return value.replace("_", " ").title()
    if value.islower():
        return value.title()
    return value


def fmt_locale(value: str, zh_map: dict) -> str:
    """根据当前语言返回中文或英文标签。中文模式优先查 zh_map，没匹配兜底英文。"""
    if "lang" in st.session_state and st.session_state["lang"] == "zh":
        zh = zh_map.get(value, "").strip()
        if zh:
            return zh
    return fmt_en(value)


# ═══════════════════════════════════════════════════════════════
# 双语切换（i18n）
# ═══════════════════════════════════════════════════════════════
T = {
    # Header
    "subtitle": ("AI 驱动的洛杉矶美食决策助手 · Vibe Coding Demo · 2026",
                 "AI-Powered LA Restaurant Itinerary Assistant · Vibe Coding Demo · 2026"),
    "reset_btn": ("🔄 重置筛选", "🔄 Reset Filters"),
    "reset_help": ("清空所有筛选 + AI 推荐 + 行程", "Clear all filters, AI recs, and itinerary"),
    # Sidebar AI section
    "ai_header": ("🤖 AI 决策助手", "🤖 AI Concierge"),
    "ai_query_label": ("用一句话描述你想要的店", "Describe what you're looking for"),
    "ai_placeholder": ("例：今晚约会，预算 $80，安静一点不排队",
                       "e.g., date night, budget $80, quiet, no wait"),
    "ai_run_btn": ("✨ 让 AI 帮我选", "✨ Let AI Pick"),
    "ai_clear_btn": ("🗑️ 清除 AI 推荐", "🗑️ Clear AI Picks"),
    # Sidebar filters
    "filter_header": ("🎛️ 多维筛选", "🎛️ Filters"),
    "price_label": ("💰 人均预算 (USD)", "💰 Budget per Person (USD)"),
    "vibe_label": ("✨ 氛围", "✨ Vibe"),
    "scenario_label": ("🎯 适合场景", "🎯 Best For"),
    "cuisine_label": ("🍴 主菜系", "🍴 Cuisine"),
    "ms_placeholder": ("请选择...", "Choose options"),
    "import_header": ("🔗 用你自己的数据", "🔗 Use Your Own Data"),
    "import_intro": (
        "想用你自己的 Google Maps 收藏吗？跟着 4 步把它接进来：",
        "Want to plug in your own Google Maps favorites? Follow these 4 steps:",
    ),
    "import_step1": (
        "**1.** 去 Google Takeout 申请导出，**只勾**「Maps (your places)」",
        "**1.** Go to Google Takeout, **only check** \"Maps (your places)\"",
    ),
    "import_step2": (
        "**2.** 等几分钟收到下载邮件，解压找到收藏列表 CSV",
        "**2.** Wait for the email, unzip and find your saved-list CSV",
    ),
    "import_step3": (
        "**3.** Fork [本项目仓库](https://github.com/ihcoaixnaug/la-vibe-itinerary)，把你的 CSV 替换 `data/my_places.csv`",
        "**3.** Fork [the repo](https://github.com/ihcoaixnaug/la-vibe-itinerary), replace `data/my_places.csv` with yours",
    ),
    "import_step4": (
        "**4.** 终端跑 `python scripts/02_process_data.py` → GPT-4o 自动给你的店打 20 维标签（约 30 秒、$0.20）",
        "**4.** Run `python scripts/02_process_data.py` → GPT-4o auto-tags your places (~30s, ~$0.20)",
    ),
    "import_btn": ("🚀 打开 Google Takeout", "🚀 Open Google Takeout"),
    "import_note": (
        "💡 因 Google 隐私政策，网站不能直接读你的收藏，必须先导出。",
        "💡 Due to Google's privacy policy, this site can't read your favorites directly — you must export first.",
    ),
    "gem_label": ("💎 小众度", "💎 Hidden Gem Level"),
    "gem_help": ("1=人尽皆知，10=本地宝藏", "1=well-known, 10=local secret"),
    "sort_label": ("🔀 排序方式", "🔀 Sort By"),
    "sort_default": ("推荐（默认）", "Recommended (default)"),
    "sort_cheapest": ("人均最便宜", "Cheapest"),
    "sort_priciest": ("人均最贵", "Most Expensive"),
    "sort_value": ("性价比最高", "Best Value"),
    "sort_wait": ("等位最短", "Shortest Wait"),
    "sort_gem": ("最小众", "Most Hidden"),
    "sort_insta": ("最出片", "Most Instagrammable"),
    "gen_btn": ("🚀 一键生成行程", "🚀 Generate Itinerary"),
    "db_caption": ("📊 数据库：{n} 家店  ·  {dp}+ 个 AI 标签",
                   "📊 Database: {n} places  ·  {dp}+ AI tags"),
    "tech_caption": ("🤖 GPT-4o 自动归纳人均/招牌菜/氛围/场景\n\n🌐 DBSCAN 聚类 + TSP 最优路径",
                     "🤖 GPT-4o auto-tags price / dishes / vibe / scenes\n\n🌐 DBSCAN clustering + TSP optimization"),
    # Metrics
    "metric_hits": ("命中店铺", "Matches"),
    "metric_tags": ("AI 标签数", "AI Tags"),
    "metric_areas": ("覆盖商圈", "Areas"),
    "metric_areas_v": ("5 个核心 + 8 散点", "5 hubs + 8 outliers"),
    "metric_ai_recs": ("AI 推荐", "AI Picks"),
    "metric_ai_off": ("未启用", "Off"),
    "metric_ai_n": ("{n} 家", "{n} places"),
    "metric_min_price": ("最低人均", "Min/Person"),
    # AI rec block
    "ai_thinking": ("🤔 GPT-4o 正在分析需求：{q}", "🤔 GPT-4o analyzing: {q}"),
    "ai_failed": ("❌ AI 推荐失败：{e}", "❌ AI rec failed: {e}"),
    "ai_you_said": ("💬 你说：\"{q}\"", "💬 You asked: \"{q}\""),
    "ai_recs_title": ("🎯 AI 推荐：{names}", "🎯 AI Picks: {names}"),
    "ai_reasoning": ("💭 {r}", "💭 {r}"),
    "ai_no_match": ("🤖 AI 没找到匹配项：{r}", "🤖 No match: {r}"),
    # Map / itinerary
    "map_title": ("🗺️ 美食地图", "🗺️ Restaurant Map"),
    "map_with_routes": ("  ·  📍 已生成打卡路线", "  ·  📍 Itinerary generated"),
    "list_title_filter": ("📋 命中店铺（{n} 家）", "📋 {n} Matches"),
    "list_title_ai": ("📋 命中 {a} 家 · 🎯 AI 推荐 {c} 家",
                     "📋 {a} Matches · 🎯 {c} AI Picks"),
    "sort_hint": ("🔀 已按 **{by}** 排序", "🔀 Sorted by **{by}**"),
    "no_match": ("⚠️ 当前筛选条件下没命中。试着放宽预算/取消氛围。",
                 "⚠️ No matches. Try relaxing budget or vibe."),
    "must_try": ("🍴 **必点**", "🍴 **Must Try**"),
    "warning_too_few": ("⚠️ 当前筛选结果不足 2 家，无法生成行程，请放宽条件。",
                       "⚠️ Need at least 2 matches to build itinerary. Relax filters."),
    "itinerary_title": ("📍 推荐打卡顺序", "📍 Suggested Visit Order"),
    "route_label": ("路线 {i} · {n} 家店 · {km:.1f} km · 步行 {w} 分 / 开车 {d} 分",
                    "Route {i} · {n} places · {km:.1f} km · {w} min walk / {d} min drive"),
    "min_per_person": ("人均", "Avg/Person"),
    # Dish dialog
    "dish_dialog_title": ("🍴 菜品预览", "🍴 Dish Preview"),
    "dish_dialog_at": ("@ **{r}**", "@ **{r}**"),
    "dish_dialog_caption": (
        "ℹ️ 这是一张通用美食示意图（来自 Flickr 摄影师），不一定是这家店的实际菜品。想看真实图片用下面按钮 ↓",
        "ℹ️ This is a generic food image (from Flickr). For the actual dish at this restaurant, use buttons below ↓",
    ),
    "dish_btn_maps": ("📍 去 Google Maps 看餐厅", "📍 View Restaurant on Google Maps"),
    "dish_btn_search": ("🖼️ Google 搜真实图", "🖼️ Real Photos via Google"),
    "dish_load_failed": ("图片加载失败，请用下方按钮看真实图",
                         "Image failed to load — use buttons below for real photos"),
    # Tag dialog
    "tag_dialog_title": ("🏷️ 同标签店铺", "🏷️ Same-Tag Restaurants"),
    "tag_dialog_count": ("含标签 `{tag}` 的店（{n} 家）",
                        "Places with tag `{tag}` ({n})"),
    "tag_dialog_empty": ("没有匹配店铺", "No matches"),
    # AI not configured
    "ai_no_key": ("未配置 OPENROUTER_API_KEY（本地需 .env，部署需 Streamlit Secrets）",
                  "OPENROUTER_API_KEY not set (need .env locally or Streamlit Secrets)"),
    # Data not found
    "data_not_found": ("❌ 没找到 data/enriched_places.csv，请先跑 `python scripts/02_process_data.py`",
                       "❌ data/enriched_places.csv not found. Run `python scripts/02_process_data.py` first."),
    # Tooltip
    "back_to_top": ("回到顶部", "Back to top"),
    "open_gmaps": ("在 Google Maps 中打开", "Open in Google Maps"),
}


def init_lang():
    if "lang" not in st.session_state:
        st.session_state["lang"] = "zh"
    return st.session_state["lang"]


def t(key: str, **kwargs) -> str:
    """根据当前语言返回翻译；支持 {var} 占位符。"""
    pair = T.get(key, (key, key))
    text = pair[0] if init_lang() == "zh" else pair[1]
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text


def field(row, name: str) -> str:
    """根据当前语言返回 _zh 或 _en 字段值。"""
    lang = init_lang()
    val = row.get(f"{name}_{lang}")
    if not val or (isinstance(val, float) and pd.isna(val)):
        # 兜底：拿另一种语言
        other = "en" if lang == "zh" else "zh"
        val = row.get(f"{name}_{other}", "")
    return str(val) if val else ""

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
def parse_intent(query: str) -> dict:
    """从自然语言中解析筛选意图：预算、氛围、场景。返回 dict，未命中的 key 不存在。"""
    result = {}
    q = query.lower()

    # 预算
    budget = parse_budget(query)
    if budget:
        result["budget"] = budget

    # 氛围
    # 只映射数据库实际存在的 5 种 vibe：casual / dive / fine_dining / lively / trendy
    vibe_map = {
        "fine_dining": ["约会", "浪漫", "romantic", "情侣", "高档", "精致", "正式", "fine dining", "upscale"],
        "casual":      ["安静", "quiet", "温馨", "不吵", "低调", "随意", "casual", "轻松", "休闲"],
        "lively":      ["热闹", "lively", "活跃", "气氛好", "嗨"],
        "trendy":      ["网红", "trendy", "时髦", "打卡", "ins"],
        "dive":        ["平价", "接地气", "苍蝇馆", "便宜", "实惠"],
    }
    matched_vibes = [v for v, kws in vibe_map.items() if any(kw in q for kw in kws)]
    if matched_vibes:
        result["vibes"] = matched_vibes

    # 场景
    scenario_map = {
        "date":           ["约会", "date", "情侣", "浪漫", "两人"],
        "friends_group":  ["朋友", "friend", "聚会", "一起吃", "一群"],
        "family":         ["家庭", "家人", "family", "父母", "孩子", "全家"],
        "business_lunch": ["商务", "business", "客户", "商谈", "工作餐"],
        "solo":           ["一人", "solo", "一个人", "独自", "自己吃"],
        "celebration":    ["庆祝", "生日", "周年", "纪念", "celebration"],
        "tourist_must":   ["必去", "游客", "第一次", "tourist", "打卡"],
    }
    matched_scenarios = [s for s, kws in scenario_map.items() if any(kw in q for kw in kws)]
    if matched_scenarios:
        result["scenarios"] = matched_scenarios

    return result


def parse_budget(query: str) -> float | None:
    """从自然语言中提取预算上限，支持中英文格式如 '预算80' '$80' 'budget 80' '80美元/刀'。"""
    patterns = [
        r'预算\s*[¥$]?\s*(\d+)',
        r'[¥$]\s*(\d+)',
        r'budget\s+[¥$]?\s*(\d+)',
        r'(\d+)\s*(?:刀|美元|usd|dollar)',
        r'under\s+[¥$]?\s*(\d+)',
        r'(\d+)\s*per\s*person',
    ]
    for p in patterns:
        m = re.search(p, query, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


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


@st.cache_data(show_spinner=False, ttl=600)
def ai_recommend(query: str, places_summary: str, lang: str = "zh") -> dict:
    """调 GPT-4o 做语义推荐。lang='zh' 返中文 reasoning，'en' 返英文。"""
    if not OPENROUTER_API_KEY:
        return {"error": "OPENROUTER_API_KEY not configured"}
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )
        lang_instruction = (
            "用中文 2-3 句话精炼解释推荐理由（每家店必须写出数据库里的实际人均价格，如'人均 $60'；"
            "若低于用户预算写'人均 $X，在预算内'；若高于则写'人均 $X，略超预算'；不得凭空估价）"
            if lang == "zh"
            else "Explain in 2-3 concise English sentences. For each place, state the EXACT price from the database "
            "(e.g. '$60/person'); if under budget say 'within budget'; if over say 'slightly over budget at $X'. Never guess the price."
        )
        example = (
            '{"recommended_names": ["Republique", "Cassia", "Sushi Gen"],'
            ' "reasoning": "Republique 浪漫氛围+可订位，人均略高 $90 但氛围完美匹配；'
            'Cassia 在 Santa Monica 海边，中价 $60 安静雅致；Sushi Gen 是 DTLA 经典寿司，环境安静、订位即可。"}'
            if lang == "zh"
            else '{"recommended_names": ["Republique", "Cassia", "Sushi Gen"],'
            ' "reasoning": "Republique offers romantic ambiance with easy reservations — slightly over budget at $90 but worth it. '
            'Cassia in Santa Monica delivers quiet seaside vibes at $60. Sushi Gen is a DTLA sushi classic, peaceful and reservable around $70."}'
        )
        system = f"""You are a helpful AI restaurant concierge for Los Angeles dining. Your goal is to ALWAYS give the user 2-4 actionable recommendations from the database — never refuse, never return empty results.

DATABASE (each line: name | cuisine | price | attributes | dishes | pitch):
{places_summary}

Return ONLY a JSON object:
{{
  "recommended_names": ["exact name 1", "exact name 2", ...],
  "reasoning": "{lang_instruction}"
}}

CRITICAL RULES:
1. ALWAYS return 2-4 recommendations — find the BEST AVAILABLE, not perfect matches.
2. Soft matching > strict matching (e.g. 10-30% over budget OK with note).
3. Names MUST exactly match the database (case-sensitive).
4. reasoning field: {"Chinese (Simplified)" if lang == "zh" else "natural English"}, 2-3 sentences max.
5. PRICE ACCURACY: Always quote the EXACT price from the database for each place. Never estimate or hallucinate prices.

EXAMPLE for query "今晚约会，预算 $80，安静一点不排队":
{example}
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
    st.error(t("data_not_found"))
    st.stop()

df = load_data()
PRICE_MIN_GLOBAL = int(df.price_per_person_usd.min())
PRICE_MAX_GLOBAL = int(df.price_per_person_usd.max())
total_data_points = len(df) * 20 + sum(
    sum(len(df.at[i, c]) for c in ["cuisine_tags", "must_try_dishes", "best_for", "best_time_slots", "dietary_friendly"] if isinstance(df.at[i, c], list))
    for i in range(len(df))
)


# ═══════════════════════════════════════════════════════════════
# Header（含锚点用于"回到顶部" + 语言切换）
# ═══════════════════════════════════════════════════════════════
st.markdown('<div id="top"></div>', unsafe_allow_html=True)
header_col1, header_col_lang, header_col_reset = st.columns([6, 1, 1.2])
with header_col1:
    st.title("🌴 LA Vibe Itinerary")
    st.caption(t("subtitle"))
with header_col_lang:
    st.write("")
    lang_choice = st.selectbox(
        "🌐", options=["zh", "en"],
        index=0 if init_lang() == "zh" else 1,
        format_func=lambda x: "中文" if x == "zh" else "English",
        label_visibility="collapsed",
        key="lang_selector",
    )
    if lang_choice != st.session_state.get("lang"):
        st.session_state["lang"] = lang_choice
        st.rerun()
with header_col_reset:
    st.write("")
    if st.button(t("reset_btn"), use_container_width=True, help=t("reset_help")):
        # 用 JS 真正刷新浏览器页面，彻底清除所有 session state
        st.session_state["_do_reload"] = True
        st.rerun()


# ═══════════════════════════════════════════════════════════════
# Sidebar：AI 自然语言查询 + 筛选
# ═══════════════════════════════════════════════════════════════
with st.sidebar:
    # ---- AI 自然语言查询 ----
    st.header(t("ai_header"))
    nl_query = st.text_area(
        t("ai_query_label"),
        value=st.session_state.get("last_nl_query", ""),
        placeholder=t("ai_placeholder"),
        height=80,
        key="nl_query_input",
    )
    nl_btn = st.button(t("ai_run_btn"), use_container_width=True, type="primary")

    if nl_btn and nl_query.strip():
        st.session_state["last_nl_query"] = nl_query.strip()
        # 解析意图 → 同步到筛选器（让筛选条件可视化反映 AI 的理解）
        _intent = parse_intent(nl_query.strip())
        # 只保留数据库里实际存在的 vibe / scenario 值，避免 pills 出现"1✓但无选项"的幽灵状态
        _available_vibes = set(df.vibe.unique())
        _available_scenarios = {s for lst in df.best_for for s in lst}
        if _intent.get("vibes"):
            _intent["vibes"] = [v for v in _intent["vibes"] if v in _available_vibes]
            if not _intent["vibes"]:
                del _intent["vibes"]
        if _intent.get("scenarios"):
            _intent["scenarios"] = [s for s in _intent["scenarios"] if s in _available_scenarios]
            if not _intent["scenarios"]:
                del _intent["scenarios"]
        st.session_state["_last_parsed_intent"] = _intent
        # 只同步预算（硬约束）；氛围/场景由 AI 语义匹配处理，不转成 pills 硬过滤
        if _intent.get("budget"):
            st.session_state["price_range_slider"] = (
                PRICE_MIN_GLOBAL, int(_intent["budget"])
            )

    if st.session_state.get("last_nl_query") and st.button(t("ai_clear_btn"), use_container_width=True):
        st.session_state.pop("last_nl_query", None)
        st.session_state.pop("_last_parsed_intent", None)
        st.rerun()

    # 已同步的筛选条件提示
    _intent_display = st.session_state.get("_last_parsed_intent", {})
    if _intent_display.get("budget"):
        st.caption(f"🤖 已同步预算上限：≤ ${int(_intent_display['budget'])}")

    st.divider()
    st.header(t("filter_header"))

    price_min = PRICE_MIN_GLOBAL
    price_max = PRICE_MAX_GLOBAL
    price_range = st.slider(
        t("price_label"),
        min_value=price_min,
        max_value=price_max,
        value=(price_min, price_max),
        step=5,
        key="price_range_slider",
    )

    # 每个筛选放进可折叠面板，默认收起，标题显示"已选 N"
    def _expander_title(label: str, selected: list) -> str:
        return f"{label}" + (f"  ·  {len(selected)} ✓" if selected else "")

    # 氛围
    all_vibes = sorted(df.vibe.unique())
    prev_v = st.session_state.get("pills_vibes", [])
    with st.expander(_expander_title(t("vibe_label"), prev_v), expanded=False):
        selected_vibes = st.pills(
            "vibes_internal_label",
            options=all_vibes,
            selection_mode="multi",
            format_func=lambda x: fmt_locale(x, VIBE_ZH),
            key="pills_vibes",
            label_visibility="collapsed",
        ) or []

    # 适合场景
    raw_scenarios = {s for lst in df.best_for for s in lst}
    all_scenarios = [s for s in CANONICAL_SCENARIOS if s in raw_scenarios]
    prev_s = st.session_state.get("pills_scenarios", [])
    with st.expander(_expander_title(t("scenario_label"), prev_s), expanded=False):
        selected_scenarios = st.pills(
            "scenarios_internal_label",
            options=all_scenarios,
            selection_mode="multi",
            format_func=lambda x: fmt_locale(x, SCENARIO_ZH),
            key="pills_scenarios",
            label_visibility="collapsed",
        ) or []

    # 主菜系
    all_cuisines = sorted(df.cuisine_primary.unique())
    prev_c = st.session_state.get("pills_cuisines", [])
    with st.expander(_expander_title(t("cuisine_label"), prev_c), expanded=False):
        selected_cuisines = st.pills(
            "cuisines_internal_label",
            options=all_cuisines,
            selection_mode="multi",
            format_func=lambda x: fmt_locale(x, CUISINE_ZH),
            key="pills_cuisines",
            label_visibility="collapsed",
        ) or []

    min_gem = st.slider(
        t("gem_label"),
        min_value=1, max_value=10, value=1,
        help=t("gem_help"),
    )

    SORT_OPTIONS = ["sort_default", "sort_cheapest", "sort_priciest",
                    "sort_value", "sort_wait", "sort_gem", "sort_insta"]
    sort_by = st.selectbox(
        t("sort_label"),
        options=SORT_OPTIONS,
        format_func=lambda k: t(k),
    )

    st.divider()
    generate_btn = st.button(t("gen_btn"), use_container_width=True, type="primary")

    # ---- 导入个人数据指南 ----
    st.divider()
    with st.expander(t("import_header"), expanded=False):
        st.caption(t("import_intro"))
        st.markdown(t("import_step1"))
        st.markdown(t("import_step2"))
        st.markdown(t("import_step3"))
        st.markdown(t("import_step4"))
        st.link_button(
            t("import_btn"),
            "https://takeout.google.com/",
            use_container_width=True,
        )
        st.caption(t("import_note"))

    st.divider()
    st.caption(
        t("db_caption", n=len(df), dp=total_data_points) + "\n\n" + t("tech_caption")
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

# 应用排序（key 是 T 的 key，映射到字段+方向）
SORT_MAP = {
    "sort_default": (None, None),
    "sort_cheapest": ("price_per_person_usd", True),
    "sort_priciest": ("price_per_person_usd", False),
    "sort_value": ("value_score", False),
    "sort_wait": ("avg_wait_minutes", True),
    "sort_gem": ("hidden_gem_score", False),
    "sort_insta": ("instagrammable_score", False),
}
sort_col, sort_asc = SORT_MAP.get(sort_by, (None, None))
if sort_col and sort_col in filtered.columns and len(filtered) > 0:
    filtered = filtered.sort_values(by=sort_col, ascending=sort_asc).reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════
# AI Agent：自然语言推荐（在所有店里选，无视筛选）
# ═══════════════════════════════════════════════════════════════
ai_rec: dict = {}
ai_rec_names: set = set()
if st.session_state.get("last_nl_query"):
    with st.spinner(t("ai_thinking", q=st.session_state['last_nl_query'])):
        # AI 在 filtered 范围内挑；若 NL query 含预算数字则额外硬过滤，确保不超预算
        if len(filtered) > 0:
            _query_str = st.session_state["last_nl_query"]
            _budget_cap = parse_budget(_query_str)
            _ai_pool = filtered.copy()
            if _budget_cap is not None:
                _budget_filtered = _ai_pool[_ai_pool["price_per_person_usd"] <= _budget_cap]
                if len(_budget_filtered) >= 2:
                    _ai_pool = _budget_filtered
                # 若预算内不足 2 家，退回全 filtered（AI 会在 reasoning 中说明）
            ai_rec = ai_recommend(
                _query_str,
                build_places_summary(_ai_pool),
                lang=init_lang(),
            )
        else:
            ai_rec = {"error": "筛选结果为空，请放宽条件后再用 AI 推荐"
                      if init_lang() == "zh" else "No matches under current filters"}
    if ai_rec.get("error"):
        st.error(t("ai_failed", e=ai_rec['error']))
    else:
        ai_rec_names = set(ai_rec.get("recommended_names", []))
        if ai_rec_names:
            st.markdown(
                f"""<div style='background:linear-gradient(135deg,#fff8e1,#fff3c4);
                padding:16px 20px;border-radius:12px;border-left:5px solid #f39c12;
                margin-bottom:16px'>
                <div style='font-size:13px;color:#7d6608;margin-bottom:8px'>
                {t("ai_you_said", q=st.session_state['last_nl_query'])}
                </div>
                <div style='font-size:18px;font-weight:600;color:#1a1a1a;margin-bottom:6px'>
                {t("ai_recs_title", names=' · '.join(ai_rec_names))}
                </div>
                <div style='color:#444;line-height:1.6'>
                {t("ai_reasoning", r=ai_rec.get('reasoning', '—'))}
                </div>
                </div>""",
                unsafe_allow_html=True,
            )
        else:
            st.warning(t("ai_no_match", r=ai_rec.get('reasoning', '—')))


# ═══════════════════════════════════════════════════════════════
# 顶部指标
# ═══════════════════════════════════════════════════════════════
# 顶部 4 指标已删（用户决策无关 + 侧栏/列表已有等价信息）


# ═══════════════════════════════════════════════════════════════
# 行程生成（结果存 session_state，跨 rerun 保留——避免点 marker 后路径消失）
# ═══════════════════════════════════════════════════════════════
if generate_btn:
    if len(filtered) >= 2:
        coords = filtered[["lat", "lng"]].values
        labels = dbscan_cluster(coords, eps_km=2.5, min_samples=2)
        filtered_with_cid = filtered.assign(_cid=labels)
        new_clusters = []
        for cid in sorted(set(labels) - {-1}):
            sub = filtered_with_cid[filtered_with_cid._cid == cid].reset_index(drop=True)
            places_list = sub.to_dict("records")
            order, total_km = optimize_route(places_list)
            ordered = [places_list[i] for i in order]
            new_clusters.append({
                "id": int(cid),
                "places": ordered,
                "total_km": total_km,
            })
        st.session_state["itinerary_clusters"] = new_clusters
    else:
        st.warning(t("warning_too_few"))
        st.session_state["itinerary_clusters"] = []

# 始终从 session_state 读，确保点 marker / 改语言等 rerun 后路径仍在
itinerary_clusters: list[dict] = st.session_state.get("itinerary_clusters", [])


# ═══════════════════════════════════════════════════════════════
# 地图
# ═══════════════════════════════════════════════════════════════
st.subheader(t("map_title") + (t("map_with_routes") if itinerary_clusters else ""))

# 地图自动居中：如果用户点击了某家店的 📍，地图重新中心 + 拉近到那家店
_map_center = LA_CENTER
_map_zoom = 11
_highlighted_now = st.session_state.get("highlighted_name")
if _highlighted_now:
    _matching = df[df["name"] == _highlighted_now]
    if len(_matching):
        _row = _matching.iloc[0]
        _map_center = [float(_row["lat"]), float(_row["lng"])]
        _map_zoom = 14
m = folium.Map(location=_map_center, zoom_start=_map_zoom, tiles="cartodbpositron")

# 标记哪些店在行程内（带顺序号）
in_itinerary: dict[str, dict] = {}
for c in itinerary_clusters:
    color = CLUSTER_COLORS[c["id"] % len(CLUSTER_COLORS)]
    for i, p in enumerate(c["places"]):
        in_itinerary[p["name"]] = {"order": i, "color": color, "cluster": c["id"]}

    # 画簇内连线
    coords_path = [[p["lat"], p["lng"]] for p in c["places"]]
    folium.PolyLine(coords_path, color=color, weight=4, opacity=0.75, dash_array="5").add_to(m)


def gmap_link(row) -> str:
    """生成 Google Maps 官方搜索 URL（用 ?api=1&query= 协议，
    用店名+地址确保 100% 跳到正确餐厅；完全忽略数据里的占位 maps_url）。"""
    q = quote_plus(f"{row['name']} {row.get('address', '')}".strip())
    return f"https://www.google.com/maps/search/?api=1&query={q}"


def gmap_route_url(places_in_order: list[dict]) -> str | None:
    """生成 Google Maps 路线规划 URL（含起点/终点/途经点），用户可在 GMaps 选交通方式。"""
    if len(places_in_order) < 2:
        return None
    addrs = [
        f"{p['name']} {p.get('address', '')}".strip()
        for p in places_in_order
    ]
    origin = quote_plus(addrs[0])
    destination = quote_plus(addrs[-1])
    base = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={destination}"
    if len(addrs) > 2:
        waypoints = "|".join(quote_plus(a) for a in addrs[1:-1])
        base += f"&waypoints={waypoints}"
    return base


def make_popup(row, order=None):
    """简化版地图弹窗：只显示店名（链接）+ 一句话点评 + 价位。多余信息留给右侧卡片。"""
    badge = ""
    if order is not None:
        badge = f"<span style='background:#1a1a1a;color:white;padding:2px 8px;border-radius:50%;font-weight:bold;margin-right:6px'>{order + 1}</span>"
    name_link = (
        f"<a href='{gmap_link(row)}' target='_blank' "
        f"style='color:#1a73e8;text-decoration:none;font-weight:bold' "
        f"title='{t('open_gmaps')}'>{row['name']} ↗</a>"
    )
    return (
        f"<div style='font-family:system-ui,sans-serif;min-width:200px;max-width:240px'>"
        f"<div style='font-size:15px;margin-bottom:6px'>{badge}{name_link}</div>"
        f"<div style='color:#666;font-size:11px;margin-bottom:6px'>~${row['price_per_person_usd']}/人</div>"
        f"<div style='font-style:italic;color:#222;font-size:12px;line-height:1.4'>{field(row, 'one_liner')}</div>"
        f"</div>"
    )


# 显示集合：筛选命中的 + AI 推荐的（即使被筛选过滤也强制显示）
display_names = set(filtered["name"].tolist()) | ai_rec_names
display_df = df[df["name"].isin(display_names)]

for _, row in display_df.iterrows():
    is_ai = row["name"] in ai_rec_names
    iti = in_itinerary.get(row["name"])
    is_highlighted = (row["name"] == _highlighted_now)

    # 被选中的店：先画一个蓝色大圆环作为高亮底层
    if is_highlighted:
        folium.CircleMarker(
            location=[row["lat"], row["lng"]],
            radius=22,
            color="#1a73e8",
            fill=True,
            fill_color="#1a73e8",
            fill_opacity=0.22,
            weight=3,
            opacity=0.9,
        ).add_to(m)

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

# ─── 菜名弹窗：点击菜名直接显示图 ───
@st.dialog("🍴 Dish Preview", width="medium")
def show_dish_dialog(dish: str, restaurant: str, gmap_url: str):
    st.markdown(f"### {dish}")
    st.caption(t("dish_dialog_at", r=restaurant))

    first_kw = quote_plus(dish.split()[0].lower())
    img_url = f"https://loremflickr.com/640/420/{first_kw},food,plate"
    try:
        st.image(img_url, use_container_width=True)
    except Exception:
        st.warning(t("dish_load_failed"))
    st.caption(t("dish_dialog_caption"))

    cols = st.columns(2)
    cols[0].link_button(t("dish_btn_maps"), gmap_url, use_container_width=True)
    image_search_url = (
        f"https://www.google.com/search?q={quote_plus(dish + ' ' + restaurant)}&tbm=isch"
    )
    cols[1].link_button(t("dish_btn_search"), image_search_url, use_container_width=True)


# Tag 弹窗已删除（标签区块已移除）


# ─── 主区两栏：地图(左) + 可滚动结果列表(右) ───
LIST_HEIGHT = 580
map_col, list_col = st.columns([3, 2], gap="medium")  # 地图变窄，列表变宽

with map_col:
    map_state = st_folium(
        m,
        height=LIST_HEIGHT,
        use_container_width=True,
        returned_objects=["last_object_clicked_tooltip"],
        # key 带上卡片点击计数器：卡片每次点击都会强制地图重渲染到新中心
        key=f"main_map_{st.session_state.get('_card_click_count', 0)}",
    )
    # 解析 tooltip 拿到店名（兼容 "Name", "1. Name", "⭐ AI 推荐：Name"）
    # 若本次是卡片按钮触发的 rerun，跳过 tooltip 覆盖（避免旧 tooltip 覆盖卡片选中）
    if map_state and map_state.get("last_object_clicked_tooltip"):
        if not st.session_state.pop("_card_just_clicked", False):
            tip = map_state["last_object_clicked_tooltip"]
            for n in df["name"]:
                if str(n) in tip:
                    st.session_state["highlighted_name"] = n
                    break

highlighted = st.session_state.get("highlighted_name")

with list_col:
    if ai_rec_names:
        _ai_in_filter = len(ai_rec_names & set(filtered["name"]))
        st.markdown(f"##### {t('list_title_ai', a=len(filtered), c=_ai_in_filter)}")
    else:
        st.markdown(f"##### {t('list_title_filter', n=len(filtered))}")

    # 排序提示
    if sort_by != "sort_default":
        st.caption(t("sort_hint", by=t(sort_by)))

    with st.container(height=LIST_HEIGHT - 50, border=False):
        # 列表内"回到顶部"锚点（需求 4）
        st.markdown('<div id="list-top-anchor"></div>', unsafe_allow_html=True)
        # 行程详情：如果生成了行程，置顶显示（这样地图+行程同屏可见，不用上下翻）
        if itinerary_clusters:
            st.markdown(f"### {t('itinerary_title')}")
            for c in itinerary_clusters:
                color = CLUSTER_COLORS[c["id"] % len(CLUSTER_COLORS)]
                walk_min = int(c["total_km"] * 12)
                drive_min = int(c["total_km"] * 3 + len(c["places"]) * 5)
                with st.container(border=True):
                    route_text = t(
                        "route_label",
                        i=c["id"] + 1, n=len(c["places"]),
                        km=c["total_km"], w=walk_min, d=drive_min,
                    )
                    st.markdown(
                        f"<div style='font-size:14px'><span style='color:{color};font-size:1.4em'>●</span> "
                        f"{route_text}</div>",
                        unsafe_allow_html=True,
                    )
                    for i, p in enumerate(c["places"]):
                        rcols = st.columns([0.4, 3])
                        rcols[0].markdown(
                            f"<div style='background:{color};color:white;width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:13px'>{i + 1}</div>",
                            unsafe_allow_html=True,
                        )
                        rcols[1].markdown(
                            f"**[{p['name']}]({gmap_link(p)})** · ${p['price_per_person_usd']}"
                        )
                    # GMaps 路线导出按钮（需求 2）
                    route_url = gmap_route_url(c["places"])
                    if route_url:
                        btn_label = (
                            "🗺️ 在 Google Maps 看路线"
                            if init_lang() == "zh"
                            else "🗺️ Open route in Google Maps"
                        )
                        st.link_button(btn_label, route_url, use_container_width=True)
            st.divider()

        # AI 推荐的店在 filtered 内排到最前面（不再 union 外部数据，确保不会超出筛选）
        list_df = filtered.copy()
        if ai_rec_names:
            ai_in_filter = list_df[list_df["name"].isin(ai_rec_names)]
            others = list_df[~list_df["name"].isin(ai_rec_names)]
            list_df = pd.concat([ai_in_filter, others]).drop_duplicates(subset=["name"]).reset_index(drop=True)

        if len(list_df) == 0:
            st.info(t("no_match"))
        else:
            for idx, row in list_df.iterrows():
                is_ai = row["name"] in ai_rec_names
                is_picked = (row["name"] == highlighted)
                with st.container(border=True):
                    # 顶部 badges + 隐藏 anchor（用于自动滚动）
                    safe_id = str(row["name"]).replace(" ", "_").replace("/", "_").replace("'", "")
                    badges = [f'<a id="card-{safe_id}"></a>']
                    if is_ai:
                        badges.append(
                            '<span class="ai-pick-badge" '
                            'style="background:linear-gradient(90deg,#ffd54f,#ffa000);'
                            'color:white;padding:3px 10px;border-radius:10px;'
                            'font-size:11px;font-weight:bold;letter-spacing:0.3px">⭐ AI PICK</span>'
                        )
                    if is_picked:
                        badges.append(
                            '<span class="picked-marker" '
                            'style="background:#1a73e8;color:white;'
                            'padding:3px 10px;border-radius:10px;'
                            'font-size:11px;font-weight:bold;letter-spacing:0.3px">📍 ON MAP</span>'
                        )
                    if len(badges) > 1:  # 至少有一个 badge（不是只有 anchor）
                        st.markdown(
                            f'<div style="margin-bottom:6px">{" ".join(badges)}</div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        # 只渲染隐藏 anchor 用于滚动定位
                        st.markdown(badges[0], unsafe_allow_html=True)

                    # 店名行：点击店名 → 地图高亮；↗ 跳 Google Maps
                    name_cols = st.columns([5, 1])
                    if name_cols[0].button(
                        row["name"],
                        key=f"card_name_{idx}_{row['name']}",
                        use_container_width=True,
                        help="在地图上定位" if init_lang() == "zh" else "Highlight on map",
                    ):
                        st.session_state["highlighted_name"] = row["name"]
                        st.session_state["_card_just_clicked"] = True  # 防止 map tooltip 覆盖
                        # 递增计数器 → st_folium key 变化 → 强制地图重渲染到新中心
                        st.session_state["_card_click_count"] = (
                            st.session_state.get("_card_click_count", 0) + 1
                        )
                        st.rerun()
                    name_cols[1].link_button(
                        "↗",
                        gmap_link(row),
                        use_container_width=True,
                        help=t("open_gmaps"),
                    )
                    st.caption(
                        f"{row['cuisine_primary']} · "
                        f"💰 ${row['price_per_person_usd']} · ⏱ ~{row['avg_wait_minutes']}min"
                    )
                    st.markdown(f"💬 *{field(row, 'one_liner')}*")

                    # 必点菜：每个菜名是按钮，点击弹窗显示图片
                    if row["must_try_dishes"]:
                        dish_show = row["must_try_dishes"][:3]
                        st.markdown(t("must_try"))
                        dish_btn_cols = st.columns(min(3, len(dish_show)))
                        for di, d in enumerate(dish_show):
                            if dish_btn_cols[di].button(
                                d, key=f"dish_{idx}_{d}", use_container_width=True
                            ):
                                show_dish_dialog(d, row["name"], gmap_link(row))

                    # Tag 区块已按需求删除（信息和菜系/氛围重复）

                    cap_cols = st.columns(3)
                    cap_cols[0].caption(f"✨ {fmt_locale(row['vibe'], VIBE_ZH)}")
                    cap_cols[1].caption(f"📸 {row['instagrammable_score']}/10")
                    cap_cols[2].caption(f"💎 {row['hidden_gem_score']}/10")

        # 底部留一点空白，置顶按钮已改为右侧浮动（见下方 JS）
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════
# 重置筛选 → 真正刷新页面（清空所有 session state）
# ═══════════════════════════════════════════════════════════════
if st.session_state.get("_do_reload"):
    st.session_state.pop("_do_reload")
    components.html("<script>parent.window.location.reload();</script>", height=0)


# ═══════════════════════════════════════════════════════════════
# JS 注入：① 给 AI 卡 / 选中卡加彩色边框 ② 滚动到选中卡
# ═══════════════════════════════════════════════════════════════
_safe_highlighted = (
    str(highlighted).replace(" ", "_").replace("/", "_").replace("'", "")
    if highlighted else ""
)
components.html(
    f"""
    <script>
    (function() {{
        // 从徽章往上找：第一个"真的有可见边框"的祖先 = st.container(border=True) 的容器
        const findCardContainer = (badge) => {{
            let el = badge.parentElement;
            for (let i = 0; i < 25 && el && el !== parent.document.body; i++) {{
                try {{
                    const cs = parent.window.getComputedStyle(el);
                    const w = parseFloat(cs.borderTopWidth || '0');
                    const style = cs.borderTopStyle;
                    if (w >= 1 && style !== 'none' && style !== 'hidden') {{
                        return el;  // 这就是 st.container(border=True) 的实际容器
                    }}
                }} catch (e) {{}}
                el = el.parentElement;
            }}
            return null;
        }};

        // Multiselect 选完自动关闭下拉（用事件委托一次性挂载）
        const setupAutoCloseDropdown = () => {{
            const doc = parent.document;
            if (doc.body.dataset.msAutoClose) return;
            doc.body.dataset.msAutoClose = "1";
            doc.addEventListener('click', (e) => {{
                const opt = e.target.closest('[role="option"]');
                const sel = e.target.closest('[data-baseweb="select"]');
                if (opt && sel) {{
                    setTimeout(() => {{
                        // 触发 mousedown 在 body 上 → BaseWeb 检测到外部点击 → 关闭弹窗
                        const ev = new MouseEvent('mousedown', {{
                            bubbles: true, cancelable: true, view: parent.window
                        }});
                        doc.body.dispatchEvent(ev);
                        // 同时 blur 输入框双保险
                        const input = sel.querySelector('input');
                        if (input) input.blur();
                    }}, 80);
                }}
            }}, true);
        }};

        const apply = () => {{
            const doc = parent.document;
            setupAutoCloseDropdown();
            // AI 推荐 → 金色
            doc.querySelectorAll('.ai-pick-badge').forEach(badge => {{
                const card = findCardContainer(badge);
                if (card) {{
                    card.style.cssText += `
                        border: 2px solid #ffa000 !important;
                        box-shadow: 0 2px 12px rgba(255,165,0,0.28) !important;
                        background: linear-gradient(180deg, #fff8e1 0%, transparent 50%) !important;
                        border-radius: 10px !important;
                    `;
                }}
            }});
            // 地图选中 → 蓝色（覆盖金色，如果同时存在）
            doc.querySelectorAll('.picked-marker').forEach(badge => {{
                const card = findCardContainer(badge);
                if (card) {{
                    // 如果同时有 ai-pick-badge → 紫色（混合）
                    const hasAi = card.querySelector('.ai-pick-badge');
                    if (hasAi) {{
                        card.style.cssText += `
                            border: 2px solid #8e44ad !important;
                            box-shadow: 0 2px 14px rgba(142,68,173,0.35) !important;
                            background: linear-gradient(180deg, #f3e5f5 0%, transparent 50%) !important;
                        `;
                    }} else {{
                        card.style.cssText += `
                            border: 2px solid #1a73e8 !important;
                            box-shadow: 0 2px 12px rgba(26,115,232,0.30) !important;
                            background: linear-gradient(180deg, #e8f0fe 0%, transparent 50%) !important;
                            border-radius: 10px !important;
                        `;
                    }}
                }}
            }});
        }};

        const scroll = () => {{
            const id = "{_safe_highlighted}";
            if (!id) return;
            const target = parent.document.getElementById("card-" + id);
            if (target) {{
                target.scrollIntoView({{behavior: 'smooth', block: 'center'}});
            }}
        }};

        // ── 右侧浮动"置顶"按钮（从 list-top-anchor 向上找滚动容器）──
        const setupScrollTopBtn = () => {{
            const doc = parent.document;

            // 用锚点元素向上找第一个可滚动父容器，比 height/overflow 猜更可靠
            const anchor = doc.getElementById('list-top-anchor');
            if (!anchor) return;
            let target = anchor.parentElement;
            while (target && target !== doc.body) {{
                const s = parent.window.getComputedStyle(target);
                if (s.overflowY === 'auto' || s.overflowY === 'scroll') break;
                target = target.parentElement;
            }}
            if (!target || target === doc.body) return;

            const positionBtn = (btn) => {{
                const rect = target.getBoundingClientRect();
                // 贴容器右侧内沿，距顶部 16px
                btn.style.left  = (rect.right - 52) + 'px';
                btn.style.top   = (rect.top   + 16) + 'px';
            }};

            let btn = doc.getElementById('vibe-scroll-top-btn');
            if (btn) {{
                positionBtn(btn);   // 已存在时只更新位置
                return;
            }}

            btn = doc.createElement('div');
            btn.id = 'vibe-scroll-top-btn';
            btn.innerHTML = '⬆<br>{"置顶" if init_lang() == "zh" else "Top"}';
            btn.style.cssText = `
                position: fixed;
                z-index: 9999;
                background: rgba(26,115,232,0.85);
                color: white;
                padding: 6px 9px;
                border-radius: 8px;
                cursor: pointer;
                font-size: 11px;
                font-weight: bold;
                line-height: 1.5;
                text-align: center;
                min-width: 36px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.25);
                user-select: none;
                transition: background 0.15s, opacity 0.15s;
                opacity: 0.88;
            `;
            positionBtn(btn);
            btn.onmouseenter = () => {{ btn.style.opacity = '1'; btn.style.background = 'rgba(26,115,232,1)'; }};
            btn.onmouseleave = () => {{ btn.style.opacity = '0.88'; btn.style.background = 'rgba(26,115,232,0.85)'; }};
            btn.onclick = (e) => {{
                e.stopPropagation();
                // scrollIntoView 比直接赋 scrollTop 更可靠（Streamlit 容器层级复杂）
                const anchor = doc.getElementById('list-top-anchor');
                if (anchor) {{
                    anchor.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
                }} else {{
                    target.scrollTop = 0;
                }}
            }};
            doc.body.appendChild(btn);

            // 窗口 resize 时重新定位
            parent.window.addEventListener('resize', () => positionBtn(btn), {{ passive: true }});
        }};

        // 多次尝试，因为卡片可能延迟渲染
        setTimeout(() => {{ apply(); scroll(); setupScrollTopBtn(); }}, 300);
        setTimeout(() => {{ apply(); setupScrollTopBtn(); }}, 800);
        setTimeout(() => {{ apply(); setupScrollTopBtn(); }}, 1500);
    }})();
    </script>
    """,
    height=0,
)


# ═══════════════════════════════════════════════════════════════
# 行程详情已移入右侧栏（不再需要往下滚）
# ═══════════════════════════════════════════════════════════════


# 全局浮动"回到顶部"按钮已删除（按需求 4 改为列表内置顶按钮）


# 底部"项目说明 & 技术栈"已按需求 5 删除
