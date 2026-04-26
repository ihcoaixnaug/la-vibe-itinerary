"""
阶段 1 自检脚本：验证 OpenRouter + GPT-4o 通路。
==================================================
跑通的标志：终端打印出 GPT-4o 的中文回复 + token 用量。

如果失败，常见原因：
  1. .env 文件不在当前目录（必须 cd 到项目根目录再跑）
  2. Key 复制时多了空格或换行
  3. OpenRouter 账户余额为 0（去 https://openrouter.ai/credits 充 $5 即可）
"""
import os
import sys
from dotenv import load_dotenv
from openai import OpenAI, AuthenticationError, APIError

# 1) 读取 .env
load_dotenv()
api_key = os.getenv("OPENROUTER_API_KEY", "").strip()

if not api_key or api_key.startswith("sk-or-v1-在这里"):
    sys.exit(
        "❌ 没有读到合法的 OPENROUTER_API_KEY。\n"
        "   检查清单：\n"
        "   - 项目根目录有没有 .env 文件（用 ls -la 看，注意有点）\n"
        "   - .env 里的值是不是真实 Key，不是占位文字\n"
        "   - Key 前后没有空格或引号"
    )

if not api_key.startswith("sk-or-"):
    print(f"⚠️  Key 格式异常：应以 sk-or- 开头，当前是 {api_key[:10]}...")
    print("    如果你确认是 OpenAI 直连 Key，把 base_url 改成 https://api.openai.com/v1\n")

# 2) 初始化 client（OpenRouter 用 OpenAI 兼容接口）
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
    default_headers={
        "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", ""),
        "X-Title": os.getenv("OPENROUTER_X_TITLE", "LA Vibe Itinerary"),
    },
)

print("📡 正在调用 GPT-4o（约需 3-8 秒）...\n")

# 3) 发请求
try:
    resp = client.chat.completions.create(
        model="openai/gpt-4o",
        messages=[
            {
                "role": "system",
                "content": "你是一个洛杉矶探店达人，回答简短精准。",
            },
            {
                "role": "user",
                "content": "用一句话推荐 LA 一家本地人才知道的低调好餐厅，并说明它的招牌菜。",
            },
        ],
        max_tokens=150,
        temperature=0.7,
    )
except AuthenticationError:
    sys.exit("❌ Key 无效或已被禁用。去 https://openrouter.ai/keys 检查。")
except APIError as e:
    sys.exit(f"❌ OpenRouter 接口报错：{e}")

# 4) 打印结果
content = resp.choices[0].message.content
usage = resp.usage

print("=" * 60)
print("✅ GPT-4o 回复：")
print("=" * 60)
print(content)
print("=" * 60)
print(f"📊 用量：input {usage.prompt_tokens} + output {usage.completion_tokens} = {usage.total_tokens} tokens")
print(f"💰 本次成本估算：约 ${usage.total_tokens * 0.00001:.4f}")
print("\n🎉 阶段 1 完成！可以进入阶段 2（Google Maps 数据抓取）了。")
