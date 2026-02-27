# Trend2Biz 版本路线图

## 版本总览

| 版本 | 主题 | 状态 | 核心内容 |
|------|------|------|----------|
| v0.1 | 能跑能测 | ✅ 已完成 | 修 bug、加测试、CLI 验证脚本 |
| v0.2 | 稳定可靠 | ✅ 已完成 | 异步 job、GitHub 限流处理、结构化日志、cursor 分页 |
| v0.3 | 分析增强 | 🔲 待开发 | 扩展商业分类、动态评分解释、HTML 报告模板 |
| v0.4 | 自动调度 | 🔲 待开发 | APScheduler 定时任务、Job 幂等重试 |
| v0.5 | 历史分析 | 🔲 待开发 | 爆发检测、赛道指数、最佳跟进清单 API |
| v1.0 | LLM 分析 | 🔲 待开发 | 可插拔 LLM 商业画像、BD 话术生成 |

---

## 已完成

### v0.1 能跑能测
- 修复 Job 错误处理 bug（rollback 后对象失效）
- metrics/biz/score 接口改为真正异步（202 + job_id）
- highlights/risks 改为数据驱动（不再硬编码）
- 加测试套件（pytest）
- 加 CLI 验证脚本 `scripts/run_pipeline.py`
- 加 `.env.example`

### v0.2 稳定可靠
- GitHub REST API 真实指标采集（stars/forks/commits/contributors）
- Job 指数退避重试（retry_count / max_retries）
- 结构化 JSON 日志
- `GET /projects` cursor 分页
- 一键端到端脚本 `scripts/run_e2e_api.py`（自动读 `.env`）
- 交互式仪表板 `scripts/dashboard.py`（Rich TUI，显示已分析指标）

---

## 待开发

### v0.3 分析增强
**核心目标**：商业分析结果有实质性参考价值

- 扩展 `biz.py` 分类到 10+ 类
  - 当前：agent / data / observability / security / developer-tools
  - 新增：fintech / edu / ecommerce / infra / low-code
- 评分 `explanations` 字段包含每维度触发信号
  - 例：`"stars=18000 → traction_score=5.8"`
- `followups` 基于 category + grade 动态生成专属追问清单
- `POST /reports:generate` 生成真正可用的 HTML 一页纸
  - 包含：repo 信息、评分文字雷达图、亮点、风险、追问清单
- 同 project + 同 model + 同天 → score UPSERT（防重复追加）

### v0.4 自动调度
**核心目标**：系统无人值守，每天自动跑完整管道

- 集成 `APScheduler`（无需 Celery/Airflow）
  - 00:10 UTC：trending_fetch daily/all
  - 00:20 UTC：trending_fetch weekly/all
  - 01:00 UTC：top 200 项目 metrics_refresh
  - 01:30 UTC：biz+score batch
  - 02:00 UTC：report_generate（grade S/A）
- Job 幂等 key 防止重复入队
- `GET /api/v1/jobs?status=failed` 查看失败任务
- `POST /api/v1/jobs/{id}:retry` 手动重试

### v0.5 历史分析
**核心目标**：回答"哪些赛道在爆发？哪些项目值得跟进？"

- `GET /api/v1/analysis/burst` — 爆发检测（7日加速 + 入榜次数）
- `GET /api/v1/analysis/leaderboard` — 最值得跟进清单（高分 + SaaS/Open-core）
- `GET /api/v1/analysis/sector-index?tag=agent&from=&to=` — 赛道热度指数
- 引入 `tags` + `project_tags` 表，自动从 biz_profile.category 写入
- Alembic 迁移脚本（Postgres 生产环境）

### v1.0 LLM 分析
**核心目标**：商业画像质量从"关键词匹配"升级到"语义理解"

- `biz.py` 支持可插拔后端：`rule-v1`（现有）vs `llm-v1`（新增）
- LLM backend：传入 repo_name + description + README 摘要，返回结构化 biz profile
- 新增 BD 话术字段 `bd_pitch`（合作价值 + 对方痛点 + 可提供资源）
- 置信度从固定 0.65 改为 LLM 输出的实际值
- 模型版本化，支持历史对比

---

## 快速启动

```bash
# 安装依赖
pip install -r requirements.txt

# 配置 token（可选，有 token 限速 5000 req/h，无则 60 req/h）
cp .env.example .env
# 编辑 .env，填入 GITHUB_TOKEN

# 交互式仪表板（推荐）
python scripts/dashboard.py

# 一键端到端脚本
python scripts/run_e2e_api.py

# 纯管道验证（离线）
python scripts/run_pipeline.py --offline
```
