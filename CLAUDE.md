# Trend2Biz — Claude Code 开发规范

## 自动合并流程

每次完成任务（所有代码已推送到 `claude/*` 分支后），**必须执行以下步骤把代码自动合并到 `main`**：

```bash
# 1. 确认当前 claude/* 分支已推送成功
git push -u origin <current-claude-branch>

# 2. 切换到 main，拉取最新
git fetch origin main
git checkout main
git pull origin main

# 3. 合并 claude 分支（fast-forward 优先，否则创建 merge commit）
git merge --no-ff <current-claude-branch> -m "Merge <branch> into main\n\nhttps://claude.ai/code/<session-id>"

# 4. 推送 main
git push origin main

# 5. 切回 claude 分支继续工作（若还有后续）
git checkout <current-claude-branch>
```

> 这样就不需要人工在 Gitea 点击 "Merge PR"。

## 开发规范

- 所有新功能开发在 `claude/*` 分支（由系统 prompt 指定）
- 单次任务完成 → 立即合并到 `main` → 推送
- commit message 末尾始终附上 Claude session URL
- Python 代码变更后必须 `python3 -c "import ast; ast.parse(open('app/main.py').read())"` 验证语法

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
