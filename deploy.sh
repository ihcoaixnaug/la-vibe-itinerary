#!/usr/bin/env bash
# LA Vibe Itinerary 一键预检 + 提交脚本
# 用法：cd ~/Documents/la-vibe-itinerary && bash deploy.sh
set -e

cd "$(dirname "$0")"

echo "═══════════════════════════════════════════════"
echo "  LA Vibe Itinerary · 部署预检"
echo "═══════════════════════════════════════════════"
echo ""

# ─── 1. 关键文件检查 ───
MISSING=()
for f in app.py requirements.txt README.md .gitignore; do
    [ -f "$f" ] || MISSING+=("$f")
done
if [ ${#MISSING[@]} -ne 0 ]; then
    echo "❌ 缺关键文件：${MISSING[*]}"
    exit 1
fi
echo "✅ 关键文件齐全（app.py / requirements.txt / README.md / .gitignore）"

# ─── 2. 安全检查：.env 绝对不能被提交 ───
if [ -f .env ]; then
    if ! grep -qE '^\.env$|^\.env[[:space:]]' .gitignore 2>/dev/null; then
        echo ""
        echo "🚨 严重安全风险：.env 存在但没被 .gitignore 排除！"
        echo "   你的 OPENROUTER_API_KEY 即将被推到 GitHub。立即停止。"
        exit 1
    fi
    echo "✅ .env 已被 .gitignore 排除（安全）"
fi

# ─── 3. 数据文件存在性 ───
if [ ! -f data/enriched_places.csv ]; then
    echo "⚠️  data/enriched_places.csv 不存在 — 部署后云端 app 会报错"
    echo "   先跑 python scripts/02_process_data.py 生成"
fi
if [ ! -f data/routes.json ]; then
    echo "⚠️  data/routes.json 不存在"
fi

# ─── 4. Git 初始化 ───
if [ ! -d .git ]; then
    echo ""
    echo "🆕 Git 仓库未初始化，正在初始化..."
    git init -q
fi
git branch -M main 2>/dev/null || true
echo "✅ Git 仓库就绪（main 分支）"

# ─── 5. 暂存所有文件 ───
git add -A

# ─── 6. 二次安全检查：scan diff ───
if git diff --cached --name-only | grep -qE '^\.env$'; then
    echo ""
    echo "🚨 STOP：.env 仍在待提交列表！终止。"
    git reset HEAD . > /dev/null
    exit 1
fi

# ─── 7. 显示待提交清单 ───
echo ""
echo "═══════════════════════════════════════════════"
echo "  待提交的文件清单："
echo "═══════════════════════════════════════════════"
git diff --cached --name-only | sed 's/^/   ✓ /'
echo ""
echo "总计：$(git diff --cached --name-only | wc -l | tr -d ' ') 个文件"
echo ""

# ─── 8. 确认提交 ───
read -r -p "👉 确认提交并继续？(y/N) " yn
if [[ ! "$yn" =~ ^[Yy]$ ]]; then
    echo "已取消。运行 'git reset' 撤销 stage。"
    git reset HEAD . > /dev/null
    exit 0
fi

git commit -q -m "feat: LA Vibe Itinerary v1.0 - AI-driven LBS itinerary generator

- 30 LA restaurants × 20 GPT-4o tags = 985+ data points
- Playwright + Takeout dual scraping pipeline
- DBSCAN haversine clustering + TSP route optimization
- Streamlit + folium interactive map
- Natural language query via GPT-4o for AI agent UX
"

echo ""
echo "✅ Commit 完成"
echo ""
echo "═══════════════════════════════════════════════"
echo "  接下来 3 步（手动 · 共 5 分钟）"
echo "═══════════════════════════════════════════════"
echo ""
echo "1️⃣  在浏览器打开（建一个 public repo，名字: la-vibe-itinerary）"
echo "   👉 https://github.com/new"
echo "   ⚠️  不要勾 Add README / License（你已经有了）"
echo ""
echo "2️⃣  回终端跑（把 \"你的用户名\" 替换成实际 GitHub 用户名）："
echo ""
echo "   git remote add origin https://github.com/你的用户名/la-vibe-itinerary.git"
echo "   git push -u origin main"
echo ""
echo "3️⃣  部署 Streamlit Cloud："
echo "   👉 https://share.streamlit.io/deploy"
echo "   - Repository: 你的用户名/la-vibe-itinerary"
echo "   - Branch: main"
echo "   - Main file path: app.py"
echo "   - 点 'Advanced settings' → Secrets，粘贴："
echo ""
echo "     OPENROUTER_API_KEY = \"sk-or-v1-你的key\""
echo ""
echo "   - 点 Deploy，等 3-5 分钟"
echo ""
echo "═══════════════════════════════════════════════"
