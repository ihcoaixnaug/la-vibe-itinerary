# LA Vibe Itinerary — 技术参考文档

> 给未来接手的工程师或 AI Agent 看的完整技术指引。
> 最后更新：2026-05-10

---

## 目录

1. [项目概览](#1-项目概览)
2. [文件地图](#2-文件地图)
3. [数据管线（离线）](#3-数据管线离线)
4. [Web UI 架构（app.py）](#4-web-ui-架构apppy)
5. [用户导入数据流](#5-用户导入数据流)
6. [Streamlit Cloud 部署配置](#6-streamlit-cloud-部署配置)
7. [关键函数索引](#7-关键函数索引)
8. [Session State 状态机](#8-session-state-状态机)
9. [已知问题与绕过方案](#9-已知问题与绕过方案)
10. [本地开发环境](#10-本地开发环境)

---

## 1. 项目概览

**一句话**：把 Google Maps 收藏夹变成可执行打卡攻略的 AI Agent。

**核心功能**：
- 自然语言筛店（"今晚约会 预算80"）→ GPT-4o 语义匹配
- DBSCAN 地理聚类 + TSP 路径优化 → 最短打卡路线
- Web UI 导入个人数据：Google Maps 分享链接抓取 / Takeout CSV 上传

**运行时依赖**：
- LLM：GPT-4o via OpenRouter（`OPENROUTER_API_KEY`）
- Python 3.12（greenlet/Playwright 与 3.14 不兼容）

---

## 2. 文件地图

```
la-vibe-itinerary/
│
├── app.py                        # 主程序，~1765 行，单文件 Streamlit app
│
├── scripts/
│   ├── 01_scrape_maps.py         # Playwright 抓取 Google Maps 共享列表 → CSV
│   ├── 01b_parse_takeout.py      # Google Takeout JSON 解析（旧备选方案）
│   ├── 01c_geocode_takeout.py    # Takeout CSV → Nominatim 地理编码 → CSV（新）
│   ├── 02_process_data.py        # GPT-4o 增强 + Pydantic 校验 + 文件缓存
│   └── 03_cluster_routes.py      # DBSCAN 聚类 + TSP 路径优化 → routes.json
│
├── data/
│   ├── my_places_sample.csv      # 30 家预置 LA 样例（开箱即用）
│   ├── my_places.csv             # 当前使用的原始数据（管线输入）
│   ├── enriched_places.csv       # GPT-4o 增强后的完整数据（app 主数据源）
│   ├── routes.json               # 聚类 + 路径优化结果
│   └── cache/                    # 02_process_data.py 的 per-place JSON 缓存
│
├── prompts/
│   └── enrich_prompt.txt         # 20 维度 GPT-4o Prompt（含 few-shot）
│
├── docs/
│   └── demo_screenshots/         # README 截图（.png）和演示视频（.mov/.mp4）
│
├── requirements.txt              # Python 依赖（playwright==1.49.0 固定版本）
├── packages.txt                  # Streamlit Cloud apt 系统依赖（Chromium libs）
├── runtime.txt                   # 指定 Python 版本（python-3.12）
├── .python-version               # pyenv 风格版本锁（3.12），双保险
├── .env                          # 本地 API Key（不入 Git）
├── .env.example                  # API Key 模板
├── .gitignore                    # 排除 .env、data/cache/、data/Takeout*/
├── README.md                     # 用户向文档（功能介绍 + 快速启动）
├── DEPLOY_GUIDE.md               # 部署操作手册
├── STAGE_2_GUIDE.md              # 数据准备指南（获取 my_places.csv）
└── TECHNICAL.md                  # 本文档
```

### 关键文件说明

**`packages.txt`**：Streamlit Cloud build 阶段用 apt 安装的系统库，全部是 Chromium 依赖（libnss3、libgbm1 等）。没有这个文件，`playwright install chromium` 会因缺系统库失败。不支持 `#` 注释。

**`runtime.txt`**：告知 Streamlit Cloud 用 Python 3.12。格式必须是 `python-3.12`（含 `python-` 前缀）。但此文件不总是被识别——**最可靠的方式是在 Streamlit Cloud Dashboard → Settings → Advanced → Python version 手动选 3.12**。

**`requirements.txt`中的 `playwright==1.49.0`**：固定版本。Playwright 1.50+ 在 Streamlit Cloud 的 Debian 系统上因 `greenlet` 编译失败无法安装（Python 3.14 兼容问题）。

---

## 3. 数据管线（离线）

```
[Google Maps 共享链接]           [Google Takeout CSV]       [手动 CSV]
         ↓                               ↓                      ↓
  01_scrape_maps.py           01c_geocode_takeout.py      直接使用（需含lat/lng）
  （Playwright 点击抓取）      （Nominatim 地理编码）
         ↓                               ↓
                   data/my_places.csv
                   （5 列：name, address, lat, lng, maps_url）
                            ↓
                  02_process_data.py
                  （GPT-4o via OpenRouter，Pydantic 校验）
                  （data/cache/ 按店名缓存，断点续跑）
                            ↓
                  data/enriched_places.csv
                  （25 列，双语，20 维 AI 标签）
                            ↓
                  03_cluster_routes.py
                  （DBSCAN haversine，eps=2.5km，min_samples=2）
                  （簇≤8 全排列 TSP，>8 最近邻贪心）
                            ↓
                     data/routes.json
```

### 01_scrape_maps.py 关键逻辑

- **Path A**（旧列表/搜索结果）：检测 `<a href*="/maps/place/">` 链接，滚动加载全量后批量访问详情页补地址
- **Path B**（共享列表）：检测按钮选择器 `button.SMP2wb`，点击每个按钮进入详情页
- **坐标提取**：优先从 URL `data=` 参数解析 `!3d<lat>!4d<lng>`（真实地点坐标）；视口 `/@lat,lng` 只作兜底（偏差可达数公里）
- **浏览器启动参数**：`--no-sandbox --disable-dev-shm-usage`（云端容器必须）

### 01c_geocode_takeout.py 关键逻辑

- 输入：Google Takeout 导出的 CSV（只有店名，无坐标）
- API：OpenStreetMap Nominatim，1 req/s 限制，`countrycodes=us`
- 过滤：LA bounding box `lat∈[33.5, 34.8]，lng∈[-119.0, -117.0]`，防止名称歧义命中其他城市
- 注意：Nominatim 会封锁 Streamlit Cloud 的 AWS IP，仅适合本地运行

### 02_process_data.py 关键逻辑

- `EnrichedPlace` Pydantic 模型定义了所有 25 列的类型和枚举约束
- `data/cache/<slug>.json` 缓存每家店的处理结果，重跑只处理新增的店
- tenacity 指数退避重试，最多 3 次
- 费用：约 $0.007/店（GPT-4o，截至 2026-05）

---

## 4. Web UI 架构（app.py）

### 整体布局

```
app.py 执行顺序（每次 rerun 从头到尾）：

[1] 顶部：playwright install 检查（冷启动时下载浏览器）
[2] 导入 + 常量定义
[3] Nominatim 工具函数（_in_la_bounds, _nom_geocode, _nom_probe）
[4] API Key 获取（_get_api_key）
[5] Pydantic schema（EnrichedPlace，与 02_process_data.py 保持一致）
[6] _enrich_place_sync（AI 增强单条数据，用于 Web UI 实时处理）
[7] i18n 系统（init_lang, t()，支持 zh/en 切换）
[8] 地理计算（haversine_km, dbscan_cluster, optimize_route）
[9] AI 推荐（parse_intent, parse_budget, build_places_summary, ai_recommend）
[10] load_data()：加载 enriched_places.csv 或 session_state["_custom_df"]
[11] ★ _pending_scrape 处理块（运行抓取子进程，阻塞直到完成）
[12] ★ _pending_upload 处理块（运行 AI 增强，显示进度条）
[13] 侧边栏 UI（筛选器 + 导入数据区）
[14] 主区：AI 推荐 → 应用筛选 → 地图（folium）+ 卡片列表
[15] 行程生成区（DBSCAN + TSP + Google Maps 链接）
```

### 双栏布局

```
侧边栏（st.sidebar）
  ├── 语言切换
  ├── 自然语言输入 + AI 推荐按钮
  ├── 多维筛选器（价格/氛围/场景/菜系/宝藏指数/排序）
  ├── 一键生成行程按钮
  └── 导入数据 expander（见第 5 节）

主区（两栏 6:4）
  ├── 左栏（60%）：folium 交互地图
  └── 右栏（40%）：可滚动卡片列表
```

---

## 5. 用户导入数据流

### 导入数据 expander 的三分支结构

```python
if st.session_state.get("_custom_df") is not None:
    # 分支 1：已有自定义数据 → 显示状态 + 恢复默认按钮
elif st.session_state.get("_scraped_data") is not None:
    # 分支 2：刚抓取完，待 AI 处理 → 预览 + AI 按钮 + 取消按钮
else:
    # 分支 3：初始状态
    # 方法一（推荐）：Google Maps URL → text_input + 开始抓取按钮
    # 方法二（备选）：Takeout CSV → file_uploader
    #   路径 A：CSV 含 lat/lng → 直接进 AI 增强
    #   路径 B：CSV 无坐标 → Nominatim 地理编码 → AI 增强
```

### 完整数据流（用户操作视角）

```
[方法一：Google Maps URL]

用户粘贴链接 → 点"开始抓取"
  → st.session_state["_pending_scrape"] = url → st.rerun()
  → app.py 第 11 步检测到 _pending_scrape
  → subprocess.Popen([sys.executable, "scripts/01_scrape_maps.py", "--url", url])
  → 实时 stream stdout 到 st.empty() caption
  → returncode == 0 → 读 CSV → st.session_state["_scraped_data"] = rows → st.rerun()
  → 展示分支 2（预览 + AI 按钮）
  → 用户点"开始 AI 数据处理"
  → st.session_state["_pending_upload"] = rows → st.rerun()
  → app.py 第 12 步检测到 _pending_upload
  → 逐条调用 _enrich_place_sync（tenacity 重试）
  → st.session_state["_custom_df"] = enriched_df → st.rerun()
  → load_data() 返回 _custom_df，整个 app 用新数据渲染

[方法二路径 A：含坐标 CSV]

用户上传 CSV → 检测到 lat/lng 列
  → 预览前 5 行 → 用户点"开始 AI 数据处理"
  → 同上 _pending_upload 流程

[方法二路径 B：无坐标 CSV（Takeout）]

用户上传 CSV → 无 lat/lng 列
  → _nom_probe() 探测 Nominatim 连通性
  → 若失败：显示本地运行命令（01c_geocode_takeout.py）
  → 若成功：逐条 Nominatim 查坐标（st.progress 进度条）→ 缓存到 session_state
  → 展示找到/丢失统计 → 用户点"开始 AI 数据处理"
  → 同上 _pending_upload 流程
```

---

## 6. Streamlit Cloud 部署配置

### 必须配置项（缺一不可）

| 配置项 | 位置 | 内容 | 说明 |
|---|---|---|---|
| Python 版本 | Dashboard → Settings → Advanced → Python version | **3.12** | 最重要；3.14 会导致 greenlet 编译失败 |
| API Key | Dashboard → Settings → Secrets | `OPENROUTER_API_KEY = "sk-or-..."` | toml 格式 |
| 系统依赖 | `packages.txt`（repo 根目录） | 17 个 Chromium 系统库 | 无注释，否则 apt 报错 |
| Playwright 版本 | `requirements.txt` | `playwright==1.49.0` | 固定，不可用 `>=` |
| 浏览器安装 | `app.py` 顶部 | `os.system("playwright install chromium")` | 每次冷启动执行，约 15s |

### 冷启动时序

```
Streamlit Cloud 容器启动
  → apt install（packages.txt 的系统库）       ← build 阶段，一次性
  → pip install（requirements.txt）            ← build 阶段，一次性
  → Python 执行 app.py（第 1 行）
      → glob 检查 ~/.cache/ms-playwright/chromium-*/chrome-linux/chrome
      → 不存在 → os.system("playwright install chromium")  ← 约 15s
      → 存在 → 跳过
  → Streamlit 开始渲染 UI
```

### 部署后检查清单

- [ ] Streamlit Cloud 日志无 `greenlet` 编译错误（Python 版本正确）
- [ ] 日志有 `Downloading Chromium` 或 `chromium already installed`（Playwright OK）
- [ ] 打开 app，地图加载 30 个 marker
- [ ] 侧边栏"用你自己的数据"→ 粘贴 Google Maps 链接 → 抓取成功

---

## 7. 关键函数索引

| 函数 | 位置 | 说明 |
|---|---|---|
| `_in_la_bounds` | app.py:52 | LA bounding box 过滤（Nominatim 防歧义）|
| `_nom_geocode` | app.py:56 | Nominatim 地理编码，返回 (lat, lng, display_name) |
| `_nom_probe` | app.py:77 | 探测 Nominatim 连通性，返回错误字符串或 "" |
| `_get_api_key` | app.py:92 | 优先 st.secrets，兜底 os.environ / .env |
| `EnrichedPlace` | app.py:131 | Pydantic v2 schema，25 列，与 02_process_data.py 保持一致 |
| `_enrich_place_sync` | app.py:163 | 同步调用 GPT-4o 增强单条数据，tenacity 重试 3 次 |
| `init_lang` | app.py:418 | 从 query param / session state 初始化语言 |
| `t` | app.py:424 | i18n 翻译函数，支持 kwargs 插值 |
| `haversine_km` | app.py:457 | 球面距离计算 |
| `dbscan_cluster` | app.py:465 | DBSCAN 地理聚类，eps=2.5km，min_samples=2 |
| `optimize_route` | app.py:472 | TSP（≤8 全排列，>8 最近邻贪心）|
| `parse_intent` | app.py:519 | 从自然语言提取筛选意图 |
| `parse_budget` | app.py:559 | 从 query 提取预算数字，用于硬截断候选池 |
| `build_places_summary` | app.py:576 | 把 DataFrame 序列化成 GPT-4o prompt 的文本 |
| `ai_recommend` | app.py:593 | 调用 GPT-4o 返回推荐结果 JSON |
| `load_data` | app.py:659 | 加载数据：优先 _custom_df，兜底 enriched_places.csv |
| `gmap_route_url` | app.py:1271 | 生成 Google Maps 多点导航 URL |
| `make_popup` | app.py:1288 | 生成 folium marker popup HTML |
| `parse_latlng` | scripts/01_scrape_maps.py:45 | 从 Google Maps URL 提取坐标 |
| `collect_by_clicking` | scripts/01_scrape_maps.py:195 | 共享列表点击抓取主逻辑 |
| `geocode` | scripts/01c_geocode_takeout.py:44 | Nominatim 查询单个地点 |

---

## 8. Session State 状态机

```
关键 key：

_pending_scrape    str     触发抓取：存 Google Maps URL，rerun 后被 _pending_scrape 处理块消费
_scraped_data      list    抓取结果：rows，展示预览等待用户确认
_pending_upload    list    触发 AI 增强：rows，rerun 后被 _pending_upload 处理块消费
_custom_df         df      已完成增强的自定义数据，load_data() 优先返回此值
_custom_data_info  dict    {n: int, failed: list[str]}，expander 状态展示用
_geocoded_<key>   list    Nominatim 结果缓存（key = 文件名+size），避免重复查询

last_nl_query      str     最后一次自然语言查询，触发 AI 推荐
ai_result          dict    AI 推荐结果缓存
_card_click_count  int     卡片点击计数器，用于强制 folium 重渲染
_card_just_clicked str     当前点击的卡片名，跳过 tooltip 处理

pills_vibes        list    氛围筛选器状态
pills_scenarios    list    场景筛选器状态
pills_cuisines     list    菜系筛选器状态
```

### 状态转换图（导入流）

```
[初始]
  → 用户粘贴 URL + 点抓取
    → _pending_scrape 存在 → [抓取中]
      → 成功 → _scraped_data 存在 → [待确认]
        → 用户点 AI 处理 → _pending_upload 存在 → [AI 增强中]
          → 成功 → _custom_df 存在 → [使用自定义数据]
            → 用户点恢复默认 → _custom_df 删除 → [初始]
```

---

## 9. 已知问题与绕过方案

### Streamlit Cloud 特有问题

| 问题 | 根因 | 解决 |
|---|---|---|
| `greenlet` 编译失败 | Streamlit Cloud 默认 Python 3.14，greenlet 无 3.14 wheel | Dashboard 手动选 Python 3.12 |
| `packages.txt` 报 "Unable to locate package #" | apt 不支持 `#` 注释 | packages.txt 里不写任何注释 |
| Nominatim 拒绝访问 | Streamlit Cloud IP（AWS us-east-1）被封 | 前端探活失败后显示本地运行指引 |
| 抓取返回码 1 但无错误信息 | stderr 被 DEVNULL 丢弃 | 已改为 `stderr=subprocess.PIPE`，错误会显示在 UI |

### Playwright / 抓取问题

| 问题 | 根因 | 解决 |
|---|---|---|
| Path A 找到 0 个链接 | 共享列表用 `<button>` 而非 `<a href>` | `run()` 自动 fallback 到 Path B（按钮点击）|
| 坐标偏差数公里 | `/@lat,lng` 是视口中心不是地点 | 改用 `!3d<lat>!4d<lng>` from data= 参数 |
| `button.SMP2wb` 找不到 | Google Maps DOM 更新 | 用 `--show-browser` 检查最新选择器，更新 `_BTN_SEL` |

### app.py / 通用问题

| 问题 | 根因 | 解决 |
|---|---|---|
| folium 地图不重渲染 | st_folium 固定 key 不触发重绘 | key 里加 `_card_click_count` 计数器 |
| 点击卡片触发 tooltip 处理 | last_object_clicked_tooltip 跨 rerun 持久 | `_card_just_clicked` flag 跳过 |
| AI 增强全部失败 | exception 被 `except: pass` 吞掉 | 改为 `st.warning(f"[{name}] 失败：{err}")` |
| API Key 401 | .env 不同步到云端 | 走 st.secrets；`_get_api_key()` 优先读 st.secrets |

---

## 10. 本地开发环境

```bash
# 创建并激活环境
conda create -n lbs python=3.12 -y
conda activate lbs

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器（只需一次）
playwright install chromium

# 配置 API Key
cp .env.example .env
# 编辑 .env：OPENROUTER_API_KEY=sk-or-xxxxxxxx

# 启动（样例数据）
streamlit run app.py

# 完整管线（自己的数据）
python scripts/01_scrape_maps.py --url "https://maps.app.goo.gl/..." --show-browser
python scripts/02_process_data.py
python scripts/03_cluster_routes.py
streamlit run app.py
```

### 常用调试命令

```bash
# 测试抓取（前 3 条，显示浏览器）
python scripts/01_scrape_maps.py \
  --url "https://maps.app.goo.gl/FGmtswh4cFzvmuCY6" \
  --show-browser --max-items 3

# 测试 Nominatim 地理编码
python scripts/01c_geocode_takeout.py \
  --input "data/Takeout_tuotuo/Saved/Default list.csv" \
  --output data/geocoded_test.csv

# 检查数据质量
python -c "
import pandas as pd
df = pd.read_csv('data/enriched_places.csv')
print(df.shape, list(df.columns))
print(df[['name','lat','lng','vibe','cuisine_primary']].head())
"

# 测试 API Key 连通
python test_api.py
```
