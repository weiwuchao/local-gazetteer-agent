# AGENTS.md

面向县志语料的 RAG 问答与报告系统。后端是 FastAPI 单进程单体，前端是 Vue 3 + Vite。
需求与设计在 `doc/`；本文件只记录容易踩错、且从文件名看不出来的工程约束，以及本项目的取舍标准。

## 项目定位（取舍标准）

这是一个**练习 agent 开发**的个人项目，不是要交付的产品。任何设计分歧先问一句：
**"不做它，agent 的核心能力会退化吗？"** 不会 → 省略或最简实现；会 → 认真做。

| 优先级 | 内容 | 态度 |
|---|---|---|
| **核心**（做扎实） | 8 阶段流水线、5 个工具的边界、预算与上下文截断、提示词版本化、三类守卫（注入/数字/引用）、轨迹与 span、双路召回 + RRF + rerank、拒答与"部分回答"的判定、评测集 | 这是练手目标，值得多花时间 |
| **外围**（能省则省） | 环境探针、诊断与修复文案、脚本参数、部署与 CI、完整权限体系、前端样式、监控集成、兼容层 | 最简实现，允许省略、允许硬编码、允许只支持本机 |

明确不要做的四件事：给外围逻辑引入抽象层、为外围写大量测试、把外围设计成插件化/可扩展结构、
为"以后可能需要"提前布局。宁可少写，也不要为了健壮而健壮。

## 修改流程

1. **先对齐文档，再改代码**：改任何实现前，先找到对应的需求/设计章节，核对现有代码与文档是否一致。不一致时先改文档（需求 → 架构 → 详细设计 → 附录 DDL），再按新文档改代码；不要用代码反推文档，也不要把偏差留在注释或 TODO 里。
2. **不为旧版本兜底**：本项目以个人开发为主，没有需要保护的历史数据。可以直接重写表结构、接口字段、索引版本和迁移脚本；本地库删掉重建即可。
3. **不写兼容层**：不需要双读/双写、旧字段别名、`legacy` / `deprecated` 分支、数据搬迁脚本、版本升降级逻辑，也不要在文档里保留“兼容旧版/旧数据”的说明。
4. **less is more**：新增文件/抽象/配置项之前先确认它服务于「项目定位」里的核心项。同一个功能能用 3 个文件写完就不要拆成 6 个；能删的代码优先删。过剩的工程化本身就是负担，会挤占练习 agent 的时间。

## 仓库现状

- 当前是脚手架：`app/api/routers`、`services`、`agent` 多为占位，函数体常为 `raise NotImplementedError`。
- **P0 入库链路已实现**：`rag/{normalizer,chunking,models,embeddings,chroma_store,indexer}.py`、
  `repo/{app_config,chunk,document,region,task}_repo.py`；
  手动入库用 `backend/scripts/ingest_corpus.py`（md/txt/pdf/docx，缺依赖/模型直接失败，不降级）。
- 没有再提供环境体检脚本：排查就看 traceback + `pytest`。
- 已实现、可参考的还有 `app/repo/db.py`、`app/repo/migrations/0001_init.sql`、`tests/integration/test_db.py`。
- 根目录 `main.py` 是 PyCharm 示例，与本项目无关，不要改。
- 没有 CI、ESLint、pre-commit、Alembic。设计明确不用 Alembic / Celery / LangGraph / AgentExecutor；`import-linter` 虽在依赖里但尚未配置。

## Python 环境

- **解释器不是全局 Python**：项目用父目录的虚拟环境 `E:\agent_code\.venv`（解释器 `E:\agent_code\.venv\Scripts\python.exe`，**3.13.17**，由 `virtualenv` 从 `H:\Python313` 创建，`include-system-site-packages = false`，与全局 site-packages 完全隔离）。
- 项目根 `local-gazetteer-agent/` 里**没有** `.venv`，venv 在**上一级** `E:\agent_code\.venv`。用时先激活（PowerShell：`E:\agent_code\.venv\Scripts\Activate.ps1`；cmd：`...\activate.bat`），或直接用绝对路径调用。
- **不要依赖 PATH 上的 `python`**：那是 `H:\Python313\python.exe`，装的东西不一样（它没有 `tiktoken`，`chromadb` 是 1.3.7 而不是 1.5.9），照着跑会出现"明明装过却 ImportError"。
- 本机**没装 poetry**，`pyproject.toml` 只作依赖清单与工具配置；装包用 `python -m pip install <pkg>`（激活 venv 后），装完把版本补回 `pyproject.toml`，保持清单与实装一致。
- `pyproject.toml` 声明 `python = "^3.11"`，实际跑 3.13.17；代码仍按 3.11 语法写，不要因为本机是 3.13 就用 3.12+ 特性。
- venv 实测版本（排障先对这里，别凭印象升级/降级）：`fastapi 0.162`、`pydantic 2.14`、`chromadb 1.5.9`、`sentence-transformers 6.2.0`、`torch 2.15.0+cpu`、`langchain-core 1.7.3`、`langchain-openai 1.7.3`、`jieba 0.42.1`、`sentencepiece 0.2.2`、`tiktoken 0.14.0`、`uvicorn 0.54`、`pytest 9.2.0`、`pytest-asyncio 1.4.1`、`httpx 0.28.1`、`ruff 0.17.3`、SQLite 3.50.4。
- 声明了但 venv 里**没有**：`python-magic`（用到前先装）、`import-linter`（本就没配置，不要为它加配置文件）。
- `torch` 是 **CPU 版**（`+cpu`，无 CUDA）：不要写 `.cuda()` / `device="cuda"`，也不要假设有 GPU 显存。
- 模型权重在 `backend/data/models/`，实测 4.3GB（`bge-m3/pytorch_model.bin` 2.16GB + `onnx/model.onnx_data` 2.16GB 副本），已被 gitignore；删掉要重新下载，单测/集成测试一律不加载它。
- `pytest` 配置在 `pyproject.toml`：`testpaths = ["tests"]`、`asyncio_mode = "auto"`（async 用例不需要 `@pytest.mark.asyncio`）。

## 常用命令

```bash
# 后端：必须在 backend/ 下执行，否则 app 包不可导入
# 先激活项目 venv（在项目父目录，不在 backend/ 下）：E:\agent_code\.venv
E:/agent_code/.venv/Scripts/Activate.ps1
cd backend
python -m pip install <pkg>        # 装依赖；本机无 poetry，见「Python 环境」
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --reload

python -m pytest tests -q                            # 33 个用例，约 6s
python -m pytest tests/integration/test_db.py -q     # 单个文件
ruff check .                                         # 配置在 backend/pyproject.toml
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

- 本项目在外网环境开发，可直接访问外网并安装第三方包（pip / npm 等，本机没有 poetry）。优先复用成熟库，避免重复造轮子。
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
- 换 embedding 模型/维度/块大小 = 新建 `chunks__{index_version}` + 删库重灌（没有 rebuild 脚本）+ 重标阈值；启动时维度不一致会拒绝启动，不能只改配置项。
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

## 测试代码

- **测试重心跟着「项目定位」走**：优先覆盖 agent 核心——意图识别、查询改写、双路召回与 RRF、rerank 归一、三类守卫、引用校验、拒答/部分回答判定、预算截断、轨迹落库。外围（脚本参数、配置读取）不写测试。
- **测试与业务代码必须分文件**：测试只写在 `backend/tests/**`，禁止在 `app/**`、`scripts/**` 里定义 `test_*` 函数、断言自测块，或写 `run_xxx_test()` 之类的"顺手跑一下"入口。要验证某个模块，就在 `tests/` 下建同名测试文件，并与业务代码同一个提交。
- **生产代码里不要有"测试形状"的东西**：禁止 `app/**` 内嵌测试脚本源码字符串（`-c` 大段代码）、禁止用 `assert` 当校验（`python -O` 会剥离）、禁止 docstring 写"测试用"然后只有测试调用。
- 目录分工：`tests/unit/` 放纯函数/纯逻辑（不碰 DB、Chroma、网络、模型）；`tests/integration/` 放走 `TestClient` 或真实 SQLite/Chroma 的链路；`tests/e2e/`、`tests/evals/` 已占位，暂时留空。
- 命名与被测模块一一对应：文件 `test_<模块>.py`，函数 `test_<行为>()`；不要一个文件塞多个不相关模块的用例。
- 共享夹具只放 `tests/conftest.py`（现有 `client` 夹具已负责临时数据目录 + 屏蔽模型加载）。不要在单个测试文件里各写一套建库、建 app 的逻辑。
- 隔离是硬要求：用 `tmp_path` + `monkeypatch.setenv("APP_DATA_DIR", ...)`，绝不读写 `backend/data/app.db`。需要向量的用例用假 stack（参考 `tests/integration/test_indexing.py`），不要加载 4.3GB 权重、不要触网下载。
- 不新增 `pytest.ini` / `setup.cfg` / `tox.ini`；pytest 与 ruff 配置只写在 `backend/pyproject.toml`。
- `scripts/*.py` 里的 `if __name__ == "__main__":` 是命令行入口，不是测试，不能拿它替代测试用例。

## 实现约定

- Python 目标 3.11+，文件加 `from __future__ import annotations`，ruff `line-length = 100`。
- 文档、注释、UI 文案用中文；术语沿用设计文档（语料 / 片段 / 引证档 / 轨迹 / 润色）。
- `agent/contracts.py` 的 `AnswerType` 是脚手架占位，与 DDL 的 `message.answer_type`（`fact|partial|refuse|boundary|report_outline`）不一致；动到该文件时以 DDL 为准修正，不要扩散旧枚举。
- 前端颜色与字体只用 `src/styles/tokens.css` 的朱墨变量，不要新造色值；请求统一走 `/api`。
- 实施顺序按「项目定位」的核心项排：检索质量（分块 + 双路召回 + RRF + rerank）→ 8 阶段编排 + 工具边界 + 守卫 → 轨迹与可观测 → 评测集。不要先铺管理台、UI、运维脚本——那些是外围，能省则省。
