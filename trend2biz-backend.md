# 任务执行器伪代码

下面继续补齐你说的三样：

**(C) 任务执行器伪代码（含并发/幂等/重试）**

**(D) GitHub API 并发与限流策略（实战可用）**

**(E) Trending 解析器回归测试用例格式（防页面结构变化）**

---

## C) 任务执行器伪代码（Worker / Scheduler）

### C.1 Job 拉取与锁（避免多 Worker 重复跑）

**核心原则：用 DB 行锁实现分布式抢占。**

```
loop forever:
  job = SELECT * FROM jobs
        WHERE status='queued'
        ORDER BY created_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED

  if no job:
     sleep(2s); continue

  UPDATE jobs SET status='running', started_at=now(), attempt=attempt+1
  COMMIT

  try:
     run_job(job)
     UPDATE jobs SET status='succeeded', finished_at=now()
  except RetryableError e:
     if job.attempt < job.max_attempt:
        UPDATE jobs SET status='queued', error=e.message
        sleep(backoff(job.attempt))
     else:
        UPDATE jobs SET status='failed', finished_at=now(), error=e.message
  except FatalError e:
     UPDATE jobs SET status='failed', finished_at=now(), error=e.message
```

**建议加一个“幂等 Key”字段（可选但非常有用）**

- 例如：`job_key = "trending:2026-02-26:daily:all"`
    
    这样同类任务不会重复入队。
    

DDL 增补：

```sql
ALTER TABLE jobs ADD COLUMN job_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_job_key ON jobs(job_key) WHERE job_key IS NOT NULL;
```

---

### C.2 run_job(job) 任务类型分发（重点：每步都可重跑）

```
function run_job(job):
  switch job.job_type:

    case 'trending_fetch':
      dim = job.payload {date, since, language, spoken}
      snapshot_id = upsert_trending_snapshot(dim)
      items = fetch_and_parse_trending_html(dim)
      upsert_snapshot_items(snapshot_id, items)
      enqueue('metrics_refresh_batch', {snapshot_id})
      enqueue('biz_score_batch', {snapshot_id})

    case 'metrics_refresh_batch':
      snapshot_id = payload.snapshot_id
      project_ids = SELECT project_id FROM trending_snapshot_items WHERE snapshot_id=...
      for batch in chunk(project_ids, 50):
         enqueue('metrics_refresh', {project_ids: batch, metric_date: snapshot_date(snapshot_id)})

    case 'metrics_refresh':
      for project_id in payload.project_ids:
         m = github_api_enrich(project_id)
         UPSERT repo_metrics_daily(project_id, metric_date) SET ...
      -- no need to enqueue next; batch scoring job handles

    case 'biz_score_batch':
      snapshot_id = payload.snapshot_id
      score_model_id = payload.score_model_id or default_latest_score_model()
      project_ids = SELECT project_id ... top N ranks (e.g. 200)
      for batch in chunk(project_ids, 20):
         enqueue('biz_generate_and_score', {project_ids: batch, snapshot_id, score_model_id})

    case 'biz_generate_and_score':
      metric_date = snapshot_date(snapshot_id)
      for project_id in payload.project_ids:
         metrics = latest metrics for metric_date (or latest)
         biz = maybe_generate_biz_profile(project_id)  -- append-only
         score = compute_score(project_id, metrics, biz, score_model_id)
         INSERT project_scores(...)
      enqueue('report_generate_batch', {snapshot_id})

    case 'report_generate_batch':
      project_ids = select projects in snapshot with latest grade in ('S','A')
      for batch in chunk(project_ids, 20):
         enqueue('report_generate', {project_ids: batch})

    case 'report_generate':
      for project_id in payload.project_ids:
         html = render_one_pager(project_id)
         INSERT reports(...)
```

**幂等关键点**

- trending snapshot：UPSERT（维度唯一键）
- snapshot items：建议“先清空再写入”同 snapshot（保证一致）
- metrics：UPSERT 主键 (project_id, metric_date)
- biz / score / report：append-only，但需要“去重策略”（见 D）

---

## D) 并发与限流策略（GitHub + LLM）

### D.1 GitHub API 限流（务实版）

**GitHub REST/GraphQL 都有 rate limit**，且不同 token/应用配额不同。建议做三层策略：

### (1) 全局令牌桶（Token Bucket）

- 设一个全局 `requests_per_minute`（如 800/min，按实际配额调）
- Worker 每次请求前 `acquire(1)`，不足则 sleep

### (2) 自适应：读 rate limit header

每次响应读取：

- `X-RateLimit-Remaining`
- `X-RateLimit-Reset`（epoch 秒）
    
    若 remaining 低于阈值（比如 < 50）：
    
- 立刻降并发（concurrency /= 2）
- 或把后续 job 重新入队（RetryableError + backoff 到 reset 后）

### (3) Batch 化减少 API 调用

- GraphQL 优势：一次 query 拉多个字段，甚至多个 repo（推荐 V1.0 上 GraphQL）
- MVP REST：对 top N 项目即可，不要全量追

---

### D.2 并发配置建议（MVP 默认）

- `trending_fetch`: 并发 1（避免重复）
- `metrics_refresh`: 并发 5~10（看 token 配额）
- `biz_generate_and_score`: 并发 3~5（LLM 成本高）
- `report_generate`: 并发 3~5

> 经验值：**metrics 别超过 10 并发**，否则 rate-limit 抖动会让系统不稳定。
> 

---

### D.3 Append-only 去重（避免 batch 重跑导致爆表）

你前面要求长期分析要保存历史，这是对的，但**同一天同模型反复跑**会制造噪音。建议两条：

### (1) Score：同日同模型只保留一条

你前面我已经给了更严格的唯一索引：

```sql
CREATE UNIQUE INDEX uq_score_once_per_day
ON project_scores(project_id, score_model_id, COALESCE(metric_date, DATE '1970-01-01'));
```

应用层插入用 `INSERT ... ON CONFLICT DO UPDATE`（覆盖解释/结论即可）。

### (2) Biz Profile：同项目同 biz_model 在 24h 内只保留最新

biz_profiles 你可以保持 append-only，但加一个“软去重”策略：

- 若 `created_at > now()-24h` 且同 `biz_profile_model_id` 已存在 → UPDATE 那条（减少垃圾版本）

可选唯一约束（更强但更死板，不推荐直接上）：

- `(project_id, biz_profile_model_id, date_trunc('day', created_at))`

---

## E) Trending 解析器回归测试用例格式（非常关键）

> Trending 没官方 API，页面结构变动是常态。你需要一个“黄金样本库”来做回归测试，确保解析器升级不把字段解析错。
> 

### E.1 用例目录结构（建议）

```
tests/
  fixtures/
    trending/
      2026-02-01_daily_all.html
      2026-02-01_daily_python.html
      2026-02-01_weekly_all.html
  expected/
    trending/
      2026-02-01_daily_all.json
      2026-02-01_daily_python.json
      2026-02-01_weekly_all.json
  test_trending_parser.py
```

### E.2 expected JSON（字段对齐入库结构）

示例 `expected/trending/2026-02-01_daily_all.json`：

```json
{
  "since": "daily",
  "language": "all",
  "spoken": null,
  "items": [
    {
      "rank": 1,
      "repo_full_name": "owner/name",
      "repo_url": "https://github.com/owner/name",
      "description": "....",
      "primary_language": "Python",
      "stars_delta_window": 1234
    }
  ]
}
```

### E.3 测试断言（必须包含的 8 条）

1. items 数量 >= 20（通常 25，但不要写死）
2. rank 连续从 1 开始（至少前 10 连续）
3. repo_full_name 满足 `owner/name` 格式
4. repo_url 以 `https://github.com/` 开头
5. stars_delta_window 为 int 或 null（不要解析成字符串）
6. description 可为空但必须存在键
7. primary_language 可为空但必须存在键
8. 对 fixture 的输出必须与 expected JSON **语义一致**（允许 minor whitespace/顺序差异）

### E.4 “结构变化探测”测试（最有用）

除了黄金样本，还要做一个**字段存在性监控**：

- 若连续 3 次抓取 `stars_delta_window` 解析为 null 的比例 > 80%
    
    → 触发告警：可能页面结构变了
    

你可以将这个监控结果写入 `jobs.error` 或单独建表 `parser_health`（可选）。

---

## 你们研发落地的最后一块拼图（我建议下一步给）

为了让系统“可运营”，还差一个最重要的东西：**数据质量与告警面板**（不然抓着抓着就悄悄坏了）。我可以下一条直接补齐：

1. 关键健康指标（抓取成功率、字段缺失率、API限流次数、LLM失败率）
2. 告警规则（阈值、连续次数、自动降级策略）
3. 对应 Postgres 表与 SQL

你回复一句：**“补健康指标与告警”** 我就接着给完整规格与实现。