---
name: yc-oss-investment-analysis
description: "Generate YC-style OSS investment reports with structured scoring, maintainer concentration analysis, organization due diligence, strategic-news normalization/translation, and source-attributed funding history."
version: "v4"
updated_at: "2026-03-03"
---

# YC OSS Investment Analysis Skill (v4)

## 1) 目标
输出可执行的开源项目投资分析报告（HTML + Markdown），并保证：
- 指标可信（GitHub API + 快照兜底）
- 组织背景可追溯（融资表含来源）
- 战略动态可读（格式规范 + 中文化）
- 团队风险可解释（主维护者专项）

## 2) 触发场景
当用户要求：
- 「开源项目投资分析 / YC 视角分析 / 商业分析报告」
- 「生成报告并评估市场、团队、风险、商业化」
- 提供 GitHub 仓库并要求可投性判断

## 3) 标准流程
1. 拉取仓库基础指标：stars / forks / issues / commits / contributors。
2. 生成 biz + score（rule-v1 或 llm-v1）。
3. 若 owner 为 Organization，执行公司背景补全（官网、融资、战略动态、收入信号）。
4. 生成报告（HTML + Markdown）并写入 reports。
5. 输出数据校验状态（哪些字段是精准抓取，哪些待补充）。

## 4) v4 强制能力（本次改动提炼）

### A. 主维护者专项（Maintainer Concentration）
必须从 `stats/contributors` 提取近 90 天贡献分布，输出：
- top 5 维护者（login / 90d commits / share）
- top1 贡献占比（bus factor）
- 专项结论：
  - `>=50%`：单点风险高，建议扩核心梯队
  - `<50%`：贡献分散，团队韧性较好

同时要求：
- 将 `bus_factor_top1_share` 写入日指标（RepoMetricDaily）
- 将 `maintainers_90d` 缓存到 `biz.explanations`
- 报告新增独立章节「主维护者专项」

### B. 战略动态格式清洗 + 中文化
对原始新闻块（常见格式：`**[title](url)** — snippet`）必须做：
- 解析为结构化条目（title / url / snippet）
- HTML 渲染为条目列表（不要原样输出 markdown 符号）
- 最佳努力中文翻译（title/snippet）
- Markdown 导出保持同样条目结构

### C. 融资历史必须注明来源
融资表必须包含列：
- 轮次 / 金额 / 时间 / 投资方 / 来源

来源要求：
- 每一行融资记录独立来源链接
- 无来源时明确显示 `—`
- HTML 与 Markdown 两个版本一致

### D. 本地分析性能优化
交互式分析链路中：
- `metrics_refresh` 仅做快速必需指标
- 重型 star history 回填放到异步 `metrics_backfill`
- 遇到 GitHub rate limit：优先复用最新指标；否则用 trending 快照 hint 兜底

## 5) 报告章节（最小集合）
1. 投资建议（总分、等级、介入时机）
2. 项目概况
3. 公司/组织背景（若适用）
4. Star 趋势
5. 牵引力 / 市场 / 产品技术 / 商业模式 / 团队社区
6. 主维护者专项（新增）
7. 竞品分析
8. 风险评估
9. YC 综合评分
10. 建议行动
11. 数据校验状态

## 6) 质量红线
- 不允许把原始 `**[...](...)**` 直接塞进报告正文。
- 不允许融资表无来源列。
- 不允许仅给“贡献者数量”而没有主维护者占比分析。
- 不允许因 GitHub 限流导致大面积分析失败（需兜底）。

## 7) 验收清单
- HTML 报告包含「主维护者专项」表格与专项说明。
- Markdown 导出包含相同章节与行数据。
- 战略动态为中文可读列表，不含原始 markdown 粗体/链接噪声。
- 融资历史显示来源链接。
- Job 面板可见 `metrics_backfill`（后台补全任务）。
