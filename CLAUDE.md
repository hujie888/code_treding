# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

单文件 Python 脚本，每个工作日自动抓取 AI 领域动态，用 Claude API 总结后推送到飞书（Lark）群。通过 GitHub Actions 定时运行（北京时间 09:00）。

## 运行方式

```bash
# 安装依赖
pip install httpx python-dotenv

# 创建 .env（本地测试用）
KIMI_API_KEY=sk-...
LARK_WEBHOOK=https://open.larksuite.com/open-apis/bot/v2/hook/xxxxx

# 运行
python ai_digest_lark.py
```

`GITHUB_TOKEN` 为可选变量，有则大幅提升 GitHub API 速率限制（GitHub Actions 中自动注入）。

## 代码架构

`ai_digest_lark.py` 分四层，顺序执行：

**1. 数据抓取层** — 5 个 fetch 函数，各自独立、带异常降级：
- `fetch_github_trending()` → 非官方 trending API，失败时 fallback 到爬 HTML
- `fetch_ossinsight_trending()` → OSSInsight AI collection 28 天增长排行
- `fetch_hacker_news_ai()` → HN Firebase API，关键词过滤 AI 相关 top 条目
- `fetch_mcp_new_servers()` → GitHub Search API，近 7 天新 MCP Server topic 仓库
- `fetch_watched_repos()` → 定向监控 `WATCHED_REPOS` 列表的最新 Release（只取昨天及今天）

**2. Kimi 总结层** — `summarize_with_kimi()` 直接调用 Kimi Chat Completions API（`moonshot-v1-32k`），失败时降级到 `_fallback_summary()` 手动拼接。

**3. Lark 推送层** — `send_lark_card()` 发飞书互动卡片（schema 2.0），失败时降级到 `send_lark_text_fallback()` 纯文本。

**4. 主流程** — `main()` 串联上述步骤，任何数据源为空时继续（全空才跳过推送）。

## 常见定制点

- **修改监控仓库**：`WATCHED_REPOS` 列表（`fetch_watched_repos` 函数内）
- **修改 AI 关键词过滤**：`ai_keywords` 集合（`fetch_github_trending` 和 `fetch_hacker_news_ai` 各有一份）
- **修改推送时间**：`ai_digest.yml` 中的 cron 表达式（当前 `0 1 * * 1-5` = 工作日 UTC 01:00 = 北京 09:00）
- **修改卡片颜色**：`send_lark_card` 中 `"template"` 字段（可选 blue/green/red/orange/purple）
- **修改 Kimi 模型或摘要格式**：`summarize_with_kimi` 函数内的 `model` 参数和 prompt（可选 `moonshot-v1-8k` / `moonshot-v1-32k` / `moonshot-v1-128k`）
