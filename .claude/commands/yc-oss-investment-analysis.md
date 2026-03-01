# YC OSS Investment Analysis

为指定的开源项目生成一份 **YC 风格的投资分析报告**，包含 Star 增长图、商业化评估与风险判断。

## 使用方式

```
/yc-oss-investment-analysis <GitHub repo URL 或 owner/repo>
```

例如：
```
/yc-oss-investment-analysis langchain-ai/langchain
/yc-oss-investment-analysis https://github.com/ggerganov/llama.cpp
```

---

## 你的任务

你是一位顶级风险投资分析师，熟悉 YC、a16z、Sequoia 的开源项目投资框架。
用户提供了一个开源项目，请完成以下完整分析并输出**结构化报告**。

### 第一步：获取项目数据

通过 Trend2Biz 本地 API（`http://localhost:8000/api/v1`）或 GitHub API 获取该项目数据：

1. 在本地数据库中查找该项目（按 `repo_full_name` 搜索）：
   `GET /api/v1/projects?q=<owner/repo>`
2. 获取项目详情与商业画像：
   `GET /api/v1/projects/<project_id>`
3. 获取 Star 历史数据（用于绘图）：
   `GET /api/v1/projects/<project_id>/metrics?metric=stars&days=365`
4. 如本地无数据，直接调用 GitHub API：
   `GET https://api.github.com/repos/<owner>/<repo>`
   `GET https://api.github.com/repos/<owner>/<repo>/stargazers` (with `per_page=1&page=1`)

### 第二步：生成 Star 增长图（ASCII + 数据表）

用 ASCII 折线图展示 star 增长趋势，并附数据表：

```
Star 增长趋势（过去 12 个月）
  45K ┤                                              ╭───
  40K ┤                                         ╭───╯
  35K ┤                                    ╭────╯
  30K ┤                          ╭─────────╯
  25K ┤                ╭─────────╯
  20K ┤    ╭───────────╯
  15K ┼────╯
       Jan  Feb  Mar  Apr  May  Jun  Jul  Aug  Sep  Oct  Nov  Dec
```

增长关键节点（标注爆发点与可能原因）：
| 月份 | Star 数 | 月增量 | MoM% | 事件推测 |
|------|---------|--------|------|----------|
| ...  | ...     | ...    | ...  | ...      |

### 第三步：YC 投资分析报告

按以下框架逐项输出，每项给出**评级（S/A/B/C/D）**和**具体理由**：

---

## 📋 项目概况

| 字段 | 内容 |
|------|------|
| 项目名 | |
| 仓库 | |
| 语言 | |
| 创建时间 | |
| 当前 Stars | |
| Forks | |
| Contributors | |
| License | |
| 最近 commit | |
| Open Issues | |

---

## 📈 Traction & Growth（牵引力与增长）评级：__

**核心问题：项目是否在自然爆发？**

- **Star 增速**：（日均/月均增长、是否加速）
- **Fork 率**：（Fork/Star 比，高代表开发者真正使用）
- **贡献者增长**：（是否有外部 contributor 加入，还是作者独自维护）
- **Issue 活跃度**：（issue 打开/关闭比，响应速度）
- **版本发布频率**：（release 节奏是否健康）

YC 视角：*"The best startups grow fast naturally. Does this project have organic growth?"*

---

## 🎯 Problem & Market（问题与市场）评级：__

**核心问题：解决的是真实的、足够大的问题吗？**

- **解决的核心痛点**：
- **目标用户**：（个人开发者 / 企业工程师 / 数据科学家 / ...）
- **市场规模估算**：（TAM/SAM/SOM）
- **现有替代方案**：（用户为什么选这个而不是竞品）
- **痛点强度**：（nice-to-have 还是 must-have？）

YC 视角：*"Build something people want."*

---

## 🛠 Product & Technology（产品与技术）评级：__

**核心问题：技术是否有护城河？**

- **核心技术创新点**：
- **可替代性**：（是否容易被大厂复刻？）
- **集成生态**：（与哪些主流系统/云服务集成？）
- **开发者体验**：（README 质量、文档、Quick Start）
- **性能/规模化能力**：

---

## 💰 Business Model（商业模式）评级：__

**核心问题：能赚钱吗？**

分析以下可能的变现路径，并评估可行性：

| 变现方式 | 可行性 | 说明 |
|----------|--------|------|
| Open Core（企业版） | | |
| SaaS / Cloud 托管 | | |
| Support & Services | | |
| API 按量计费 | | |
| 数据/模型订阅 | | |
| Marketplace / 插件 | | |

**推荐变现路径**：（结合项目特点给出 1-2 条最优路径）

YC 视角：*"How will this make money? Who will pay and why?"*

---

## 🏆 Competition（竞争格局）评级：__

**核心问题：为什么是这个项目而不是竞品？**

| 竞品 | 类型 | 优势 | 劣势 | 与本项目差异 |
|------|------|------|------|-------------|
| | | | | |

**差异化优势**：
**潜在威胁**：（大厂是否可能直接抄袭/收购？）

---

## 👥 Team & Community（团队与社区）评级：__

**核心问题：谁在推动这个项目？**

- **核心维护者背景**：（从 GitHub profile、commit 历史判断）
- **公司背书**：（个人项目 / 初创公司 / 大厂孵化？）
- **社区健康度**：（Contributor 多样性、Discord/Slack 活跃度、文档贡献）
- **商业化意愿**：（是否有 Pro/Enterprise 计划，有无融资信号）

---

## ⚠️ Risk Assessment（风险评估）

列出主要风险，每条标注 **高/中/低**：

| 风险类型 | 等级 | 描述 | 缓解可能性 |
|----------|------|------|-----------|
| 大厂竞争风险 | | | |
| License 风险 | | | |
| 维护者单点风险 | | | |
| 商业化转化风险 | | | |
| 技术过时风险 | | | |
| 监管/合规风险 | | | |

---

## 🎯 YC 综合评分

| 维度 | 权重 | 评级 | 得分 |
|------|------|------|------|
| Traction & Growth | 30% | | |
| Problem & Market | 20% | | |
| Product & Technology | 20% | | |
| Business Model | 15% | | |
| Competition | 10% | | |
| Team & Community | 5% | | |
| **综合得分** | **100%** | | **/100** |

评分说明：S=95, A=80, B=65, C=50, D=35

---

## 💡 投资建议

**结论**：[ 强烈推荐跟进 / 值得观察 / 暂不推荐 / 不推荐 ]

**核心逻辑**（3 句话）：
1.
2.
3.

**建议行动**：
- [ ] 联系维护团队了解商业化计划
- [ ] 调研企业用户付费意愿
- [ ] 监控 Star 增速是否持续
- [ ] 关注竞品动态
- [ ] 评估 License 变更风险

**最佳介入时机**：（现在 / 等待 A 轮信号 / 等到商业化路径清晰）

---

## 📊 对比同赛道项目

如果本地数据库有同类项目，列出对比：

| 项目 | Stars | 月增 | 商业化评分 | 赛道 |
|------|-------|------|-----------|------|
| 本项目 | | | | |
| 竞品1 | | | | |
| 竞品2 | | | | |

---

*报告生成时间：{当前日期}*
*数据来源：GitHub API + Trend2Biz 本地数据库*
