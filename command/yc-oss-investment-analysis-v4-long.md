---
name: yc-oss-investment-analysis
description: "Use this skill to produce YC-style OSS investment reports with quantitative scoring, maintainer concentration diagnostics, organization-level due diligence, normalized strategic-news rendering, and source-attributed funding history."
version: "v4-long"
updated_at: "2026-03-03"
---

# YC 视角开源技术项目投资分析 Skill（v4-long）

## 概述
本 Skill 用于输出“可投研、可追溯、可复核”的开源项目投资分析报告（HTML + Markdown），核心目标：
- 评分结构化：七维打分 + 风险拆解 + 行动建议
- 数据可追溯：融资记录逐条来源链接
- 表达可读：战略动态自动清洗并中文化
- 风险可解释：主维护者专项（贡献占比 + 单点风险结论）
- 运行可用：本地限流场景下仍能稳定产出

---

## 一、触发条件
出现以下需求时启用本 Skill：
- “给这个 GitHub 项目做投资分析 / 商业分析 / YC 风格报告”
- “判断该开源项目是否值得投 / 值得持续观察”
- “输出结构化投研报告（市场、团队、护城河、商业化、风险）”
- 用户提供 GitHub 仓库链接并要求商业潜力评估

---

## 二、报告输出标准
必须产出：
1. HTML 报告（可浏览、可打印）
2. Markdown 报告（可复用、可版本管理）

最小章节：
1. 投资建议（总分、等级、介入时机）
2. 项目概况
3. 公司/组织背景（如适用）
4. Star 增长趋势
5. 牵引力 / 问题与市场 / 产品与技术 / 商业模式 / 团队与社区
6. 主维护者专项（v4 新增）
7. 竞品分析
8. 风险评估
9. YC 综合评分
10. 建议行动清单
11. 数据校验状态

---

## 三、v4 核心升级（必须执行）

### 1) 主维护者专项（Maintainer Concentration）
必须抓取并展示：
- 近 90 天 top 维护者（login、commits_90d、share_90d）
- top1 贡献占比（bus factor）
- 专项说明：
  - `top1 >= 50%`：单点风险高，建议扩核心梯队
  - `top1 < 50%`：贡献较分散，团队韧性较好

实现要求：
- 从 GitHub `stats/contributors` 计算 `bus_factor_top1_share`
- 将结果写入 `repo_metrics_daily.bus_factor_top1_share`
- 将维护者列表缓存到 `biz.explanations.maintainers_90d`
- HTML 与 Markdown 都必须有“主维护者专项”章节

### 2) 战略动态清洗 + 翻译
对原始新闻块（常见：`**[title](url)** — snippet`）必须：
- 先解析为结构化 item：`title/url/snippet`
- 再渲染为列表（禁止原样输出 markdown 噪声）
- 进行最佳努力中文翻译（title + snippet）
- HTML 与 Markdown 保持一致

### 3) 融资历史来源化
融资表强制列：
- 轮次 | 金额 | 时间 | 投资方 | 来源

规则：
- 每条融资记录必须尽量带来源链接
- 无来源显示 `—`
- HTML/Markdown 两端一致

### 4) 本地性能与容错
交互式分析链路要求：
- `metrics_refresh` 只保留轻量关键步骤
- 重型 star-history 回填改为异步 `metrics_backfill`
- GitHub 限流时：
  1. 优先复用最新指标
  2. 无最新指标则用 trending snapshot hint 兜底
  3. 避免整条分析链路失败

---

## 四、执行流程（推荐顺序）

### Step 0：基础准备
- 确认项目存在（project_id 可定位）
- 确认 GitHub Token 状态（建议配置，减少 403 限流）

### Step 1：指标刷新
- 触发 `metrics_refresh`（快速）
- 后台并行触发 `metrics_backfill`（重型，不阻塞）

### Step 2：biz + score
- 按当前模型生成商业画像与评分
- 若 LLM 不可用，回退 rule-v1 并标记说明

### Step 3：组织补全（owner = Organization）
- 执行公司背景补全：官网、融资、收入、战略动态
- 结果写回 `biz.explanations`

### Step 4：报告生成
- 组装 HTML / Markdown
- 注入：融资来源列、战略动态清洗列表、主维护者专项

### Step 5：验收
- 检查关键章节是否存在
- 检查是否还有 markdown 噪声（如 `**[...](...)**`）
- 检查融资表是否有来源列
- 检查 maintainer 专项是否有明细与结论

---

## 五、质量红线（Fail Fast）
出现以下任一项，视为不合格：
- 战略动态段落出现原始 `**[title](url)**` 文本
- 融资历史缺少“来源”列
- 仅有“贡献者数”，没有“主维护者占比”
- 因限流导致大批分析任务 failed 且无兜底
- HTML 和 Markdown 信息不一致

---

## 六、数据校验清单（报告末尾必须体现）
- Stars / Forks / Issues 是否来自 API
- License 是否识别
- 产品类别是否确认
- owner 类型是否识别（User/Organization）
- 组织官网是否抓取
- 融资记录条数与来源条数
- 主维护者是否抓取成功（>=1 条）
- 竞品是否有效（非占位）

---

## 七、建议阈值（可直接复用）
- 主维护者集中度 `>= 50%`：高单点风险
- 主维护者集中度 `30% ~ 50%`：中等集中
- 主维护者集中度 `< 30%`：分散健康

---

## 八、典型故障与处理

### 1) GitHub 403 rate limit exceeded
- 优先检查 Token
- fallback 到 latest metric / snapshot hint
- 不中断报告主链路

### 2) 报告格式乱（战略动态）
- 必须先 parse 再 render
- 不允许 raw markdown 直接入 HTML `<p>`

### 3) 融资记录重复
- 可在后处理阶段按 `round+amount+source` 去重
- 保留来源最多的一条

### 4) 主维护者数据缺失
- 触发一次 metrics refresh
- 若 API 返回 202/空，先报告“待抓取”并提示重试

---

## 九、验收示例（最小）
满足以下即可通过：
- 报告含“主维护者专项”表格（至少 1 行）
- 报告含“专项说明”一句结论
- 融资历史表含来源列并可点击
- 战略动态为中文列表，无 markdown 噪声
- Markdown 导出与 HTML 对齐

---

## 十、实现映射（Trend2Biz 当前代码）
- 维护者抓取与贡献占比：`app/services/github_metrics.py`
- 指标入库与缓存 explanations：`app/main.py` -> `refresh_metrics_for_project`
- 报告章节渲染：`app/main.py` -> `generate_report`
- 异步重任务：`metrics_backfill`

