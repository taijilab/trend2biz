# YC OSS Investment Analysis (v5 Decision Engine)

为指定开源项目生成 **v5 决策引擎** 投资分析报告（Investment Thesis 置首 + 三层评分卡 + Maintainer Risk Matrix）。

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

### 第一步：解析仓库名

从参数中提取 `owner/repo`（去掉 `https://github.com/` 前缀）。

### 第二步：在本地数据库查找项目

```
GET http://localhost:8000/api/v1/projects/search?q=<repo_name>&limit=5
```

- 找到匹配的项目后记录 `project_id`。
- 如果本地无数据，告知用户该项目尚未被 Trend2Biz 采集，建议先触发 Trending 采集或直接用 GitHub API 补充基础摘要（见第五步）。

### 第三步：触发 v5 LLM 分析（确保报告含 Investment Thesis）

检查项目是否已有 llm-v1 模型的 biz profile：

```
GET http://localhost:8000/api/v1/projects/<project_id>/biz-profiles
```

如果最新 biz profile 的 `version` **不是** `llm-v1`，或尚无 biz profile，则触发 LLM 分析以生成 v5_structural + investment_thesis：

```
POST http://localhost:8000/api/v1/projects/<project_id>/biz-profiles:generate
Content-Type: application/json

{"model": "llm-v1"}
```

等待 Job 完成（轮询 `GET http://localhost:8000/api/v1/jobs/<job_id>` 直到 `status` 为 `done` 或 `failed`，最多等 60 秒）。

> 注意：若 LLM 分析失败（`status=failed`）或服务端无 LLM Key，继续到第四步即可，后端会降级为 rule-v1 报告（无 Investment Thesis）。

### 第四步：生成 v5 报告

```
POST http://localhost:8000/api/v1/reports:generate
Content-Type: application/json

{"project_id": "<project_id>"}
```

返回值示例：`{"report_id": "abc123", "url": "/reports/abc123"}`

拿到 `report_id` 后获取 Markdown 内容：

```
GET http://localhost:8000/api/v1/reports/<report_id>/markdown
```

将返回的 Markdown **完整输出**给用户，并告知：
- HTML 完整报告（含 v5 交互式图表）：`http://localhost:8000/reports/<report_id>`
- 若报告顶部出现 **🎯 INVESTMENT THESIS** 卡片 + **📊 v5 三层评分卡**，表示 v5 Decision Engine 已生效

### 第五步：仅当本地无数据时，用 GitHub API 补充

```
GET https://api.github.com/repos/<owner>/<repo>
```

用基础字段（stars、forks、language、description、created_at、pushed_at、open_issues_count）
手动构造一份简化版摘要输出，并提示用户先让系统采集该项目再生成完整报告。

---

## v5 报告结构说明

v5 Decision Engine 报告包含以下新增章节（llm-v1 模式下）：

| 章节 | 内容 |
|------|------|
| 🎯 Investment Thesis | 一句话投资核心逻辑 + 投资阶段/窗口/评分摘要 |
| 📊 v5 三层评分卡 | Health(30%) + Commercial(30%) + Structural(30%) + Momentum(10%) 进度条 |
| 🔬 Maintainer Risk Matrix | 6 维度风险评估（集中度/创始人依赖/API依赖/公司集中/License/社区深度）|

旧版 YC 7 维评分卡保留在 `<details>` 折叠区，向下兼容。

---

## 注意事项

- 报告逻辑由后端 `app/main.py:generate_report()` 维护，**不要在此重复定义评分标准**。
- v5 评分等级阈值：S≥9.0 / A≥7.5 / B≥6.0 / C≥4.0 / D<4.0（比旧版更严格）。
- 如需调整分析逻辑，修改 `app/services/scoring.py:compute_score_v5()` 即可。
