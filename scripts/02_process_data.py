"""
阶段 3：GPT-4o 20 维度标签增强管线
================================================================
读取 data/my_places.csv (5 列) → 调 GPT-4o → 写 data/enriched_places.csv (25 列)

核心特性：
  ✅ Pydantic 强校验输出，schema 不合规自动重试
  ✅ 文件级缓存 (data/cache/<slug>.json)，跑挂重启不重复花钱
  ✅ 异步并发（默认 5 并发，约 1 分钟跑完 30 家）
  ✅ 实时显示 tokens/成本，跑完算总账
  ✅ 失败自动列出，方便手动复跑

用法：
  # 全量
  python scripts/02_process_data.py

  # 测试 5 家
  python scripts/02_process_data.py --limit 5

  # 改并发或换模型
  python scripts/02_process_data.py --concurrency 3 --model openai/gpt-4o-mini

  # 强制重新生成（忽略缓存）
  python scripts/02_process_data.py --no-cache
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
from dotenv import load_dotenv
from openai import AsyncOpenAI, APIError
from pydantic import BaseModel, Field, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm.asyncio import tqdm

# ---- 路径 ----
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
PROMPTS = ROOT / "prompts"
CACHE.mkdir(exist_ok=True, parents=True)

# ---- 加载环境变量 ----
load_dotenv(ROOT / ".env")
API_KEY = os.getenv("OPENROUTER_API_KEY")
if not API_KEY:
    sys.exit("❌ 没读到 OPENROUTER_API_KEY，检查项目根目录的 .env 文件")

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=API_KEY,
    default_headers={
        "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", ""),
        "X-Title": os.getenv("OPENROUTER_X_TITLE", "LA Vibe Itinerary"),
    },
)

# ---- Pydantic Schema：20 维度强校验 ----
VibeEnum = Literal["casual", "trendy", "fine_dining", "dive", "cozy", "lively", "romantic"]
PriceTierEnum = Literal["$", "$$", "$$$", "$$$$"]
NoiseEnum = Literal["quiet", "moderate", "loud"]
DressEnum = Literal["casual", "smart_casual", "upscale"]
ReservationEnum = Literal["no", "recommended", "essential"]
ParkingEnum = Literal["easy", "moderate", "hard"]
# 国际公认菜系标准（按地理 / 民族划分，不混业态）
CuisineEnum = Literal[
    "Italian", "French", "Spanish", "Mediterranean", "Greek",
    "Japanese", "Korean", "Chinese", "Thai", "Vietnamese", "Indian",
    "Mexican", "Latin American",
    "Middle Eastern", "Israeli",
    "American", "Southern American", "Cajun",
    "Bakery", "Café", "BBQ", "Pizza", "Burger",
    "Fusion", "Other",
]


class EnrichedPlace(BaseModel):
    """20 维度结构化标签（每家店一份）。"""
    price_per_person_usd: int = Field(ge=5, le=500)
    price_tier: PriceTierEnum
    cuisine_primary: CuisineEnum
    cuisine_tags: list[str] = Field(min_length=1, max_length=5)
    must_try_dishes: list[str] = Field(min_length=1, max_length=5)
    dietary_friendly: list[str] = Field(default_factory=list)
    best_for: list[str] = Field(min_length=1, max_length=4)
    vibe: VibeEnum
    noise_level: NoiseEnum
    dress_code: DressEnum
    best_time_slots: list[str] = Field(min_length=1, max_length=5)
    avg_wait_minutes: int = Field(ge=0, le=300)
    reservation_needed: ReservationEnum
    parking_difficulty: ParkingEnum
    instagrammable_score: int = Field(ge=1, le=10)
    hidden_gem_score: int = Field(ge=1, le=10)
    value_score: int = Field(ge=1, le=10)
    crowd_typical_zh: str = Field(min_length=2, max_length=40)
    crowd_typical_en: str = Field(min_length=2, max_length=120)
    one_liner_zh: str = Field(min_length=2, max_length=60)
    one_liner_en: str = Field(min_length=2, max_length=180)
    avoid_if_zh: str = Field(min_length=2, max_length=50)
    avoid_if_en: str = Field(min_length=2, max_length=150)


# ---- 工具函数 ----
def slugify(name: str) -> str:
    """把店名转成文件安全的 slug，作缓存 key。"""
    s = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE)
    s = re.sub(r"[\s-]+", "_", s).strip("_").lower()
    return s or "unnamed"


def load_prompt() -> str:
    p = PROMPTS / "enrich_prompt.txt"
    if not p.exists():
        sys.exit(f"❌ 找不到 Prompt 文件：{p}")
    return p.read_text(encoding="utf-8")


def estimate_cost_usd(tokens_in: int, tokens_out: int, model: str) -> float:
    """根据当前 GPT-4o 在 OpenRouter 的官方价（2025）估算。"""
    if "gpt-4o-mini" in model:
        return tokens_in * 0.00000015 + tokens_out * 0.0000006
    # gpt-4o 默认
    return tokens_in * 0.0000025 + tokens_out * 0.00001


# ---- 单店调用 ----
async def call_gpt(
    name: str, address: str, system_prompt: str, model: str
) -> tuple[dict, int, int]:
    """调一次 GPT-4o，返回 (验证后的 data dict, tokens_in, tokens_out)"""
    user_msg = (
        f"Restaurant: {name}\n"
        f"Address: {address}\n\n"
        f"Generate the 20-dimension JSON profile now."
    )
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=900,
    )
    content = resp.choices[0].message.content or "{}"
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"非合法 JSON: {e}; 原文前 200 字: {content[:200]}")
    # Pydantic 校验（不合规会抛 ValidationError，触发重试）
    validated = EnrichedPlace.model_validate(raw)
    return (
        validated.model_dump(),
        resp.usage.prompt_tokens,
        resp.usage.completion_tokens,
    )


async def enrich_one(
    row: dict,
    system_prompt: str,
    model: str,
    sem: asyncio.Semaphore,
    use_cache: bool,
    progress_state: dict,
) -> Optional[dict]:
    """处理单家店：缓存 → 重试 3 次调 API → 写缓存 → 返回合并后的 dict"""
    name = str(row.get("name", "")).strip()
    if not name:
        return None
    # 防御：name 看起来像 lat/lng 数字，说明 CSV 解析错位了
    if re.match(r"^-?\d+\.?\d*$", name):
        print(f"⚠️ 跳过疑似坏数据 name={name!r}（CSV 列错位？检查引号）")
        return None

    cache_path = CACHE / f"{slugify(name)}.json"
    if use_cache and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            progress_state["cached"] += 1
            return cached
        except Exception:
            pass  # 缓存损坏，重新跑

    address = str(row.get("address", "")).strip()
    last_err: Optional[Exception] = None

    async with sem:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(min=2, max=10),
                retry=retry_if_exception_type((ValidationError, ValueError, APIError)),
                reraise=True,
            ):
                with attempt:
                    data, tin, tout = await call_gpt(name, address, system_prompt, model)
        except Exception as e:
            last_err = e
            progress_state["failed"].append({"name": name, "error": str(e)[:120]})
            return None

    progress_state["tokens_in"] += tin
    progress_state["tokens_out"] += tout
    progress_state["cost"] += estimate_cost_usd(tin, tout, model)
    progress_state["succeeded"] += 1

    # 合并原始字段 + AI 增强字段
    output = {**row, **data}
    cache_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


# ---- 主流程 ----
async def main(args):
    df = pd.read_csv(args.input)
    if args.limit:
        df = df.head(args.limit)
    n = len(df)
    print(f"📂 输入：{args.input.name} - {n} 家店")
    print(f"🤖 模型：{args.model} | 并发：{args.concurrency} | 缓存：{'开' if not args.no_cache else '关'}")

    system_prompt = load_prompt()
    sem = asyncio.Semaphore(args.concurrency)
    state = {
        "cached": 0,
        "succeeded": 0,
        "failed": [],
        "tokens_in": 0,
        "tokens_out": 0,
        "cost": 0.0,
    }

    t0 = time.time()
    tasks = [
        enrich_one(row.to_dict(), system_prompt, args.model, sem, not args.no_cache, state)
        for _, row in df.iterrows()
    ]
    results: list[dict] = []
    for coro in tqdm.as_completed(tasks, total=n, desc="GPT-4o 增强中"):
        r = await coro
        if r:
            results.append(r)

    elapsed = time.time() - t0

    # 写出 CSV
    out_df = pd.DataFrame(results)
    # 把 list 字段转成分号分隔的字符串，方便 CSV 阅读 / 后续 Streamlit 解析
    list_cols = [
        "cuisine_tags",
        "must_try_dishes",
        "dietary_friendly",
        "best_for",
        "best_time_slots",
    ]
    for c in list_cols:
        if c in out_df.columns:
            out_df[c + "_str"] = out_df[c].apply(
                lambda x: " | ".join(x) if isinstance(x, list) else str(x or "")
            )
    out_df.to_csv(args.output, index=False)

    # 终端统计
    n_data_points = len(results) * 20 + sum(
        len(r.get(c, [])) for r in results for c in list_cols
    )
    print(f"\n{'='*60}")
    print(f"✅ 完成：{state['succeeded']} 新调 + {state['cached']} 缓存命中 = {len(results)}/{n}")
    print(f"💰 本次新调用：input {state['tokens_in']:,} + output {state['tokens_out']:,} tokens")
    print(f"   估算成本：${state['cost']:.4f}")
    print(f"⏱  耗时：{elapsed:.1f} 秒")
    print(f"📊 颗粒度数据点：约 {n_data_points} 个（{len(results)} 店 × 20 字段 + 列表展开）")
    print(f"💾 已写入：{args.output}")
    if state["failed"]:
        print(f"\n⚠️ 失败 {len(state['failed'])} 家：")
        for f in state["failed"]:
            print(f"   · {f['name']}: {f['error']}")

    # 抽查一家
    if results:
        sample = random.choice(results)
        print(f"\n🔎 随机抽查 → {sample.get('name')}")
        for k in ["one_liner_zh", "must_try_dishes", "vibe", "price_per_person_usd", "best_for"]:
            print(f"   {k}: {sample.get(k)}")


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DATA / "my_places.csv")
    ap.add_argument("--output", type=Path, default=DATA / "enriched_places.csv")
    ap.add_argument("--model", type=str, default="openai/gpt-4o")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 家")
    ap.add_argument("--no-cache", action="store_true", help="忽略并覆盖缓存")
    return ap.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
