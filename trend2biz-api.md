# 任务流与重试策略+关键 SQL

下面我把你要的两份继续补齐：

**(A) 任务流与重试策略（Scheduler + 队列 + 幂等规则）**

**(B) 关键 SQL（榜单/项目页/趋势分析/赛道指数/爆发检测）**

全部基于你前面确认的 **Postgres** 方案。

---

## A) 任务流与重试策略

### A.1 总体流水线（Pipeline）

目标：每次抓取都能 **可重跑、可回放、可追溯**。

**Pipeline：Trending → Projects Upsert → Metrics Enrich → Biz Profile → Score → Report**

1. **Trending Fetch**
- 输入：`since, language, spoken, snapshot_date`
- 输出：`trending_snapshots + trending_snapshot_items`
- 幂等：对 `trending_snapshots(snapshot_date,since,language,spoken)` 唯一键 UPSERT
1. **Project Upsert**
- 从 snapshot_items 提取 repo_full_name
- projects 表 UPSERT（`repo_full_name` 唯一）
- 更新 `last_seen_at`
1. **Metrics Refresh（当日）**
- 对 snapshot 命中项目跑 `repo_metrics_daily(project_id, metric_date=today)` UPSERT
- 幂等：主键 `(project_id, metric_date)` 决定
1. **Biz Profile Generate**
- 触发条件：
    - 项目首次入榜
    - 或模型版本升级
    - 或“近7天 stars_delta/commits 激增”重新生成
- 输出：`biz_profiles` 追加版本（不覆盖历史）
1. **Score Compute**
- 对 snapshot 批量评分（指定 score_model_id）
- 输出：`project_scores` 追加版本（保留历史）
1. **Report Generate（可选）**
- 对 A/S 级项目生成一页纸 HTML，保存到 `reports`

---

### A.2 调度策略（推荐默认）

> 你们做长期分析，关键是“采样一致”和“热点及时”。
> 

**每天（必做）**

- 00:10 UTC：抓 `daily all`
- 00:20 UTC：抓 `weekly all`
- 00:30 UTC：抓 `monthly all`
- 01:00 UTC：对当天 snapshot 项目跑 metrics refresh
- 01:30 UTC：对当天 snapshot 项目跑 biz+score（只对 top N，如 200）
- 02:00 UTC：生成报告（只对 grade S/A）

**每小时（可选，增强“捕捉爆发”）**

- 每小时整点 +10 分钟：抓 `daily all`
- 对“新入榜项目”优先 enrich（降成本）

---

### A.3 Job 设计：队列化 + 幂等 + 可重试

你现在有 `jobs` 表了，建议加 2 个字段让执行器更稳：

**DDL 增补**

```sql
ALTER TABLE jobs
ADD COLUMN attempt INT NOT NULL DEFAULT 0,
ADD COLUMN max_attempt INT NOT NULL DEFAULT 5;

CREATE INDEX idx_jobs_type_status ON jobs(job_type, status, created_at DESC);
```

**任务执行器规则**

- `queued → running → succeeded/failed`
- 重试：`failed` 且 `attempt < max_attempt` → 回到 `queued`（指数退避）
- 退避建议：`min(2^attempt * 30s, 30min) + random(0..30s)`
- 熔断：GitHub API 速率受限时，暂停 metrics/biz/score 类 job

---

### A.4 幂等（Idempotency）规则（最重要）

你后面会反复重跑任务，所以必须确保：

1. **Trending snapshot 幂等**
- snapshot 维度唯一键已建：`uq_trending_snapshot_dim`
- 插入 snapshot 用 UPSERT：
    - 已存在则复用 snapshot_id
    - items 以 `(snapshot_id, rank)` 为主键，先 delete 再 insert，或逐条 upsert
1. **Metrics 幂等**
- `repo_metrics_daily` 用 `(project_id, metric_date)` 主键 UPSERT
- 同一天重复跑会覆盖当日数值（OK）
1. **Biz/Score 版本化**
- 永远新增一条版本（append-only）
- “最新版本”由 `created_at DESC` 决定
- 需要去重时：可在应用层“同项目同模型同 metric_date 若已存在则跳过”

---

### A.5 失败场景与处理

1. Trending 页面结构变化
- 解析失败：job failed + 告警
- 保护：保留 raw_payload 与 HTML 片段（建议在对象存储存一份）
1. GitHub API rate limit
- 将 metrics refresh 分批（比如 50 个 repo/批）
- 超限：标记 job failed，按退避重试
1. LLM 分析失败/超时
- biz_profile 允许降级：先规则分类 + 低置信度
- score 可在 biz 缺失时仍可跑（把 biz 相关项权重置 0 或默认值）

---

## B) 关键 SQL（后台查询 + 趋势分析）

> 下面 SQL 都是你们后台/研究页立刻可用的“核心查询”。
> 

### B.1 某天 Trending 榜单（含最新评分）

```sql
WITH snap AS (
  SELECT snapshot_id
  FROM trending_snapshots
  WHERE snapshot_date = $1::date
    AND since = $2::trending_since
    AND language = $3
    AND COALESCE(spoken,'') = COALESCE($4,'')
  LIMIT 1
),
latest_score AS (
  SELECT DISTINCT ON (ps.project_id)
    ps.project_id, ps.total_score, ps.grade, ps.created_at
  FROM project_scores ps
  ORDER BY ps.project_id, ps.created_at DESC
)
SELECT
  i.rank,
  p.repo_full_name,
  p.primary_language,
  i.stars_delta_window,
  m.stars, m.forks, m.commits_30d, m.contributors_90d,
  ls.total_score, ls.grade
FROM snap
JOIN trending_snapshot_items i ON i.snapshot_id = snap.snapshot_id
JOIN projects p ON p.project_id = i.project_id
LEFT JOIN repo_metrics_daily m
  ON m.project_id = p.project_id AND m.metric_date = $1::date
LEFT JOIN latest_score ls
  ON ls.project_id = p.project_id
ORDER BY i.rank ASC;
```

---

### B.2 项目详情页：取最新 metrics / biz / score

```sql
SELECT
  p.*,
  m.metric_date, m.stars, m.forks, m.commits_30d, m.contributors_90d,
  b.category, b.monetization_candidates, b.confidence,
  s.total_score, s.grade
FROM projects p
LEFT JOIN LATERAL (
  SELECT *
  FROM repo_metrics_daily
  WHERE project_id = p.project_id
  ORDER BY metric_date DESC
  LIMIT 1
) m ON TRUE
LEFT JOIN LATERAL (
  SELECT *
  FROM biz_profiles
  WHERE project_id = p.project_id
  ORDER BY created_at DESC
  LIMIT 1
) b ON TRUE
LEFT JOIN LATERAL (
  SELECT *
  FROM project_scores
  WHERE project_id = p.project_id
  ORDER BY created_at DESC
  LIMIT 1
) s ON TRUE
WHERE p.project_id = $1::uuid;
```

---

### B.3 项目时间序列（stars/commits/contrib）

```sql
SELECT metric_date, stars, forks, commits_30d, contributors_90d
FROM repo_metrics_daily
WHERE project_id = $1::uuid
  AND metric_date BETWEEN $2::date AND $3::date
ORDER BY metric_date ASC;
```

---

### B.4 “爆发检测”：7日 stars 增量加速 + 入榜次数

思路：用日度 stars 差分计算“近7天增长”，再看增长是否加速。

```sql
WITH s AS (
  SELECT
    project_id,
    metric_date,
    stars,
    stars - LAG(stars) OVER (PARTITION BY project_id ORDER BY metric_date) AS stars_delta_1d
  FROM repo_metrics_daily
  WHERE metric_date >= (CURRENT_DATE - INTERVAL '30 days')::date
),
agg AS (
  SELECT
    project_id,
    SUM(COALESCE(stars_delta_1d,0)) FILTER (WHERE metric_date >= CURRENT_DATE - INTERVAL '7 days')  AS delta_7d,
    SUM(COALESCE(stars_delta_1d,0)) FILTER (WHERE metric_date BETWEEN CURRENT_DATE - INTERVAL '14 days' AND CURRENT_DATE - INTERVAL '8 days') AS delta_prev_7d
  FROM s
  GROUP BY project_id
),
trend_hits AS (
  SELECT project_id, COUNT(*) AS trending_hits_14d
  FROM trending_snapshot_items i
  JOIN trending_snapshots s ON s.snapshot_id = i.snapshot_id
  WHERE s.snapshot_date >= CURRENT_DATE - INTERVAL '14 days'
  GROUP BY project_id
)
SELECT
  p.repo_full_name,
  a.delta_7d,
  a.delta_prev_7d,
  (a.delta_7d - a.delta_prev_7d) AS acceleration,
  COALESCE(t.trending_hits_14d,0) AS trending_hits_14d
FROM agg a
JOIN projects p ON p.project_id = a.project_id
LEFT JOIN trend_hits t ON t.project_id = a.project_id
WHERE a.delta_7d >= 200
  AND (a.delta_7d - a.delta_prev_7d) >= 100
ORDER BY acceleration DESC, a.delta_7d DESC
LIMIT 100;
```

---

### B.5 赛道指数（按 tag）：入榜次数 + 平均排名 + 平均增长

```sql
WITH hits AS (
  SELECT
    s.snapshot_date,
    pt.tag_id,
    COUNT(*) AS hit_count,
    AVG(i.rank) AS avg_rank,
    AVG(COALESCE(i.stars_delta_window,0)) AS avg_stars_delta_window
  FROM trending_snapshot_items i
  JOIN trending_snapshots s ON s.snapshot_id = i.snapshot_id
  JOIN project_tags pt ON pt.project_id = i.project_id
  WHERE s.snapshot_date BETWEEN $1::date AND $2::date
    AND s.since = $3::trending_since
  GROUP BY s.snapshot_date, pt.tag_id
)
SELECT
  h.snapshot_date,
  t.tag_key,
  t.tag_name,
  h.hit_count,
  h.avg_rank,
  h.avg_stars_delta_window
FROM hits h
JOIN tags t ON t.tag_id = h.tag_id
ORDER BY h.snapshot_date ASC, h.hit_count DESC;
```

---

### B.6 “最值得跟进清单”：近期入榜 + 高分 + 商业化候选为 SaaS/Open-core

```sql
WITH recent_trend AS (
  SELECT DISTINCT i.project_id
  FROM trending_snapshot_items i
  JOIN trending_snapshots s ON s.snapshot_id = i.snapshot_id
  WHERE s.snapshot_date >= CURRENT_DATE - INTERVAL '7 days'
),
latest_score AS (
  SELECT DISTINCT ON (project_id)
    project_id, total_score, grade
  FROM project_scores
  ORDER BY project_id, created_at DESC
),
latest_biz AS (
  SELECT DISTINCT ON (project_id)
    project_id, monetization_candidates, confidence
  FROM biz_profiles
  ORDER BY project_id, created_at DESC
)
SELECT
  p.repo_full_name,
  ls.total_score, ls.grade,
  lb.monetization_candidates, lb.confidence
FROM recent_trend rt
JOIN projects p ON p.project_id = rt.project_id
LEFT JOIN latest_score ls ON ls.project_id = p.project_id
LEFT JOIN latest_biz lb ON lb.project_id = p.project_id
WHERE ls.total_score >= 7.2
  AND (
    lb.monetization_candidates @> ARRAY['SaaS']::text[]
    OR lb.monetization_candidates @> ARRAY['Open-core']::text[]
  )
ORDER BY ls.total_score DESC
LIMIT 50;
```

---

## 下一步我建议你立刻加的 2 个小增强（不改大架构）

1. **Score 去重逻辑更严格**
    
    把 `uq_score_dedup` 改成 “同项目 + 同 score_model + 同 metric_date 只能一条”，这样 batch 重跑不会越堆越多：
    

```sql
DROP INDEX IF EXISTS uq_score_dedup;
CREATE UNIQUE INDEX uq_score_once_per_day
ON project_scores(project_id, score_model_id, COALESCE(metric_date, DATE '1970-01-01'));
```

1. **为趋势分析准备的物化视图（可选）**
    
    把 `repo_metrics_daily` 的差分与7日聚合做成 MV，前端秒开（后面我也可以直接给你 MV 版本）。
    

---

你要不要我继续把“任务执行器伪代码 + 并发/限流策略 + 解析器回归测试用例格式”也补齐？（这三样一补，研发团队就可以直接开工了。）