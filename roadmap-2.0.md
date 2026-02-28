# Trend2Biz 产品路线图 2.0

> 更新日期：2026-02-28
> 基于实际开发进度重新校准，原 roadmap.md 中 v0.3/v0.4/v1.0 的核心能力已大幅超前完成。

---

## 当前状态：v0.6-beta ✅

### 已完成功能全景

#### 基础设施（原 v0.1 / v0.2）
- [x] 异步 Job 系统（202 + job_id + 指数退避重试）
- [x] GitHub REST API 真实指标采集（stars/forks/commits/contributors）
- [x] 结构化 JSON 日志 + cursor 分页
- [x] SQLite / PostgreSQL 双数据库支持
- [x] Alembic 迁移基础 + 启动自动建表

#### Web 仪表板（原计划外）
- [x] 全功能 Web 仪表板，替代原计划的 Rich TUI
- [x] 日期导航（前/后一天）+ 历史无数据智能跳转（查询最近有数据日期）
- [x] Daily / Weekly / Monthly 三时间窗口切换
- [x] 语言筛选器（客户端实时过滤）
- [x] 项目自动批量分析队列（加载后逐项分析）
- [x] 快照接口直接内嵌 project_id + biz + score，消除 200 项目分页限制

#### AI 中文描述系统（原 v1.0 内容提前完成）
- [x] 三家 LLM 提供商可切换：
  - Anthropic Claude Haiku
  - OpenRouter Deepseek-V3
  - 智谱 AI GLM-4-Flash
- [x] 读取 README 上下文生成 2-3 句准确中文描述
- [x] API Key 前端管理（localStorage 存储，按需传参，不落服务器 DB）
- [x] 无 Key 时自动降级为 MyMemory 免费翻译
- [x] 未配置 Key 点击分析时弹窗引导配置

#### 商业分析（rule-v1，原 v0.3 部分）
- [x] 基于规则的商业分类（5 类：agent / data / observability / security / developer-tools）
- [x] 多维评分（traction / team / market / tech / openness）+ Grade S/A/B/C
- [x] 商业关键词标签（category / monetization_candidates）可点击
- [x] 点击标签弹出"全库同标签项目列表"（含评级、描述、Star）

#### 设置与自动化（原 v0.4 前端部分）
- [x] 设置面板（⚙）：AI 提供商选择 + API Key + 每日自动采集开关
- [x] 每日自动采集：首次访问当天无数据时自动触发 GitHub Trending 抓取

#### 手机端（原计划外）
- [x] 响应式三级断点（900px 平板 / 640px 手机 / 400px 超小屏）
- [x] CSS Grid 卡片化表格（tr → 卡片，无需改 HTML）
- [x] 底部抽屉式 Modal（符合手机操作习惯）
- [x] 手机端自动展示语言标签和总 Star 数（隐藏列数据内联）

---

## 待完成路线图

### v0.7 — 分析质量跃升
> **核心目标**：让商业分析结果从"可参考"升级为"可直接使用"
> **优先级**：🔴 最高

#### 商业分类体系扩展
- [ ] `biz.py` 分类从 5 类扩展到 15 类
  - 新增：fintech / edu / ecommerce / devtools / low-code / infra / robotics / biotech / media / gaming
  - 精化 delivery_forms（OSS / SaaS / SDK / API / On-premise）
  - 精化 buyer（individual-dev / startup-cto / enterprise-buyer / researcher）

#### LLM 完整商业画像（llm-v1）
- [ ] 新增 `model: llm-v1` 分析路径，调用 LLM 生成**完整** biz profile（不只是 description_zh）
- [ ] LLM 输出结构化字段：category / scenarios / monetization / buyer / delivery_forms
- [ ] 新增 `bd_pitch` 字段：合作价值主张 + 对方痛点 + 可提供资源（3 句话 BD 话术）
- [ ] 置信度从固定 0.65 改为 LLM 实际输出值（0.0-1.0）
- [ ] rule-v1 保留作为无 Key 时的降级方案

#### 评分解释增强
- [ ] 每个评分维度附带触发信号（例："stars_delta=3200 → traction=8.5"）
- [ ] followups 根据 category + grade 动态生成专属追问清单（非通用模板）
- [ ] 同 project + 同 model + 同天 → score UPSERT（防止重复追加得分记录）

#### 一页报告生成（原 v0.3 遗留）
- [ ] `POST /reports:generate` 生成可分享 HTML 报告
- [ ] 报告内容：项目基本信息、评分雷达图（SVG）、亮点/风险、BD 话术、追问清单
- [ ] 前端"导出报告"按钮 → 新标签页打开

---

### v0.8 — 后台自动调度
> **核心目标**：系统无人值守，每天自动完成完整采集→分析管道
> **优先级**：🟠 高（运营必须）

#### APScheduler 集成（原 v0.4 全量）
- [ ] 集成 `APScheduler`（无需 Celery/Airflow/Redis）
- [ ] 计划任务配置：
  - `00:10 UTC`：trending_fetch daily/all
  - `00:20 UTC`：trending_fetch weekly/all + monthly/all
  - `01:00 UTC`：top 200 项目 metrics_refresh
  - `01:30 UTC`：未分析项目 biz+score batch（llm-v1 优先，降级 rule-v1）
- [ ] 幂等 key：同 date + since + language 的 fetch 任务不重复入队

#### Job 管理 UI
- [ ] 首页增加"后台任务"状态面板（可折叠）
- [ ] 显示：今日任务状态 / 失败任务数 / 下次调度时间
- [ ] `GET /api/v1/jobs?status=failed` + 前端失败任务列表
- [ ] `POST /api/v1/jobs/{id}:retry` + 前端一键重试按钮

---

### v0.9 — 历史趋势与洞察
> **核心目标**：回答"哪些赛道在爆发？哪些项目值得长期跟进？"
> **优先级**：🟡 中（产品差异化核心）

#### 爆发检测 API
- [ ] `GET /api/v1/analysis/burst?since=7d` — 7日入榜加速度 + 连续上榜天数
- [ ] 首页新增"本周爆发"Tab，按爆发指数排序
- [ ] 引入 `project_snapshots_count` 统计字段（项目历史入榜次数）

#### 赛道指数
- [ ] `GET /api/v1/analysis/sector?tag=agent&from=&to=` — 指定赛道热度时序
- [ ] 首页增加赛道概览面板（Top 5 赛道 + 热度变化）
- [ ] `tags` + `project_tags` 表从 biz_profile.category 自动写入

#### 项目时序详情
- [ ] 项目详情页（点击仓库名进入）
- [ ] 内容：stars 增长曲线 / 历次分析得分变化 / 首次入榜日期
- [ ] 前端路由：`/web/projects/{owner}/{repo}`

#### 关注列表（Watchlist）UI
- [ ] 后端接口已有，前端补完：添加/移除/备注
- [ ] Watchlist Tab：独立项目列表，支持批量分析
- [ ] 支持导出 CSV（project_id / repo / grade / category / bd_pitch）

---

### v1.0 — 可对外发布的完整版
> **核心目标**：完整产品体验，可持续运营，支持团队协作或对外展示
> **优先级**：🟢 中长期

#### 分享与协作
- [ ] 项目详情页独立永久 URL（`/web/projects/owner/repo`，可直接分享）
- [ ] 一键生成分享卡片（og:image，适合发到微信/X）
- [ ] 报告支持公开访问链接（含过期时间）

#### 通知推送
- [ ] Telegram Bot：每日推送新入库的 S/A 级项目摘要
- [ ] 邮件周报（可选）
- [ ] 设置面板增加通知配置（Bot Token + Chat ID）

#### 多语言 Trending
- [ ] 支持 `spoken` 参数（zh/ja/es 等）采集非英语圈项目
- [ ] 国际化项目的中文描述生成优化（双语提示词）

#### 性能与部署
- [ ] Redis 可选缓存（热门快照 + 频繁查询结果）
- [ ] Docker Compose 一键部署（app + db + redis）
- [ ] 完整 Alembic 迁移脚本（生产环境 PostgreSQL 升级路径）
- [ ] 基础访问统计（每日活跃 IP、分析次数）

---

## 版本优先级与节奏建议

```
v0.6-beta（当前）
    │
    ▼
 v0.7  分析质量   ←── 最高优先级：核心竞争力，影响所有后续价值
    │              主要工作：biz.py 扩展 + llm-v1 + bd_pitch + 报告
    ▼
 v0.8  自动调度   ←── 运营必须：无调度就要每天手动跑
    │              主要工作：APScheduler（简单）+ Job UI
    ▼
 v0.9  历史洞察   ←── 产品差异化：此功能是与其他工具的核心区别
    │              主要工作：时序模型 + 爆发检测 + 赛道指数
    ▼
 v1.0  完整发布   ←── 对外展示 / 团队使用 / 可持续运营
                   主要工作：分享 + 通知 + Docker 部署
```

---

## 关键技术决策记录

| 决策 | 选择 | 原因 |
|------|------|------|
| 调度方案 | APScheduler（进程内） | 无需额外基础设施，适合单机部署 |
| LLM 调用 | 用户自带 Key（前端传参） | 降低服务成本，Key 不落库保护隐私 |
| 翻译降级 | MyMemory 免费 API | 无 Key 时仍可使用，不阻断流程 |
| 前端框架 | 原生 JS + CSS | 零构建依赖，静态文件直接部署 |
| 移动适配 | 纯 CSS Grid 卡片化 | 不改 HTML/JS 结构，维护成本低 |
| 数据库 | SQLite（开发）/ PostgreSQL（生产） | 开发零配置，生产可扩展 |
