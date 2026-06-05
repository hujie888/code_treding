#!/usr/bin/env python3
"""
AI 情报日报 → Lark 推送
数据源：GitHub Trending / OSSInsight / Hacker News / MCP 生态
处理：Kimi API 筛选 + 总结
推送：飞书 Webhook 富文本卡片

依赖：pip install httpx python-dotenv
环境变量（.env 或系统变量）：
  KIMI_API_KEY=sk-...
  LARK_WEBHOOK=https://open.larksuite.com/open-apis/bot/v2/hook/xxxxx
"""

from __future__ import annotations  # Python 3.8 兼容 list[dict] 类型注解

import httpx
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

KIMI_API_KEY = os.environ["KIMI_API_KEY"]
LARK_WEBHOOK = os.environ["LARK_WEBHOOK"]
TODAY             = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# 数据抓取用：自动读取系统代理
_proxy = os.environ.get("https_proxy") or os.environ.get("http_proxy")
client = httpx.Client(proxy=_proxy, follow_redirects=True, timeout=15)

# 大模型调用用：由 LLM_PROXY 单独控制（true=走系统代理，false/不设=直连）
_llm_use_proxy = os.environ.get("LLM_PROXY", "").lower() in ("1", "true", "yes")
llm_client = httpx.Client(proxy=_proxy if _llm_use_proxy else None, trust_env=_llm_use_proxy, follow_redirects=True, timeout=60)

# ─────────────────────────────────────────────
# 1. 数据抓取层
# ─────────────────────────────────────────────

def fetch_github_trending() -> list[dict]:
    """
    GitHub 全榜 Trending（直接爬官方页面），过滤 AI 相关
    返回字段：name / url / description / stars_today / language
    """
    import html as html_lib
    ai_keywords = {"ai", "agent", "llm", "gpt", "claude", "mcp", "rag",
                   "diffusion", "embedding", "langchain", "openai", "gemini",
                   "mistral", "ollama", "transformer", "copilot", "cursor",
                   "vtuber", "tts", "ocr", "notebook"}
    try:
        resp = client.get(
            "https://github.com/trending",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        articles = re.findall(
            r'<article class="Box-row">(.*?)</article>', resp.text, re.DOTALL
        )
        results = []
        for art in articles:
            name_m = re.search(
                r'/login\?return_to=%2F([A-Za-z0-9_.-]+%2F[A-Za-z0-9_.-]+)', art
            )
            if not name_m:
                continue
            repo = name_m.group(1).replace("%2F", "/")

            desc_m = re.search(r'<p\s+class="col-9[^"]*"[^>]*>(.*?)</p>', art, re.DOTALL)
            desc = html_lib.unescape(
                re.sub(r"\s+", " ", desc_m.group(1)).strip()
            ) if desc_m else ""

            today_m = re.search(r"([0-9,]+)\s+stars today", art)
            stars_today = int(today_m.group(1).replace(",", "")) if today_m else 0

            lang_m = re.search(r'itemprop="programmingLanguage"[^>]*>(.*?)</span>', art)
            lang = lang_m.group(1).strip() if lang_m else ""

            combined = (repo + " " + desc).lower()
            if any(kw in combined for kw in ai_keywords):
                results.append({
                    "name":        repo,
                    "url":         f"https://github.com/{repo}",
                    "description": desc,
                    "stars_today": stars_today,
                    "language":    lang,
                })
        return results[:12]
    except Exception as e:
        print(f"[GitHub Trending] 失败: {e}")
        return []


def fetch_ossinsight_trending() -> list[dict]:
    """
    OSSInsight：从多个 AI Collection 聚合，按近 28 天涨星排序
    https://ossinsight.io/trending/ai
    """
    # 核心 AI 子领域 collection（来自 ossinsight.io/trending/ai）
    AI_COLLECTION_IDS = [
        10098,  # AI Agent Frameworks
        10076,  # LLM Tools
        10106,  # Coding Agents
        10105,  # MCP Servers
        10108,  # RAG Frameworks
        10109,  # LLM Inference Engines
        10087,  # LLM DevTools
    ]
    BASE = "https://api.ossinsight.io/v1"
    seen, all_repos = set(), []
    for cid in AI_COLLECTION_IDS:
        try:
            resp = client.get(
                f"{BASE}/collections/{cid}/ranking_by_stars/",
                params={"period": "past_28_days"},
            )
            resp.raise_for_status()
            for row in resp.json().get("data", {}).get("rows", []):
                name = row.get("repo_name", "")
                if name and name not in seen:
                    seen.add(name)
                    all_repos.append({
                        "name":      name,
                        "url":       f"https://github.com/{name}",
                        "stars":     int(row.get("total", 0)),
                        "stars_28d": int(row.get("current_period_growth", 0)),
                    })
        except Exception as e:
            print(f"[OSSInsight] collection {cid} 失败: {e}")
            continue
    # 按近 28 天涨星降序，取 Top 12
    all_repos.sort(key=lambda r: r["stars_28d"], reverse=True)
    return all_repos[:12]


def fetch_ossinsight_hot() -> list[dict]:
    """
    OSSInsight 通用趋势榜（24h 全量），按 total_score 过滤 AI 相关
    https://api.ossinsight.io/v1/trends/repos/?period=past_24_hours
    """
    try:
        resp = client.get(
            "https://api.ossinsight.io/v1/trends/repos/",
            params={"period": "past_24_hours", "language": "All"},
        )
        resp.raise_for_status()
        rows = resp.json().get("data", {}).get("rows", [])
        ai_keywords = {"ai", "agent", "llm", "gpt", "claude", "mcp", "rag",
                       "diffusion", "embedding", "openai", "gemini", "mistral",
                       "ollama", "copilot", "cursor", "vtuber", "tts"}
        results = []
        for row in rows:
            name = row.get("repo_name", "")
            desc = (row.get("description") or "").lower()
            if any(kw in (name + " " + desc).lower() for kw in ai_keywords):
                results.append({
                    "name":        name,
                    "url":         f"https://github.com/{name}",
                    "description": row.get("description", ""),
                    "stars":       int(row.get("stars", 0)),
                    "score_24h":   float(row.get("total_score", 0)),
                    "language":    row.get("primary_language", ""),
                })
        results.sort(key=lambda r: r["score_24h"], reverse=True)
        return results[:10]
    except Exception as e:
        print(f"[OSSInsight Hot] 失败: {e}")
        return []


def fetch_hacker_news_ai() -> list[dict]:
    """
    Hacker News Top Stories，过滤 AI/Agent/LLM 关键词
    使用官方 Firebase API
    """
    ai_keywords = {"ai", "agent", "llm", "gpt", "claude", "mcp", "model",
                   "openai", "anthropic", "gemini", "mistral", "copilot",
                   "vector", "embedding", "rag", "diffusion", "cursor"}
    try:
        top_ids = client.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
        ).json()[:60]

        results = []
        for sid in top_ids:
            if len(results) >= 8:
                break
            try:
                item = client.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                ).json()
                title = (item.get("title") or "").lower()
                if any(kw in title for kw in ai_keywords):
                    results.append({
                        "title":  item.get("title", ""),
                        "url":    item.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                        "score":  item.get("score", 0),
                        "comments": item.get("descendants", 0),
                    })
            except Exception:
                continue
        return results
    except Exception as e:
        print(f"[Hacker News] 请求失败: {e}")
        return []


def fetch_mcp_new_servers() -> list[dict]:
    """
    GitHub Search API：搜索活跃的 MCP Server 和 AI Agent 仓库
    策略1：星数 >20 且近 14 天有推送（按星数排序，找有积累的项目）
    策略2：近 7 天新建的 MCP Server（发现新苗子）
    """
    from datetime import timedelta
    since_14d = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    since_7d  = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"

    queries = [
        # 有积累且近期活跃的 MCP Server（≥500星，上限5000过滤刷星）
        (f"topic:mcp-server stars:500..5000 pushed:>{since_14d}", 6),
        # 更广义的 MCP 生态（model-context-protocol 话题，≥500星）
        (f"topic:model-context-protocol stars:500..5000 pushed:>{since_14d}", 4),
        # 新建但已获得关注的 MCP Server（7天内创建且≥100星才算有价值）
        (f"topic:mcp-server created:>{since_7d} stars:>100", 4),
    ]

    seen, all_repos = set(), []
    for q, per_page in queries:
        try:
            resp = client.get(
                "https://api.github.com/search/repositories",
                params={"q": q, "sort": "stars", "order": "desc", "per_page": per_page},
                headers=headers,
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                name = item["full_name"]
                if name not in seen:
                    seen.add(name)
                    all_repos.append({
                        "name":        name,
                        "url":         item["html_url"],
                        "description": item.get("description", ""),
                        "stars":       item["stargazers_count"],
                        "created_at":  item["created_at"][:10],
                        "pushed_at":   (item.get("pushed_at") or "")[:10],
                    })
        except Exception as e:
            print(f"[MCP/Agent] 查询失败: {q[:50]}... {e}")
            continue

    # 按总星数降序，优先展示有影响力的项目
    all_repos.sort(key=lambda r: r["stars"], reverse=True)
    return all_repos[:12]


def fetch_watched_repos() -> list[dict]:
    """
    定向监控重点项目的最新 Release
    可在 WATCHED_REPOS 里加你关心的仓库
    """
    WATCHED_REPOS = [
        "cline/cline",
        "OpenDevin/OpenDevin",
        "langchain-ai/langgraph",
        "crewAIInc/crewAI",
        "modelcontextprotocol/servers",
        "anthropics/claude-code",          # 官方 CLI
        "BerriAI/litellm",
        "microsoft/autogen",
    ]
    results = []
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"

    for repo in WATCHED_REPOS:
        try:
            r = client.get(
                f"https://api.github.com/repos/{repo}/releases/latest",
                headers=headers,
            )
            if r.status_code == 200:
                rel = r.json()
                results.append({
                    "repo":       repo,
                    "url":        rel.get("html_url", ""),
                    "tag":        rel.get("tag_name", ""),
                    "published":  (rel.get("published_at") or "")[:10],
                    "body":       (rel.get("body") or "")[:300],
                })
        except Exception:
            continue
    # 只返回今天或昨天有新 Release 的
    from datetime import timedelta
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    fresh = [r for r in results if r["published"] >= yesterday]
    return fresh


# ─────────────────────────────────────────────
# 2. Claude API 总结层
# ─────────────────────────────────────────────

def summarize_with_kimi(raw_data: dict) -> str:
    """
    把所有原始数据打包送给 Kimi，返回结构化的中文 Markdown 摘要
    """
    prompt = f"""你是一位专注 AI 领域的技术编辑，今天是 {TODAY}。
下面是今天抓取到的原始数据，请生成一份简洁有料的中文技术日报。

## 严格格式规范
- 使用飞书兼容的 Markdown：**加粗**、`代码`、[文字](url)
- 链接格式只能是 [文字](url)，禁止在链接前后添加反引号或其他符号
- 全文使用中文，项目名保留英文原名

## 数据处理规则
- 对 GitHub 项目，用一句话提炼其核心价值，避免直译描述字段
- HN 条目附评论数，优先选评论数 > 50 的

## 三个板块

**🔥 今日 GitHub 热榜**（严格输出 5 条）
来源：GitHub Trending + OSSInsight，按今日涨星数降序。
处理：今日涨星为 0 或缺失的项目直接跳过；从剩余中取涨星最多的前 5 条，不足 5 条则取全部。
格式：`- **项目名** 今日 +N⭐ · [GitHub](url) · 一句话亮点`

**🤖 AI Agent & MCP 新动态**（严格输出 5 条）
来源：stars ≥ 500 的 MCP Server / model-context-protocol 生态仓库，以及重要 Release。
处理：按总星数从高到低排列，输出前 5 条；stars < 100 的项目一律跳过不展示；不要出现任何低知名度仓库。
格式：`- **项目名** ⭐ N(总) · [GitHub](url) · 一句话说清楚它解决什么问题`

**📰 HN 热议**（严格输出 5 条）
来源：Hacker News 当日 AI 相关热帖。
处理：按评论数降序选前 5 条；不足 5 条则取全部。
格式：`- **标题（中文翻译）** · [原文](url) · N 条讨论 · 一句话说争议点或亮点`

## 原始数据
{json.dumps(raw_data, ensure_ascii=False, indent=2)}

直接输出 Markdown 正文，不要加任何前言或解释。"""

    resp = llm_client.post(
        "https://api.moonshot.cn/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {KIMI_API_KEY}",
            "Content-Type":  "application/json",
        },
        json={
            "model":    "moonshot-v1-32k",
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ─────────────────────────────────────────────
# 3. Lark 推送层
# ─────────────────────────────────────────────

def send_lark_card(title: str, markdown_body: str) -> bool:
    """
    发送飞书互动卡片（Interactive Card）
    支持 Markdown + 颜色 header
    """
    payload = {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "title": {
                    "tag":     "plain_text",
                    "content": title,
                },
                "template": "blue",           # 可选: blue / green / red / orange / purple
                "subtitle": {
                    "tag":     "plain_text",
                    "content": f"📅 {TODAY}  ·  由 Kimi + Python 自动生成",
                },
            },
            "body": {
                "elements": [
                    {
                        "tag":     "markdown",
                        "content": markdown_body,
                    },
                    {
                        "tag": "hr",
                    },
                    {
                        "tag":     "markdown",
                        "content": "数据来源：GitHub Trending · OSSInsight · Hacker News · MCP Servers",
                    },
                ]
            },
        }
    }

    try:
        resp = client.post(LARK_WEBHOOK, json=payload)
        resp.raise_for_status()
        result = resp.json()
        if result.get("StatusCode") == 0 or result.get("code") == 0:
            print("✅ Lark 推送成功")
            return True
        else:
            print(f"⚠️  Lark 返回异常: {result}")
            return False
    except Exception as e:
        print(f"❌ Lark 推送失败: {e}")
        return False


def send_lark_text_fallback(content: str) -> bool:
    """备用：纯文本推送（卡片格式不支持时）"""
    payload = {"msg_type": "text", "content": {"text": content}}
    try:
        resp = client.post(LARK_WEBHOOK, json=payload)
        return resp.json().get("StatusCode") == 0
    except Exception:
        return False


# ─────────────────────────────────────────────
# 4. 主流程
# ─────────────────────────────────────────────

def main():
    print(f"{'='*50}")
    print(f"  AI 情报日报  {TODAY}")
    print(f"{'='*50}")

    # 顺序抓取各数据源
    print("\n[1/5] 抓取 GitHub Trending...")
    gh_trending = fetch_github_trending()
    print(f"  → {len(gh_trending)} 条 AI 仓库")

    print("[2/5] 抓取 OSSInsight AI Collections（28天）...")
    ossinsight   = fetch_ossinsight_trending()
    print(f"  → {len(ossinsight)} 条")

    print("[3/5] 抓取 OSSInsight 热榜（24h）...")
    ossinsight_hot = fetch_ossinsight_hot()
    print(f"  → {len(ossinsight_hot)} 条")

    print("[4/5] 抓取 Hacker News...")
    hn_items     = fetch_hacker_news_ai()
    print(f"  → {len(hn_items)} 条 AI 话题")

    print("[5/5] 抓取 MCP 新 Server + 重点项目 Release...")
    mcp_servers  = fetch_mcp_new_servers()
    watched      = fetch_watched_repos()
    print(f"  → MCP: {len(mcp_servers)} 个, Release: {len(watched)} 个")

    # 组合原始数据
    raw_data = {
        "github_trending":    gh_trending,
        "ossinsight_28d":     ossinsight,
        "ossinsight_hot_24h": ossinsight_hot,
        "hacker_news":        hn_items,
        "mcp_new_servers":    mcp_servers,
        "watched_releases":   watched,
    }

    # 没有任何数据时跳过
    total = sum(len(v) for v in raw_data.values())
    if total == 0:
        print("⚠️  所有数据源均无数据，跳过推送")
        return

    # Kimi 总结
    print(f"\n[Kimi] 正在生成摘要（共 {total} 条原始数据）...")
    try:
        summary = summarize_with_kimi(raw_data)
        print("  → 摘要生成成功")
    except Exception as e:
        print(f"  → Kimi 摘要失败: {e}")
        # 降级：直接推原始数据的简单版本
        summary = _fallback_summary(raw_data)

    # 推送 Lark
    print("\n[Lark] 推送中...")
    title = f"🤖 AI 技术日报 · {TODAY}"
    ok = send_lark_card(title, summary)
    if not ok:
        # 降级到纯文本
        send_lark_text_fallback(f"{title}\n\n{summary}")

    print("\n完成！")


def _fallback_summary(raw_data: dict) -> str:
    """Claude 不可用时的降级摘要"""
    lines = []
    if raw_data["github_trending"]:
        lines.append("**🔥 GitHub AI 今日热榜**")
        for r in raw_data["github_trending"][:5]:
            lines.append(f"- [{r['name']}]({r['url']})  ⭐+{r.get('stars_today',0)}  {r['description'][:60]}")
    if raw_data["hacker_news"]:
        lines.append("\n**📰 HN 热议**")
        for h in raw_data["hacker_news"][:4]:
            lines.append(f"- [{h['title']}]({h['url']})  💬{h.get('comments',0)}")
    if raw_data["mcp_new_servers"]:
        lines.append("\n**🔌 新 MCP Server**")
        for m in raw_data["mcp_new_servers"][:3]:
            lines.append(f"- [{m['name']}]({m['url']})  ⭐{m['stars']}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
