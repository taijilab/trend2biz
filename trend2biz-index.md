# Trend2Biz 运营监控系统设计方案（trend2biz-index）

## 1. 目标与范围

### 1.1 目标
- 实时掌握 Trend2Biz 核心链路健康状态：抓取、入库、分析、评分、报告、接口服务。
- 在故障或数据质量下降时 5 分钟内感知，30 分钟内定位到模块级问题。
- 为运营与研发提供统一看板，支持按日期、数据源、任务类型追溯。

### 1.2 覆盖范围
- 数据采集链路：Trending 抓取、GitHub API enrich、Biz Profile、Score、Report。
- 服务链路：API 可用性、延迟、错误率。
- 数据质量：字段缺失、解析异常、重复率、时效性。
- 任务执行：队列积压、重试率、失败分布、执行耗时。

---

## 2. 监控架构

## 2.1 架构分层
1. 指标采集层
- 应用埋点（API、任务执行器、解析器）
- 数据库巡检任务（按小时跑 SQL）

2. 指标存储层
- 在线指标：Prometheus（分钟级）
- 历史分析：Postgres `monitor_*` 表（小时/天级）

3. 告警引擎层
- Prometheus Alertmanager + Webhook（飞书/Slack/邮件）
- 规则分级：P0/P1/P2

4. 可视化层
- Grafana 运营总览
- Admin 后台「系统监控」页（面向业务运营）

## 2.2 数据流
- 应用服务输出 metrics/logs -> Prometheus/Grafana
- 巡检任务写入 `monitor_health_checks`、`monitor_data_quality_daily`
- 告警规则命中后写入 `monitor_alert_events` 并推送通知

---

## 3. 指标体系（KPI / SLI / SLO）

## 3.1 可用性与性能
- `api_success_rate_5m`：5 分钟 API 成功率（目标 >= 99.5%）
- `api_p95_latency_ms`：P95 延迟（目标 <= 800ms）
- `api_error_rate_5m`：5xx 错误率（阈值 > 1% 告警）

## 3.2 任务系统
- `job_queue_backlog`：待执行任务数（阈值 > 500）
- `job_failure_rate_1h`：1 小时失败率（阈值 > 5%）
- `job_retry_rate_1h`：1 小时重试率（阈值 > 15%）
- `job_avg_duration_sec`：平均执行时长（按 job_type）

## 3.3 数据采集质量
- `trending_fetch_success_rate_daily`：每日抓取成功率（目标 >= 99%）
- `trending_items_count`：单次快照条数（阈值 < 20 预警）
- `stars_delta_null_ratio`：`stars_delta_window` 空值率（阈值 > 80% 连续 3 次告警）
- `repo_metrics_freshness_hours`：指标新鲜度（阈值 > 26h）

## 3.4 商业化分析质量
- `biz_profile_generation_success_rate`：画像生成成功率（目标 >= 98%）
- `score_generation_success_rate`：评分成功率（目标 >= 99%）
- `score_distribution_drift`：评分分布漂移（异常偏移触发审计）

---

## 4. 告警分级与策略

## 4.1 告警级别
- P0：系统不可用或主链路中断（例如连续 15 分钟 API 失败率 > 20%）
- P1：核心功能降级（抓取中断、任务失败率高、数据明显异常）
- P2：可恢复异常或趋势性风险（延迟抖动、轻度缺失、重试升高）

## 4.2 告警规则样例
1. Trending 抓取失败
- 条件：`trending_fetch_success_rate_daily < 95%` 或连续 3 次失败
- 级别：P1
- 动作：通知值班 + 自动补跑 `trending_fetch`

2. 解析器结构变更风险
- 条件：`stars_delta_null_ratio > 0.8` 且连续 3 次
- 级别：P1
- 动作：通知研发 + 切换降级模式（只保留可解析字段）

3. 指标补齐超时
- 条件：`repo_metrics_freshness_hours > 26`
- 级别：P1
- 动作：触发批量 refresh 任务，限制并发避免 rate limit

4. API 性能抖动
- 条件：`api_p95_latency_ms > 1500` 持续 10 分钟
- 级别：P2
- 动作：记录性能事件，提示扩容或优化查询

---

## 5. 运营看板设计

## 5.1 总览页（NOC）
- 今日抓取状态：成功/失败、最近快照时间、覆盖维度（daily/weekly/monthly）
- 今日任务状态：队列积压、运行中、失败率
- API 健康：QPS、成功率、P95 延迟
- 数据质量：快照条数、字段缺失率、数据新鲜度

## 5.2 数据质量页
- 每日字段缺失热力图（description/language/stars_delta）
- Trending 解析异常趋势图
- Repo metrics 新鲜度分布

## 5.3 任务运营页
- 按 job_type 的成功率、平均耗时、重试次数
- Top 失败原因（error 聚类）
- 失败任务一键重试

## 5.4 告警中心
- 当前告警列表（级别、首次触发、持续时长、负责人）
- 告警确认与关闭
- 告警 SLA 统计（响应时间、恢复时间）

---

## 6. 数据库设计（监控扩展表）

```sql
CREATE TABLE monitor_health_checks (
  check_id            UUID PRIMARY KEY,
  check_time          TIMESTAMPTZ NOT NULL DEFAULT now(),
  component           TEXT NOT NULL,          -- api/trending_fetch/metrics_refresh/biz/score/report/db
  status              TEXT NOT NULL,          -- ok/warn/error
  latency_ms          INT,
  detail              JSONB
);

CREATE INDEX idx_monitor_health_time ON monitor_health_checks(check_time DESC);
CREATE INDEX idx_monitor_health_component ON monitor_health_checks(component, check_time DESC);

CREATE TABLE monitor_data_quality_daily (
  day                             DATE PRIMARY KEY,
  snapshot_count                  INT NOT NULL DEFAULT 0,
  avg_items_per_snapshot          NUMERIC(10,2),
  stars_delta_null_ratio          NUMERIC(6,4),
  description_null_ratio          NUMERIC(6,4),
  primary_language_null_ratio     NUMERIC(6,4),
  metrics_freshness_p95_hours     NUMERIC(10,2),
  created_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE monitor_alert_events (
  alert_id             UUID PRIMARY KEY,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  level                TEXT NOT NULL,         -- P0/P1/P2
  title                TEXT NOT NULL,
  component            TEXT NOT NULL,
  rule_key             TEXT NOT NULL,
  status               TEXT NOT NULL,         -- open/ack/resolved
  fingerprint          TEXT,
  detail               JSONB,
  assignee             TEXT,
  acknowledged_at      TIMESTAMPTZ,
  resolved_at          TIMESTAMPTZ
);

CREATE INDEX idx_alert_status_time ON monitor_alert_events(status, created_at DESC);
CREATE INDEX idx_alert_component_time ON monitor_alert_events(component, created_at DESC);
```

---

## 7. 自动化巡检任务

## 7.1 调度建议
- 每 5 分钟：API 健康检查、任务积压检查
- 每 15 分钟：抓取成功率与解析字段异常检查
- 每小时：数据新鲜度检查、失败原因聚合
- 每天 00:30 UTC：生成 `monitor_data_quality_daily`

## 7.2 巡检输出
- 写入 `monitor_health_checks`
- 命中规则则写入 `monitor_alert_events`
- 对可自动修复问题触发补偿任务（如补跑 refresh）

---

## 8. 权限与运营流程

## 8.1 角色
- Admin：规则配置、告警确认、任务重试
- Operator：看板查看、告警处理、生成日报
- Viewer：只读访问

## 8.2 处理流程
1. 告警触发 -> 值班收到通知
2. 5 分钟内 ACK，标记责任人
3. 30 分钟内给出初步定位与处置
4. 恢复后补充 RCA（根因分析）

---

## 9. 上线计划

## Phase 1（1 周）
- 建立基础指标与告警：API、任务、抓取成功率
- 上线总览页 + 告警中心

## Phase 2（1-2 周）
- 增加数据质量巡检与字段异常探测
- 增加自动补偿重跑机制

## Phase 3（2 周）
- 告警降噪（去重/抑制/合并）
- 趋势预测告警（如失败率提前预警）

---

## 10. 验收标准
- 告警漏报率 < 5%
- P0/P1 平均发现时间（MTTD）< 5 分钟
- P1 平均恢复时间（MTTR）< 30 分钟
- 监控看板覆盖主链路 100%
- 每日自动巡检成功率 >= 99%
