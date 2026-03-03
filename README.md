# Trend2Biz · v1.0.0

GitHub Trending 热点发现 + 商业化评估平台。自动抓取每日热门开源项目，采集真实 GitHub 指标，通过 LLM + 规则引擎生成 YC 风格商业评分和完整投资分析报告，帮助 BD / 投资人快速判断项目商业化潜力。

---

## 功能概览

| 功能 | 说明 |
|------|------|
| **每日 Trending 抓取** | 自动拉取 GitHub Trending 日榜/周榜/月榜，支持语言过滤 |
| **真实 GitHub 指标** | Stars · Forks · Open Issues · Commits(30d) · Contributors(90d) · Bus Factor |
| **LLM 商业画像** | AI 自动生成赛道分类、使用场景、价值主张、变现路径、BD 话术 |
| **AI 动态竞品分析** | LLM 输出项目专属竞品列表（最多 5 条），覆盖 OSS / SaaS / OSS+SaaS |
| **AI 动态风险评估** | LLM 生成项目专属风险条目（高/中/低），含 License / 技术 / 竞争 / 商业化 |
| **7 维 YC 评分** | 牵引力 · 市场 · Hype · 产品技术 · 团队 · 商业模式 · 风险，加权综合得分 0–100 |
| **License 友好度** | MIT/Apache → 商业友好 ✅，AGPL → 强传染性 ⚠️⚠️，GPL → 传染性 ⚠️，BUSL → 商业受限 ❌ |
| **Hype Score** | 基于 Star 增长加速度自动计算热度评分 |
| **组织公司背景** | Organization 类项目自动抓取官网 / 融资 / 新闻，补全商业背景章节 |
| **完整 HTML 报告** | 一键生成可分享的投资分析报告，含估值建议、追问清单、数据验证表 |
| **高级搜索过滤** | 关键词 + 评级 + 赛道 + 最低 Stars 多维筛选 |
| **移动端适配** | 仪表板和报告页均支持手机浏览 |
| **异步 Job 系统** | 所有耗时操作后台执行，前端实时轮询进度 |
| **历史快照** | 保存每日 / 周 / 月 Trending 记录，支持回溯历史日期 |

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 API | FastAPI + SQLAlchemy |
| 数据库 | SQLite（默认）/ PostgreSQL |
| LLM | OpenRouter (DeepSeek) / 智谱 GLM / Anthropic Claude（可选） |
| 数据采集 | httpx + BeautifulSoup（Trending 页面）/ GitHub REST API（指标） |
| 公司研究 | GitHub Orgs API + 官网抓取 + DuckDuckGo 新闻搜索 |
| 前端 | 纯 HTML + CSS + Vanilla JS（无框架） |

---

## 快速启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置（可选）
cp .env.example .env
# 编辑 .env：
#   GITHUB_TOKEN=<your_token>      # 限速 5000 req/h，无则 60 req/h
#   OPENROUTER_API_KEY=<key>       # LLM 商业画像（DeepSeek 免费额度）
#   ZHIPU_API_KEY=<key>            # 备选 LLM（GLM-4-Flash）

# 3. 启动服务器
uvicorn app.main:app --port 8765

# 4. 打开 Web 仪表板
open http://localhost:8765/web/
```

---

## Web 仪表板使用

```
http://localhost:8765/web/
```

1. **抓取数据**：今日无数据时点击「⚡ 抓取今日 Trending」
2. **分析项目**：未分析项目点击「⚡ 分析」，后台生成商业画像 + 评分
3. **生成报告**：点击「📊 报告」在新标签页打开完整投资分析报告
4. **高级筛选**：点击搜索框旁「高级 ▾」展开评级 / 赛道 / Stars 过滤器
5. **重新分析**：已分析项目可点击「↺」重新触发 LLM 分析（更新竞品和风险）

---

## 商业报告评估要点

每份报告包含以下 8 个章节，核心评估维度如下：

### 1. 项目概况

| 字段 | 说明 |
|------|------|
| 账号类型 | Organization（有商业背景）vs User（个人项目）|
| 主维护者集中度 | Top-1 贡献者占比，>50% 标记为单点风险 |
| License | 自动添加友好度标注（商业友好 / 传染性 / 商业受限） |
| 商业赛道 | LLM 从 15 个赛道中分类 |
| 变现形式 | LLM 推断的变现路径（SaaS / API / 企业版等）|

### 2. 公司 / 组织背景（Organization 专属）

- 融资轮次与估值（自动从新闻提取）
- 官网及商业落地情况（定价页抓取）
- 商业版图与竞争格局
- 战略动态（近期新闻）
- 公司 vs 开源项目风险对比

### 3. Star 增长曲线

- 月度 Stars / 增量 / 环比增速折线图
- 评估增长是否持续、是否有爆发性事件

### 4. YC 7 维评分卡

| 维度 | 英文 | 权重 | 评分来源 |
|------|------|------|---------|
| 牵引力与增长 | Traction & Growth | 18% | Stars + Commits(30d) |
| 问题与市场 | Problem & Market | 18% | 赛道市场基准分 |
| 媒体与热度 | Hype & Media | 12% | Star 增长加速度 |
| 产品与技术 | Product & Technology | 13% | Contributors(90d) |
| 团队与社区 | Team & Community | 14% | Contributors + Bus Factor 惩罚 |
| 商业模式 | Business Model | 13% | 变现路径层级 + ARR 加成 |
| 风险（反转） | Risk (inverted) | 12% | 贡献者数 + License + Bus Factor |

**综合评级**：S（≥85）· A（≥70）· B（≥55）· C（<55）

**投资建议阈值**：
- ≥80 分 → 强烈推荐跟进 🚀
- ≥65 分 → 值得持续观察 👀
- ≥50 分 → 等待更多信号 ⏳
- <50 分 → 商业潜力有限 ❌

### 5. 竞品分析

- 优先使用 LLM 动态生成的项目专属竞品（3–5 条）
- LLM 未覆盖时 fallback 到赛道静态竞品库（13 个主要赛道）
- 每条竞品含：类型（OSS / SaaS / OSS+SaaS）· 市场定位 · 差异化要点

### 6. 风险评估

- 优先使用 LLM 生成的项目专属风险（3–5 条，含等级 high/medium/low）
- Fallback 使用动态静态风险：大厂竞争 · License（按 SPDX 自动分级）· 维护者单点 · 商业化转化 · 技术过时

### 7. 投资 / 合作建议

- 介入时机判断（基于综合得分）
- 个性化追问清单（基于赛道 + 评级生成，6 条）
- BD 话术（LLM 生成：价值主张 · 痛点 · 可提供资源）

### 8. 数据验证表

- 列出报告中所有数据来源（GitHub API · LLM · 自动抓取 · 待调研）
- 标注哪些字段为「⚠️ 待调研」，方便后续人工补充

---

## 评分公式详解

```
综合得分 = Market×0.18 + Traction×0.18 + Hype×0.12
         + Moat×0.13  + Team×0.14   + Monetization×0.13 + Risk_inv×0.12
         × 10  → 0–100 分

Traction  = min(10, 4.0 + stars/10000 + commits_30d/100)
Hype      = min(10, 5.0 + 增长加速度×20 + stars/20000)  [或 4.5+stars/20000 作 fallback]
Moat      = min(10, 4.5 + contributors_90d/20)
Team      = min(10, 4.0 + contributors_90d/30) − bus_factor惩罚
            [bus_factor>0.7: −1.0; >0.5: −0.5]

Monetization（4档）:
  无变现线索       → 5.0
  有候选（非主流） → 6.5
  SaaS/API/企业版  → 7.5
  有 ARR 数据      → +1.5 加成（上限 10）

Risk（License + bus_factor 调整）:
  基础分: contributors_90d≥5 → 7.0; <5 → 5.5
  AGPL/GPL     → −0.5   MIT/Apache → +0.3
  BUSL/SSPL    → −1.0   bus_factor>0.8 → −1.0; >0.5 → −0.5
```

---

## 核心 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/trending/snapshots:fetch` | 抓取 Trending（异步，返回 job_id）|
| GET  | `/api/v1/trending/snapshots` | 快照列表（date / since 过滤）|
| GET  | `/api/v1/projects` | 项目列表（cursor 分页）|
| GET  | `/api/v1/projects/{id}` | 项目详情（含指标 / 画像 / 评分）|
| POST | `/api/v1/projects/{id}/metrics:refresh` | 刷新 GitHub 指标（异步）|
| POST | `/api/v1/projects/{id}/biz-profiles:generate` | 生成 LLM 商业画像（异步）|
| POST | `/api/v1/projects/{id}/research-company` | 触发公司背景调研（Organization）|
| GET  | `/api/v1/projects/{id}/scores` | 历史评分列表 |
| GET  | `/api/v1/projects/search` | 搜索（q / grade / category / min_stars）|
| POST | `/api/v1/scores:batch` | 批量评分（异步）|
| POST | `/api/v1/reports:generate` | 生成 HTML 报告 |
| GET  | `/api/v1/jobs/{job_id}` | 查询 Job 状态 |
| POST | `/api/v1/watchlist` | 添加关注 |
| GET  | `/api/v1/watchlist` | 关注列表 |

---

## 版本历史

| 版本 | 主题 | 状态 |
|------|------|------|
| v0.1 | 基础框架 + 数据采集 | ✅ |
| v0.2 | 稳定可靠 + 规则评分 | ✅ |
| v0.9 | 报告增强 + 团队共享 + 移动端 | ✅ |
| **v1.0** | **LLM 竞品 / 风险 / 评分改进 / 高级搜索** | ✅ |
| v2.0 | 订阅推送 / 多用户 / SaaS 化 | 🔲 规划中 |

### v1.0.0 变更日志

**P0 — 报告质量**
- LLM 画像新增 `competitors`（3–5 条动态竞品）和 `project_risks`（3–5 条专属风险）
- 竞品 / 风险章节优先使用 LLM 数据，回退到静态规则库
- License 友好度标注：MIT/Apache ✅ · AGPL ⚠️⚠️ · GPL ⚠️ · BUSL ❌
- LLM max_tokens 800 → 1500

**P1 — 评分升级**
- 新增 Hype Score（基于 Star 增长加速度）
- Monetization 评分从二值改为 4 档，支持 ARR 加成
- Risk / Team 评分加入 License 惩罚 + Bus Factor 惩罚

**P2 — 搜索**
- `/api/v1/projects/search` 新增 `grade` / `category` / `min_stars` 参数
- 前端「高级 ▾」折叠筛选面板

**P3 — 其他**
- 版本号 0.9.0 → 1.0.0
- 公司背景：Organization 项目自动触发 GitHub Orgs API + 官网 + 新闻抓取

---

## 说明

- Trending 抓取无官方 API，采用页面抓取 + HTML 解析，偶有 GitHub 页面结构变化。
- LLM 商业画像需配置 API Key（支持 OpenRouter / 智谱 / Anthropic），规则模式不需要。
- 数据库在服务启动时自动建表，无需手动初始化。
- `.env` 已加入 `.gitignore`，Token 不会被提交。
