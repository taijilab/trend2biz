# Trend2Biz：GitHub 热点开源项目自动发现与商业化评估系统

下面是把“自动抓取 GitHub Trending 热点项目 + 自动商业化分析 + 长期历史数据沉淀与趋势分析”写成一份**产品规格书（PRD）**的版本，按你们做生态/评审/孵化器的使用场景来设计。

> 关键事实：GitHub 目前**没有公开的 Trending 官方 API**，主流做法是抓取 Trending 页面并解析 HTML。 ([Stack Overflow](https://stackoverflow.com/questions/30525330/how-to-get-list-of-trending-github-repositories-by-github-api?utm_source=chatgpt.com))
> 
> 
> Trending 页面可按 `since`（daily/weekly/monthly）等维度过滤，社区已有成熟实现与参数约定可参考。 ([GitHub](https://github.com/hedyhli/gtrending?utm_source=chatgpt.com))
> 

---

# 产品规格书（PRD）

## 产品名

**Trend2Biz：GitHub 热点开源项目自动发现与商业化评估系统**

## 版本

V0.1（MVP） / V1.0（规模化）

## 背景与目标

### 背景

- GitHub Trending 能快速反映开发者注意力与新项目爆发点，但缺乏官方 API，且热点“转瞬即逝”。([Stack Overflow](https://stackoverflow.com/questions/30525330/how-to-get-list-of-trending-github-repositories-by-github-api?utm_source=chatgpt.com))
- 你们需要：**自动捕获热点 → 自动商业化研判 → 形成历史数据库 → 支持长期趋势研究与专家评审提效**。

### 产品目标（MVP）

1. 每天/每小时自动抓取 GitHub Trending（可选语言/周期）
2. 对热点项目生成**商业化画像**与**YC式评估分**（可解释）
3. 将项目“热度 + 关键指标 + 评估结论”入库，形成**可回溯历史**
4. 提供后台查询：热点榜单、项目详情、趋势曲线、行业/赛道聚类

---

# 目标用户与使用场景

## 用户角色

1. **开源投资/孵化评审专家**：看“最值得跟进的项目清单 + 追问点”
2. **生态运营/BD**：发现可合作项目（企业版、SaaS、生态集成）
3. **研究分析团队**：做“赛道长期趋势、爆发规律、信号领先指标”

## 典型场景

- “今天 Trending 里，哪些项目具备商业化潜力？为什么？”
- “过去 6 个月，AI Agent / 数据分析 / 观测性方向的爆发节奏如何？”
- “这个项目是昙花一现还是持续增长？贡献者结构健康吗？”
- “生成专家一页纸：亮点、风险、追问清单、竞品对照”

---

# 核心功能需求（Functional Requirements）

## 1) Trending 抓取与标准化

### 1.1 抓取范围

- Trending Repos：按
    - 时间窗口：daily / weekly / monthly（对应 `since`）([GitHub](https://github.com/hedyhli/gtrending?utm_source=chatgpt.com))
    - 语言：All / 指定语言（如 python / rust / …）([GitHub](https://github.com/hedyhli/gtrending?utm_source=chatgpt.com))
        - （可选）spoken language code 过滤（如 en / zh）([GitHub](https://github.com/hedyhli/gtrending?utm_source=chatgpt.com))

> 注：由于缺少官方 API，抓取方式为：请求 Trending 页面 → HTML 解析 → 结构化字段。([Stack Overflow](https://stackoverflow.com/questions/30525330/how-to-get-list-of-trending-github-repositories-by-github-api?utm_source=chatgpt.com))
> 

### 1.2 输出字段（trending_snapshot）

- 抓取时间（ts）
- 榜单维度：since / language / spoken_lang
- 排名 rank
- repo_full_name（owner/name）
- repo_url
- description
- primary_language
- stars_total / forks_total（页面有时展示增量 star，也可解析）
- stars_delta_window（若页面展示 “stars today/this week/this month”，则解析为增量）
- 主题标签（若能解析到 topics 则加；MVP 可后置）

### 1.3 去重策略

- 同一抓取维度 + 日期 + rank + repo_full_name 作为唯一键
- 允许同一 repo 在不同维度重复出现（用于多维热度分析）

---

## 2) 项目深度采集（Repo Enrichment）

对 Trending 命中项目，调用 GitHub API（REST/GraphQL）补齐可量化指标（不依赖 Trending API）。

**关键字段（repo_metrics_daily）**

- stars, forks, open_issues
- watchers/subscribers
- default_branch
- license
- created_at, updated_at, pushed_at
- releases_count（近 180 天）
- commits_30d / commits_90d
- prs_30d / issues_30d
- contributors_90d（活跃贡献者）
- bus_factor_proxy（Top-N commit share）
- issue_first_response_median（可选，V1.0）

---

## 3) 商业化自动分析（Biz Lens）

目标：生成可解释的“商业化画像”与“YC式结论”。

### 3.1 商业化画像字段（biz_profile）

- **用户是谁**：Dev / Data / Sec / Infra / End-user / 企业IT
- **场景是什么**：研发效率/推理部署/数据分析/观测/安全/电商运营/金融量化/教育内容生产等
- **价值点**：降本/增收/合规/效率/体验
- **交付形态**：库/CLI/Server/SaaS/API/插件/agent skill
- **商业模式候选**：
    - Open-core 企业版
    - SaaS 托管
    - API 计费
    - Marketplace 抽成（插件/模型/agent）
    - 商业许可/双许可
    - 私有化交付与服务
- **购买方**：研发负责人/数据负责人/安全负责人/业务负责人
- **销售路径**：PLG / 社区转化 / 渠道 / 生态绑定（云、芯片、IDE、平台）

### 3.2 自动化推断方法（MVP）

- 基于 README / docs / topics / tagline 的信息抽取与分类（LLM + 规则）
- 基于依赖与文件特征：Dockerfile、helm、k8s、terraform、sdk、api spec
- 基于语言/领域词典（Agent、RAG、OLAP、ETL、SIEM、observability…）

### 3.3 YC式评分模型（可解释）

维度建议（与你之前模板一致）：

- Market（25%）
- Traction（25%）
- Moat（15%）
- Team proxy（10%：用组织信息/维护者活跃度/公司背书作为弱代理）
- Monetization readiness（20%：从交付形态+企业适配信号推断）
- Risk（5%）

输出：

- total_score（0-10）
- grade：S/A/B/C
- top_highlights（最多3条）
- top_risks（最多3条）
- expert_followups（自动追问清单）

> 说明：Team 和真实收入无法可靠从 GitHub 获得，MVP 以“可观察信号”做 proxy，后续引入“创始人提交表单 + 证据上传”。
> 

---

## 4) 历史数据沉淀与长期分析

### 4.1 必须存历史的原因

- Trending 本质是“注意力信号”，价值在于**时间序列**：爆发 → 回落 → 二次增长 → 商业化落地
- 你们要回答“哪些赛道在未来 3-6 个月更可能出独角兽”

### 4.2 历史存储设计（核心表）

1. `trending_snapshots`（榜单快照）
2. `repo_metrics_daily`（每日仓位数据：stars/forks/commits/active_contrib…）
3. `biz_profile_versions`（商业画像版本：模型升级可回溯）
4. `score_versions`（评分版本：权重变更可对照）
5. `tags_taxonomy`（赛道体系：Agent/数据/电商/金融/教育…）

### 4.3 长期分析能力（V1.0）

- 项目生命周期曲线：热度 vs 增长（stars_delta vs commits_30d）
- 赛道热度指数：某赛道入榜次数、平均排名、平均增长
- “领先指标”挖掘：如贡献者扩散速度、issue 响应速度改善等
- 爆发预测：连续 N 天入榜 + 增长加速 → 进入观察池

---

# 后台与前台（Admin + Dashboard）

## 1) 热点榜单页

- 维度筛选：daily/weekly/monthly、语言、日期
- 排序：rank / stars_delta / total_score
- 快速标签：赛道、商业模式候选、风险标记

## 2) 项目详情页（一页纸）

- Repo 基本信息
- 时间序列（stars_total、stars_delta、commits、contributors）
- 商业化画像（Who/What/How to charge）
- 评分解释（每项得分来源）
- 自动追问清单（给专家/BD用）
- “加入观察池 / 标记跟进 / 导出报告”

## 3) 趋势分析页（研究视角）

- 赛道指数曲线
- Top 项目对比
- 新项目爆发榜（首次入榜 + 增速）

---

# 非功能需求（Non-Functional Requirements）

- **合规与礼貌抓取**：遵守 GitHub 的访问规则、合理频率、缓存与退避；Trending 无官方 API，抓取需做好限速与失败重试。([Stack Overflow](https://stackoverflow.com/questions/30525330/how-to-get-list-of-trending-github-repositories-by-github-api?utm_source=chatgpt.com))
- 稳定性：抓取失败不影响全局；队列化重试
- 可扩展：后续接入 PyPI/NPM/DockerHub/OpenSSF
- 可解释性：评分必须可解释、可追溯版本
- 安全：Token 密钥管理、权限最小化

---

# 系统架构（建议）

## MVP 架构

- Scheduler（cron / Airflow / Temporal 可选）
- Trending Fetcher（HTML 抓取 + 解析）
- Enricher（GitHub API 补齐 metrics）
- Analyzer（LLM/规则生成商业画像 + 评分）
- Storage（Postgres/ClickHouse：若做长期趋势建议 ClickHouse）
- Dashboard（Next.js + Admin）

## 抓取策略

- 每日：抓 daily/weekly/monthly 各一次（All languages）
- 每小时（可选）：抓 daily（All languages）
- 对 top N 项目进行 metrics 补齐（如 top 200）

---

# MVP 里程碑与验收标准

## MVP（2~4周）

- ✅ 自动抓取 Trending（daily/weekly/monthly + 可选语言）
- ✅ 入库并可回放历史榜单
- ✅ Repo metrics 每日补齐（stars/forks/commits_30d/active_contrib…）
- ✅ 商业化画像（基础分类 + 商业模式建议）
- ✅ 评分 + S/A/B/C 分级 + 追问清单
- ✅ 后台：榜单页 + 项目详情页 + 搜索

验收指标：

- 抓取成功率 ≥ 99%（按天）
- 热点项目入库延迟 ≤ 30 分钟（每日任务）
- 每个项目报告生成 ≤ 60 秒（异步队列）

---

# 风险与对策

1. **Trending 无官方 API、页面结构变化** → 解析器版本化 + 监控报警 + 回归测试集 ([Stack Overflow](https://stackoverflow.com/questions/30525330/how-to-get-list-of-trending-github-repositories-by-github-api?utm_source=chatgpt.com))
2. **误判商业化** → “模型推断 + 人工标注纠偏”闭环；引入创始人问卷提高置信度
3. **数据规模增长** → 指标分层：热榜全量、长尾抽样；OLAP 存储用于趋势

---

# 附：对接你们生态的“升级点”（强相关）

- 把输出直接变成“孵化/投资/合作线索”：
    - 自动识别：可做企业版的项目、可做 SaaS 托管的项目、适合进入 Agent/插件市场的项目
- 生成“BD 话术”：合作价值、对方痛点、可提供资源（算力/渠道/企业客户/社区曝光）
- 与你们的 Agent 平台结合：把每个热点项目变成“可调用能力/skill”候选库

---

如果你希望我把这份 PRD 进一步落地成“可交付研发”的版本，我可以直接补齐两块（不需要你再提供信息，我会按默认最佳实践写）：

1. **接口规格（API Contract）**：/trending、/projects、/scores、/reports、/watchlist
2. **数据库 DDL（Postgres + 可选 ClickHouse）**：含索引、分区、去重键、版本表设计