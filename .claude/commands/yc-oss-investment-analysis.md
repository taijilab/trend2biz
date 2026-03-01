# YC OSS Investment Analysis

为指定开源项目生成 YC 风格投资分析报告。

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
- 如果本地无数据，直接调 GitHub API 补充基础信息（见第四步），然后告知用户该项目尚未被 Trend2Biz 采集。

### 第三步：生成报告（调用后端，避免重复实现）

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

将返回的 Markdown **完整输出**给用户。

同时告知用户可在浏览器打开完整 HTML 报告：
`http://localhost:8000/reports/<report_id>`

### 第四步：仅当本地无数据时，用 GitHub API 补充

```
GET https://api.github.com/repos/<owner>/<repo>
```

用基础字段（stars、forks、language、description、created_at、pushed_at、open_issues_count）
手动构造一份简化版摘要输出，并提示用户先让系统采集该项目再生成完整报告。

---

## 注意事项

- 报告的 YC 评分维度、竞品数据库、风险分类、商业模式选项均由后端维护，**不要在此重复定义**。
- 如需调整分析逻辑，修改 `app/main.py` 的 `generate_report` 函数即可，skill 无需同步修改。
