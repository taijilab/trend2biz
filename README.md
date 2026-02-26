# Trend2Biz

Trend2Biz 是一个 GitHub Trending 热点发现与商业化评估的 MVP 后端服务，按文档 `Trend2Biz.md / trend2biz-spec.md / trend2biz-api.md / trend2biz-backend.md` 生成。

## 技术栈

- FastAPI
- SQLAlchemy
- SQLite（默认，可切换 PostgreSQL）
- httpx + BeautifulSoup（Trending 抓取解析）

## 快速启动

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
python3 -m uvicorn app.main:app --reload
```

打开文档：`http://127.0.0.1:8000/docs`

## 核心 API

- `GET /api/v1/trending/snapshots`
- `POST /api/v1/trending/snapshots:fetch`
- `GET /api/v1/projects`
- `GET /api/v1/projects/{project_id}`
- `GET /api/v1/projects/{project_id}/metrics`
- `POST /api/v1/projects/{project_id}/metrics:refresh`
- `GET /api/v1/projects/{project_id}/biz-profiles`
- `POST /api/v1/projects/{project_id}/biz-profiles:generate`
- `GET /api/v1/projects/{project_id}/scores`
- `POST /api/v1/scores:batch`
- `POST /api/v1/reports:generate`
- `POST /api/v1/watchlist`
- `GET /api/v1/watchlist`
- `DELETE /api/v1/watchlist/{project_id}`

## 说明

- Trending 抓取无官方 API，采用页面抓取 + HTML 解析。
- 商业化画像和评分当前是规则引擎版本（可替换为 LLM 流程）。
- 数据库会在服务启动时自动建表。
