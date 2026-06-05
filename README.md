# AI 情报日报 → Lark 推送

每天自动抓取 GitHub Trending、OSSInsight、Hacker News、MCP 生态新动态，
用 Kimi 总结后推送到飞书置顶群。

**Python 版本要求**：>= 3.8

---

## 安装

```bash
# 克隆仓库
git clone https://github.com/hujie888/code_treding.git
cd code_treding

# 安装依赖（二选一）
pip install -r requirements.txt
# 或
pip install httpx python-dotenv
```

---

## 快速上手

### 1. 配置环境变量

```bash
cp .env.example .env   # 若无 .env.example，手动创建
```

`.env` 内容：

```
KIMI_API_KEY=sk-...          # Kimi API Key（https://platform.moonshot.cn）
LARK_WEBHOOK=https://open.larksuite.com/open-apis/bot/v2/hook/xxxxx
GITHUB_TOKEN=ghp_...         # 可选，有则 GitHub API 速率限制从 60→5000 次/小时
```

### 2. 获取飞书 Webhook

飞书群 → 右上角 `···` → 设置 → 机器人 → 添加机器人 → 自定义机器人  
→ 复制 Webhook 地址 → 粘贴到 `.env`

### 3. 本地运行

```bash
python ai_digest_lark.py
```

---

## 部署方式

### 方式一：GitHub Actions（推荐，免费）

1. Fork/推送本仓库到你的 GitHub
2. 进入仓库 **Settings → Secrets and variables → Actions**，添加：
   - `KIMI_API_KEY`
   - `LARK_WEBHOOK`
3. 将 `ai_digest.yml` 放到 `.github/workflows/` 目录

```
你的 repo/
├── ai_digest_lark.py
├── requirements.txt
└── .github/
    └── workflows/
        └── ai_digest.yml
```

每天工作日 09:00（北京时间）自动运行，也可在 Actions 页面点 **Run workflow** 手动触发。

---

### 方式二：Linux 服务器（systemd + cron）

#### 方法 A：crontab 定时任务

```bash
# 安装依赖
pip3 install -r requirements.txt

# 编辑 crontab（北京时间 09:00 = UTC 01:00）
crontab -e
```

添加一行：

```
0 1 * * 1-5 cd /path/to/code_treding && python3 ai_digest_lark.py >> /var/log/ai_digest.log 2>&1
```

#### 方法 B：systemd timer（更可靠，支持失败重启）

创建服务文件 `/etc/systemd/system/ai-digest.service`：

```ini
[Unit]
Description=AI 情报日报推送

[Service]
Type=oneshot
WorkingDirectory=/path/to/code_treding
ExecStart=/usr/bin/python3 ai_digest_lark.py
EnvironmentFile=/path/to/code_treding/.env
StandardOutput=journal
StandardError=journal
```

创建定时器文件 `/etc/systemd/system/ai-digest.timer`：

```ini
[Unit]
Description=每工作日 09:00（北京时间）运行 AI 情报日报

[Timer]
OnCalendar=Mon-Fri 01:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-digest.timer

# 查看状态
sudo systemctl status ai-digest.timer
journalctl -u ai-digest.service -f
```

---

## 数据源

| 数据源 | 内容 | 说明 |
|--------|------|------|
| GitHub Trending API | 今日涨星最多的 AI 仓库 | 非官方，稳定可用 |
| OSSInsight | AI 仓库 28 天增长排行 | 官方 API，无需 Token |
| Hacker News | AI 相关热议话题 | 官方 Firebase API |
| GitHub Search | 活跃 MCP Server / model-context-protocol 生态 | 有 GITHUB_TOKEN 速率更高 |
| 定向监控 | Cline、OpenHands 等重点项目 Release | 自定义列表 |

---

## 定制

| 需求 | 修改位置 |
|------|---------|
| 修改监控仓库 | `ai_digest_lark.py` 中 `WATCHED_REPOS` 列表 |
| 修改 AI 关键词 | `ai_keywords` 集合（共两处）|
| 修改推送时间 | `ai_digest.yml` 中 cron 表达式 |
| 修改卡片颜色 | `send_lark_card` 中 `"template"` 字段（blue/green/orange/purple）|
| 修改 Kimi 模型 | `summarize_with_kimi` 中 `model` 参数 |
