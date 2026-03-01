# Trend2Biz — Claude Code 开发规范

## 自动合并流程

每次完成任务（所有代码已推送到 `claude/*` 分支后），**必须通过 Gitea API 自动创建并合并 PR，不需要人工操作**。

### 步骤

```bash
# 1. 先推送 claude/* 分支
git push -u origin <current-claude-branch>

# 2. 用 Gitea API 创建 PR 并立即合并（替换变量后执行）
BRANCH="<current-claude-branch>"
GITEA="http://127.0.0.1:19175"
REPO="taijilab/trend2biz"
TOKEN="$(git config --get gitea.token 2>/dev/null || echo '')"

# 2a. 创建 PR
PR_NUM=$(curl -s -X POST "$GITEA/api/v1/repos/$REPO/pulls" \
  -H "Content-Type: application/json" \
  ${TOKEN:+-H "Authorization: token $TOKEN"} \
  -d "{\"title\":\"Auto-merge $BRANCH\",\"head\":\"$BRANCH\",\"base\":\"main\",\"body\":\"Auto-merged by Claude\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('number',''))")

# 2b. 合并 PR
curl -s -X POST "$GITEA/api/v1/repos/$REPO/pulls/$PR_NUM/merge" \
  -H "Content-Type: application/json" \
  ${TOKEN:+-H "Authorization: token $TOKEN"} \
  -d '{"Do":"merge","merge_message_field":"Auto-merged by Claude"}'

echo "PR #$PR_NUM merged into main"
```

### 如果 Gitea 关闭了分支保护（最简方式）

直接 push main，无需 PR：

```bash
git fetch origin main
git checkout main && git pull origin main
git merge --no-ff <current-claude-branch> -m "Merge <branch> into main"
git push origin main
git checkout <current-claude-branch>
```

## 开发规范

- 所有新功能开发在 `claude/*` 分支（由系统 prompt 指定）
- 单次任务完成 → 推送 → **自动合并到 main**（上方流程）
- commit message 末尾始终附上 Claude session URL
- Python 代码变更后必须验证语法：
  ```bash
  python3 -c "import ast; ast.parse(open('app/main.py').read()); print('OK')"
  ```

## 项目结构

```
app/
  main.py          — FastAPI 主应用（所有路由/业务逻辑）
  models.py        — SQLAlchemy ORM 模型
  schemas.py       — Pydantic 请求/响应 schema
  config.py        — 配置（Settings）
static/
  app.js           — 前端逻辑
  index.html       — 主页面
.claude/
  commands/        — Claude slash command skills
```

## 常用命令

```bash
# 语法检查
python3 -c "import ast; ast.parse(open('app/main.py').read()); print('OK')"

# 本地运行
uvicorn app.main:app --reload --port 8000
```
