# Trend2Biz

GitHub Trending 热点发现 + 商业化评估平台。自动抓取每日热门开源项目，采集 GitHub 真实指标，生成商业分类、评分和追问清单，帮助 BD/投资人快速判断项目商业化潜力。

## 功能概览

- **Web 仪表板**：浏览器打开即用，按日期查看 Trending 榜单，含评分和商业分析
- **GitHub 指标采集**：stars / forks / commits(30d) / contributors(90d)（真实 REST API）
- **商业画像推断**：自动识别赛道（agent / observability / security / data / developer-tools）
- **YC 风格评分**：市场、牵引力、护城河、团队、变现路径、风险，0–10 分，评级 S/A/B/C
- **异步 Job 系统**：所有耗时操作后台执行，前端轮询进度
- **历史快照**：保存每日/周/月 Trending 记录，支持历史日期查看

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 API | FastAPI + SQLAlchemy |
| 数据库 | SQLite（默认）/ PostgreSQL |
| 数据采集 | httpx + BeautifulSoup（Trending）/ GitHub REST API（指标）|
| 前端 | 纯 HTML + CSS + Vanilla JS（无框架） |
| 工具脚本 | rich（TUI 仪表板）/ pytest（测试）|

## 快速启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置（可选，有 Token 限速 5000 req/h，无则 60 req/h）
cp .env.example .env
# 编辑 .env，填入 GITHUB_TOKEN=<your_token>

# 3. 启动服务器
python -m uvicorn app.main:app --port 8765

# 4. 打开 Web 仪表板
open http://localhost:8765/web/
```

## 使用方式

### Web 仪表板（推荐）

```
http://localhost:8765/web/
```

- 顶部日期导航栏：`← 前一天 / → 后一天 / 日期选择器 / daily-weekly-monthly`
- 今日无数据时，点击「⚡ 抓取今日 Trending」自动拉取并展示
- 未分析项目点击「⚡ 分析」按钮，实时显示进度，完成后行内刷新评分和商业分析

### 终端一键脚本

```bash
# 交互式：先看完整榜单，再选项目分析
python scripts/run_e2e_api.py

# 直接分析前 3 名
python scripts/run_e2e_api.py --top 3

# 周榜 + 过滤 Python 项目
python scripts/run_e2e_api.py --lang python --since weekly
```

### 终端 TUI 仪表板

```bash
python scripts/dashboard.py
```

### API 文档

```
http://localhost:8765/docs
```

## 核心 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/trending/snapshots:fetch` | 抓取 Trending（异步，返回 job_id）|
| GET  | `/api/v1/trending/snapshots` | 查询快照（支持 date / since 过滤）|
| GET  | `/api/v1/projects` | 项目列表（cursor 分页）|
| GET  | `/api/v1/projects/{id}` | 项目详情（含最新指标/画像/评分）|
| POST | `/api/v1/projects/{id}/metrics:refresh` | 刷新 GitHub 指标（异步）|
| POST | `/api/v1/projects/{id}/biz-profiles:generate` | 生成商业画像（异步）|
| GET  | `/api/v1/projects/{id}/scores` | 历史评分列表 |
| POST | `/api/v1/scores:batch` | 批量评分（异步）|
| POST | `/api/v1/reports:generate` | 生成 HTML 报告 |
| GET  | `/api/v1/jobs/{job_id}` | 查询 Job 状态 |
| POST | `/api/v1/watchlist` | 添加关注 |
| GET  | `/api/v1/watchlist` | 关注列表 |

## 版本路线图

| 版本 | 主题 | 状态 |
|------|------|------|
| v0.1 | 能跑能测 | ✅ 已完成 |
| v0.2 | 稳定可靠 | ✅ 已完成 |
| v0.3 | 分析增强 | 🔲 待开发 |
| v0.4 | 自动调度 | 🔲 待开发 |
| v0.5 | 历史分析 | 🔲 待开发 |
| v1.0 | LLM 分析 | 🔲 待开发 |

详见 [ROADMAP.md](ROADMAP.md)

## 说明

- Trending 抓取无官方 API，采用页面抓取 + HTML 解析。
- 商业画像和评分当前是规则引擎版本（v1.0 计划接入 LLM）。
- 数据库会在服务启动时自动建表，无需手动初始化。
- `.env` 文件已加入 `.gitignore`，Token 不会被提交。
