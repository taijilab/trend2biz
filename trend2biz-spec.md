# 接口规格（API Contract） + (2) Postgres 数据库 DDL

下面给你补齐 **(1) 接口规格（API Contract）** + **(2) Postgres 数据库 DDL**（含索引/去重键/版本表），按你要的“Trending 抓取 → 指标补齐 → 商业化分析 → 评分版本化 → 历史沉淀与长期分析”来设计。

---

## 1) API Contract（REST，v1）

### 统一约定

- Base: `/api/v1`
- Auth（MVP）：`Authorization: Bearer <token>`（或内网放行）
- 时间：ISO8601（UTC）存储；前端按 Asia/Singapore 展示
- 分页：`limit`（默认 50，max 200），`cursor`（可选）
- 版本：评分/画像均可带 `version_id` 或 `latest=true`

---

### A. Trending 榜单

### 1) 获取某天榜单快照

`GET /trending/snapshots`

- Query
    - `date`（YYYY-MM-DD，可选，默认今天）
    - `since` = `daily|weekly|monthly`（必填）
    - `language`（可选，默认 `all`）
    - `spoken`（可选）
    - `limit`（可选）
- Response (200)

```json
{
  "snapshot": {
    "snapshot_id": "uuid",
    "date": "2026-02-26",
    "since": "daily",
    "language": "all",
    "spoken": null,
    "captured_at": "2026-02-26T02:10:00Z"
  },
  "items": [
    {
      "rank": 1,
      "repo_full_name": "owner/name",
      "repo_url": "https://github.com/owner/name",
      "description": "...",
      "primary_language": "Python",
      "stars_total_hint": 12345,
      "forks_total_hint": 234,
      "stars_delta_window": 980
    }
  ]
}
```

### 2) 手动触发抓取（管理员/任务系统用）

`POST /trending/snapshots:fetch`

- Body

```json
{ "since":"daily", "language":"all", "spoken":null }
```

- Response (202)

```json
{ "job_id":"uuid", "status":"queued" }
```

---

### B. 项目库与详情

### 3) 项目列表查询（用于后台筛选）

`GET /projects`

- Query
    - `q`（repo/name/关键词）
    - `tag`（赛道标签，如 `agent`, `observability`）
    - `grade`（S/A/B/C）
    - `min_score`（0-10）
    - `language`
    - `trending_date`（某天入榜）
    - `since`
    - `limit`, `cursor`
- Response (200)

```json
{
  "items":[
    {
      "project_id":"uuid",
      "repo_full_name":"owner/name",
      "primary_language":"Python",
      "latest_score": { "total": 7.8, "grade":"A" },
      "latest_biz": { "category":"agent", "monetization_candidates":["SaaS","Open-core"] }
    }
  ],
  "next_cursor": null
}
```

### 4) 项目详情（含最新指标/画像/评分）

`GET /projects/{project_id}`

- Query: `include=metrics,biz,score,timeline`（可选）
- Response (200)（示意）

```json
{
  "project": {
    "project_id":"uuid",
    "repo_full_name":"owner/name",
    "repo_url":"https://github.com/owner/name",
    "license":"Apache-2.0",
    "created_at":"2025-08-01T00:00:00Z"
  },
  "latest_metrics": { "as_of":"2026-02-26", "stars":12345, "forks":234, "commits_30d":120, "contributors_90d":18 },
  "latest_biz_profile": { "category":"agent", "value_props":["效率","降本"], "buyer":"研发负责人", "monetization_candidates":["SaaS"] },
  "latest_score": { "total":7.8, "grade":"A", "breakdown": { "market":7.5, "traction":8.2, "moat":7.0, "monetization":7.1, "risk":8.5 } }
}
```

---

### C. 指标补齐与时间序列

### 5) 获取项目日度指标序列

`GET /projects/{project_id}/metrics`

- Query: `from=YYYY-MM-DD&to=YYYY-MM-DD&granularity=daily`
- Response (200)

```json
{
  "series":[
    { "date":"2026-02-20", "stars":11000, "forks":200, "commits_30d":98, "contributors_90d":15 },
    { "date":"2026-02-21", "stars":11250, "forks":205, "commits_30d":102, "contributors_90d":16 }
  ]
}
```

### 6) 触发项目指标更新（管理员/任务）

`POST /projects/{project_id}/metrics:refresh`

- Response (202)

```json
{ "job_id":"uuid", "status":"queued" }
```

---

### D. 商业化画像与评分（版本化）

### 7) 获取项目商业化画像（可指定版本）

`GET /projects/{project_id}/biz-profiles`

- Query: `latest=true` 或 `version_id=uuid`
- Response (200)

```json
{
  "items":[
    { "biz_profile_id":"uuid", "version":"llm-v1", "created_at":"...", "category":"agent", "confidence":0.72 }
  ]
}
```

### 8) 重新生成商业化画像（管理员/任务）

`POST /projects/{project_id}/biz-profiles:generate`

- Body（可选）：`{ "model":"gpt-5.2", "prompt_profile":"biz_v1" }`
- Response (202)：job

### 9) 获取项目评分（可指定评分版本）

`GET /projects/{project_id}/scores`

- Query: `latest=true` 或 `score_model_id=uuid`
- Response (200)

```json
{
  "items":[
    { "score_id":"uuid", "score_model_id":"uuid", "total":7.8, "grade":"A", "explanations":{...} }
  ]
}
```

### 10) 批量评分（某个 snapshot）

`POST /scores:batch`

- Body

```json
{
  "snapshot_id":"uuid",
  "score_model_id":"uuid",
  "biz_profile_model":"llm-v1"
}
```

- Response (202)：job

---

### E. 报告与观察池

### 11) 生成“一页纸报告”

`POST /reports:generate`

- Body

```json
{ "project_id":"uuid", "format":"html", "latest":true }
```

- Response (200)

```json
{ "report_id":"uuid", "url":"/reports/uuid" }
```

### 12) 观察池（watchlist）

- `POST /watchlist` body：`{ "project_id":"uuid", "note":"..." }`
- `GET /watchlist`
- `DELETE /watchlist/{project_id}`

---

## 2) Postgres DDL（含索引/去重/版本表）

> 说明：
> 
> - Trending 快照、日度指标、画像、评分都**版本化**，便于长期对比与回放。
> - 时间序列建议按 `date` 做唯一键，且对查询维度加复合索引。
> - 用 `pgcrypto` 生成 UUID（或应用端生成也行）。

```sql
-- Extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ==============
-- 1) Projects
-- ==============
CREATE TABLE projects (
  project_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  repo_full_name      TEXT NOT NULL UNIQUE,         -- owner/name
  repo_url            TEXT NOT NULL,
  description         TEXT,
  homepage_url        TEXT,
  primary_language    TEXT,
  license_spdx        TEXT,
  is_fork             BOOLEAN,
  created_at_github   TIMESTAMPTZ,
  updated_at_github   TIMESTAMPTZ,
  pushed_at_github    TIMESTAMPTZ,
  first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_projects_language ON projects(primary_language);
CREATE INDEX idx_projects_last_seen ON projects(last_seen_at);

-- =========================
-- 2) Trending snapshots
-- =========================
CREATE TYPE trending_since AS ENUM ('daily','weekly','monthly');

CREATE TABLE trending_snapshots (
  snapshot_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  snapshot_date   DATE NOT NULL,                     -- date represented
  since           trending_since NOT NULL,
  language        TEXT NOT NULL DEFAULT 'all',
  spoken          TEXT,
  captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  source          TEXT NOT NULL DEFAULT 'github_trending'
);

-- One snapshot per (date, since, language, spoken)
CREATE UNIQUE INDEX uq_trending_snapshot_dim
ON trending_snapshots(snapshot_date, since, language, COALESCE(spoken,''));

CREATE TABLE trending_snapshot_items (
  snapshot_id        UUID NOT NULL REFERENCES trending_snapshots(snapshot_id) ON DELETE CASCADE,
  rank               INT NOT NULL,
  project_id         UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  repo_full_name     TEXT NOT NULL, -- redundancy for debugging
  repo_url           TEXT NOT NULL,
  description        TEXT,
  primary_language   TEXT,
  stars_total_hint   INT,
  forks_total_hint   INT,
  stars_delta_window INT,
  raw_payload        JSONB,
  PRIMARY KEY (snapshot_id, rank)
);

CREATE INDEX idx_trending_items_project ON trending_snapshot_items(project_id);

-- =========================
-- 3) Daily repo metrics
-- =========================
CREATE TABLE repo_metrics_daily (
  project_id              UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  metric_date             DATE NOT NULL,
  stars                   INT,
  forks                   INT,
  watchers                INT,
  open_issues             INT,
  releases_180d           INT,
  commits_30d             INT,
  commits_90d             INT,
  prs_30d                 INT,
  issues_30d              INT,
  contributors_90d        INT,
  bus_factor_top1_share   NUMERIC(5,4),   -- 0~1
  issue_first_response_median_hours NUMERIC(10,2),
  data_source             TEXT NOT NULL DEFAULT 'github_api',
  captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_payload             JSONB,
  PRIMARY KEY (project_id, metric_date)
);

CREATE INDEX idx_metrics_date ON repo_metrics_daily(metric_date);
CREATE INDEX idx_metrics_project_date ON repo_metrics_daily(project_id, metric_date DESC);

-- =========================
-- 4) Taxonomy tags (赛道)
-- =========================
CREATE TABLE tags (
  tag_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tag_key     TEXT NOT NULL UNIQUE,    -- e.g. agent, ecommerce, finance, education
  tag_name    TEXT NOT NULL,           -- display name (CN/EN)
  parent_key  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE project_tags (
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  tag_id     UUID NOT NULL REFERENCES tags(tag_id) ON DELETE CASCADE,
  source     TEXT NOT NULL DEFAULT 'auto',
  confidence NUMERIC(5,4),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, tag_id)
);

CREATE INDEX idx_project_tags_tag ON project_tags(tag_id);

-- =========================
-- 5) Biz profile versions
-- =========================
CREATE TABLE biz_profile_models (
  biz_profile_model_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL UNIQUE,          -- e.g. llm-v1
  description TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE biz_profiles (
  biz_profile_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id           UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  biz_profile_model_id UUID NOT NULL REFERENCES biz_profile_models(biz_profile_model_id),
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  category             TEXT,                 -- agent / data / observability / security...
  user_persona         TEXT,
  scenarios            TEXT[],
  value_props          TEXT[],
  delivery_forms       TEXT[],               -- lib/cli/server/saas/api/plugin/agent-skill
  monetization_candidates TEXT[],            -- SaaS/Open-core/API/License/Services/Marketplace
  buyer                TEXT,
  sales_motion         TEXT,                 -- PLG/Enterprise/Channel
  confidence           NUMERIC(5,4),
  explanations         JSONB,                -- why, evidence
  raw_payload          JSONB
);

CREATE INDEX idx_biz_profiles_project_latest
ON biz_profiles(project_id, created_at DESC);

-- =========================
-- 6) Score model + score versions
-- =========================
CREATE TABLE score_models (
  score_model_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name           TEXT NOT NULL UNIQUE,        -- e.g. yc-open-source-v1
  weights        JSONB NOT NULL,              -- {market:0.25,...}
  rules          JSONB,                       -- kill switch, thresholds
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TYPE project_grade AS ENUM ('S','A','B','C');

CREATE TABLE project_scores (
  score_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  score_model_id  UUID NOT NULL REFERENCES score_models(score_model_id),
  biz_profile_id  UUID REFERENCES biz_profiles(biz_profile_id),
  metric_date     DATE,                       -- which day's metrics used (optional but recommended)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  market_score        NUMERIC(5,2),
  traction_score      NUMERIC(5,2),
  moat_score          NUMERIC(5,2),
  team_score          NUMERIC(5,2),
  monetization_score  NUMERIC(5,2),
  risk_score          NUMERIC(5,2),
  total_score         NUMERIC(5,2) NOT NULL,
  grade               project_grade NOT NULL,

  highlights      TEXT[],
  risks           TEXT[],
  followups       TEXT[],
  explanations    JSONB,                      -- per-dimension evidence
  kill_switch_hit TEXT
);

-- For "latest score" queries
CREATE INDEX idx_scores_project_latest
ON project_scores(project_id, created_at DESC);

-- prevent accidental duplicates for same model & metric_date snapshot run (optional)
CREATE UNIQUE INDEX uq_score_dedup
ON project_scores(project_id, score_model_id, COALESCE(metric_date, DATE '1970-01-01'), created_at);

-- =========================
-- 7) Reports (optional storage)
-- =========================
CREATE TABLE reports (
  report_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  score_id      UUID REFERENCES project_scores(score_id),
  format        TEXT NOT NULL,                -- html/pdf/json
  content       TEXT,                         -- store html; pdf store path/url
  storage_url   TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_reports_project ON reports(project_id, created_at DESC);

-- =========================
-- 8) Watchlist (admin/user)
-- =========================
CREATE TABLE watchlist (
  watch_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id   UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  note         TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id)
);

-- =========================
-- 9) Jobs (for fetch/enrich/analyze pipelines)
-- =========================
CREATE TYPE job_status AS ENUM ('queued','running','succeeded','failed');

CREATE TABLE jobs (
  job_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_type    TEXT NOT NULL,                  -- trending_fetch, metrics_refresh, biz_generate, score_batch
  payload     JSONB,
  status      job_status NOT NULL DEFAULT 'queued',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at  TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  error       TEXT
);

CREATE INDEX idx_jobs_status ON jobs(status, created_at DESC);
```

---

### 你接下来研发最需要的两点建议（直接影响效果）

1. **repo_metrics_daily** 建议每天固定时间跑一次（例如 02:00 UTC），保证时间序列“采样一致”。
2. **评分与画像必须带 model/version**（上面已设计），否则你们半年后权重升级会导致历史数据不可比。

---

如果你要我继续把 PRD 变成“研发可直接开工”的包，我可以再补两份（你一句话我就直接给）：

- **任务流与重试策略（Scheduler + 队列 + 幂等规则）**
- **榜单/项目页的关键 SQL（含趋势分析、赛道指数、爆发检测）**