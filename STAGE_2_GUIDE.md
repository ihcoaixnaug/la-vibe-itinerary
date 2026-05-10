# 阶段 2 操作指南：拿到 my_places.csv

> 目标：得到一个含 30-50 行、5 列（name/address/lat/lng/maps_url）的 CSV 文件，放在 `data/my_places.csv`。
> 给你 **4 条路径**，按推荐度排序。任选一条跑通即可。

---

## 路径 0 · 直接在网页 UI 导入（最简单，无需命令行）

如果你只是想用自己的数据体验一下，不需要跑任何脚本：

1. 打开 app（本地 `streamlit run app.py` 或云端部署的链接）
2. 侧边栏 → 展开"用你自己的数据"
3. **方法一（推荐）**：粘贴 Google Maps 分享链接 → 点"开始抓取"
   - 打开 Google Maps → 你的列表 → 右上角分享 → 复制链接（`maps.app.goo.gl/...`）
   - ⚠️ 注意：抓取需要 Playwright，仅在本地运行时可用（Streamlit Cloud 云端需等冷启动完成）
4. **方法二（备选）**：上传 Google Takeout CSV
   - 有经纬度列：直接进 AI 处理
   - 无经纬度列（Takeout 原始格式）：自动用 Nominatim 查坐标（仅本地可用，云端 IP 被封）

---

## 路径 A · Playwright 抓共享列表（推荐，命令行）

### 准备：把你的列表设成共享

1. 在手机或电脑端打开 [Google Maps](https://maps.google.com)
2. 进入 "Your places" → "Lists"，打开你的 LA 美食列表（如果还没有就新建一个，加 30-50 家想吃的店）
3. 点列表名右边的 **⋮ (三个点)** → **Share list**
4. 切换 "Anyone with the link" → 复制链接（形如 `https://maps.app.goo.gl/XXXXXX`）

### 跑脚本

```bash
# 在项目根目录
cd ~/Documents/la-vibe-itinerary

# 第一次跑，加 --show-browser 看脚本在干什么
python scripts/01_scrape_maps.py \
  --url "https://maps.app.goo.gl/你的链接" \
  --show-browser \
  --max-items 5

# 看到前 5 个店都成功提取后，去掉调试参数跑全量
python scripts/01_scrape_maps.py \
  --url "https://maps.app.goo.gl/你的链接"
```

完成后 `data/my_places.csv` 就有了。

### 常见错误

| 报错 | 原因 | 解决 |
|---|---|---|
| `TimeoutError: page.goto` | 网络慢或链接错 | 检查链接，或加 `--show-browser` 看页面是否打得开 |
| `selector "div[role='feed']" not found` | Google 改 DOM 了 | 用 `--show-browser` 手动检查最新选择器 |
| 经纬度大量为空 | 详情页没加载完 | 把脚本里 `wait_for_timeout(1500)` 改成 `2500` |

---

## 路径 B · Google Takeout + Nominatim 地理编码

Google Takeout 导出的 CSV 只有店名，没有经纬度。用 `01c_geocode_takeout.py` 通过 OpenStreetMap 自动查坐标。

### 步骤

1. 打开 [takeout.google.com](https://takeout.google.com)
2. 点 **"Deselect all"**
3. 滚到 **"Maps (your places)"**，勾上
4. 拉到底点 **"Next step"** → 选 ".zip" → **"Create export"**
5. 等邮件 → 下载 ZIP → 解压
6. 找到你的列表 CSV，例如 `~/Downloads/Takeout/Saved/Default list.csv`

### 跑脚本

```bash
cd ~/Documents/la-vibe-itinerary
conda activate lbs
python scripts/01c_geocode_takeout.py \
  --input "~/Downloads/Takeout/Saved/Default list.csv" \
  --output data/my_places.csv \
  --city "Los Angeles, CA"
```

约 1-2 秒/家店（Nominatim 限速 1 req/s）。结果直接写到 `data/my_places.csv`。

**注意**：
- 命中率约 60-80%（中文店名或冷门地点可能查不到）
- 查不到的店会在终端打印出来，可手动补充经纬度（Google Maps URL 里的 `@lat,lng`）
- Nominatim 对 Streamlit Cloud 的 IP 封锁，必须在本地运行此脚本

---

## 路径 C · 用样例数据立刻进阶段 3（救生圈）

如果你想 **现在马上** 进入阶段 3 测全链路，跑这一条：

```bash
cd ~/Documents/la-vibe-itinerary
cp data/my_places_sample.csv data/my_places.csv
head -5 data/my_places.csv
```

我已经在 `my_places_sample.csv` 里放好了 30 家真实 LA 餐厅（Bestia、Sun Nong Dan、Bavel、Howlin' Ray's…），全部含真实经纬度，可以直接喂给阶段 3 的 GPT-4o。

后面真实抓取脚本调通后，覆盖一下 `my_places.csv` 即可。

---

## 完成判断

跑完任意一条路径后，运行：

```bash
python -c "
import pandas as pd
df = pd.read_csv('data/my_places.csv')
print(f'总行数：{len(df)}')
print(f'有经纬度：{df[[\"lat\",\"lng\"]].dropna().shape[0]}')
print(df.head())
"
```

只要总行数 ≥ 20、有经纬度比例 ≥ 80%，就达标，进入阶段 3。
