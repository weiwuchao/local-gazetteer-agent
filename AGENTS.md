# AGENTS.md

面向县志语料的 RAG 问答与报告系统。后端是 FastAPI 单进程单体，前端是 Vue 3 + Vite。
需求与设计在 `doc/`；本文件只记录容易踩错、且从文件名看不出来的工程约束。

## 修改流程

1. **先对齐文档，再改代码**：改任何实现前，先找到对应的需求/设计章节，核对现有代码与文档是否一致。不一致时先改文档（需求 → 架构 → 详细设计 → 附录 DDL），再按新文档改代码；不要用代码反推文档，也不要把偏差留在注释或 TODO 里。
2. **不为旧版本兜底**：本项目以个人开发为主，没有需要保护的历史数据。可以直接重写表结构、接口字段、索引版本和迁移脚本；本地库删掉重建即可。
3. **不写兼容层**：不需要双读/双写、旧字段别名、`legacy` / `deprecated` 分支、数据搬迁脚本、版本升降级逻辑，也不要在文档里保留“兼容旧版/旧数据”的说明。

## 仓库现状

- 当前是脚手架：`app/api/routers`、`services`、`agent`、`rag` 多为占位，函数体常为 `raise NotImplementedError`。已实现、可参考的是 `app/repo/db.py`、`app/repo/migrations/0001_init.sql`、`tests/integration/test_db.py`。
- 根目录 `main.py` 是 PyCharm 示例，与本项目无关，不要改。
- 没有 CI、ESLint、pre-commit、Alembic。设计明确不用 Alembic / Celery / LangGraph / AgentExecutor；`import-linter` 虽在依赖里但尚未配置。

## 常用命令

```bash
# 后端：必须在 backend/ 下执行，否则 app 包不可导入
cd backend
poetry install
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --reload

python -m pytest tests -q
python -m pytest tests/integration/test_db.py -q   # 单个文件
ruff check .                                       # 配置在 backend/pyproject.toml
```

```bash
# 前端：没有测试脚本，验证靠类型检查与构建
cd frontend
npm install
npm run dev        # http://127.0.0.1:5173，/api 代理到 8000
npm run typecheck
npm run build      # vue-tsc --noEmit && vite build
```

## 依赖与网络

- 本项目在外网环境开发，可直接访问外网并安装第三方包（pip / poetry / npm 等）。优先复用成熟库，避免重复造轮子。
- 单个依赖下载超过 5 分钟仍未成功时，终止当前任务并说明原因；不要反复重试，也不要为了绕开下载而临时手写替代实现。

## 架构红线

- 单进程是硬约束（`--workers 1`）：进程内 `queue.Queue`、Chroma `PersistentClient`、SQLite 单写线程、内存 SSE 重放都依赖它。不要改多 worker，也不要引入 Redis/Celery。
- 依赖方向：`api → services → repo/agent/rag/workers/llm → core`。SQL 只能写在 `app/repo/**`；`agent` 不碰 SQL/IO；`rag` 只接收 scope，不自行判断权限。
- 可见范围唯一由 `services/chat_service.build_scope()` 计算，作为不可变 `SearchScope` 传给检索。Chroma 里的 `region_id`/状态元数据不能用于权限判断（会滞后）。
- 所有写走 `repo.db.get_write_queue()`，读用 `db.read_connection()` / `query_*()`；不要自己 `sqlite3.connect()` 写库。多条语句需要原子性时放进同一个 `submit(fn)`。
- 写事务内禁止任何网络调用（LLM/HTTP/文件）；单次事务超过 200ms 会打 warning。
- 时间统一 TEXT ISO-8601 UTC 毫秒（`2026-10-29T07:12:33.412Z`），禁止 INTEGER 时间戳；展示层再转 `+08:00`。
- 阶段 0–4、6 是同步 `def`，由 `agent/pipeline.py` 下沉到线程池；只有阶段 5（流式生成）和 7（交付）是 `async`。不要改成 async，也不要在工具内部再切线程池。
- `retrieval.score_threshold` 只比较 rerank 分（0–1），绝不用于 RRF 分（单路上限约 0.033）。
- 换 embedding 模型/维度/块大小/opencc 版本 = 新建 `chunks__{index_version}` + 全量重建 + 重标阈值；启动时维度不一致会拒绝启动，不能只改配置项。
- 问答流是 POST + `fetch` ReadableStream 手写 SSE（不用 `EventSource`）。`citation` 事件可能先于 `delta` 到达，前端按“骨架 → 引用卡 → 正文”渲染。
- 身份只来自 JWT；请求 DTO 禁止包含 `role` / `user_id`，统一用 `StrictModel`（`extra='forbid'`）。admin 端点必须 `require_admin`；资源归属校验失败返回 404 而非 403。

## 数据库与迁移

- `0001_init.sql` 是架构文档附录 A 的完整拷贝（24 张实体表 + `chunk_fts`）。schema 与 CHECK 枚举以它和文档为准，不要凭记忆改列或加枚举值。
- 表结构变更可直接改 `0001_init.sql`（或当前初始化脚本），删掉 `backend/data/app.db` 重建即可；不要为兼容旧库写增量迁移、数据搬迁或旧字段过渡。`db.py` 按文件名序号维护 `PRAGMA user_version`，迁移文件里不要自己写。
- 迁移在 `app/main.py` 的 lifespan 里由 `db.init()` 自动执行；顺序必须是“先迁移，再启动写线程/worker”。
- `chunk` 有两个 ID：`id` 是 INTEGER（FTS5 的 `rowid`），`chunk_id` 是 TEXT（对外与引用使用）。关联 FTS 用 `id`，业务引用用 `chunk_id`。
- `document` 与 `document_version` 是循环外键：先插 `document`（`searchable_version_id` 为 NULL）→ 插 version → 再 UPDATE 回填。
- `chunk_fts` 是 FTS5 外部内容表；重建用 `INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')`。
- 测试必须用 `tmp_path` 临时库，不要读写 `backend/data/app.db`。`backend/data/**` 的数据库、Chroma、上传件、导出件都已 gitignore。

## 实现约定

- Python 目标 3.11+，文件加 `from __future__ import annotations`，ruff `line-length = 100`。
- 文档、注释、UI 文案用中文；术语沿用设计文档（语料 / 片段 / 引证档 / 轨迹 / 润色）。
- `agent/contracts.py` 的 `AnswerType` 是脚手架占位，与 DDL 的 `message.answer_type`（`fact|partial|refuse|boundary|report_outline`）不一致；动到该文件时以 DDL 为准修正，不要扩散旧枚举。
- 前端颜色与字体只用 `src/styles/tokens.css` 的朱墨变量，不要新造色值；请求统一走 `/api`。
- 实施顺序：P0 检索（分块 + 双路召回 + RRF + rerank）→ P1 最小闭环（含轨迹写入）→ P2 → P3。不要先铺管理台或 UI。
