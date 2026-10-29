# 中国县市 AI 智能体 · 详细设计

> **文档性质**：设计方案（Detailed Design）  
> **配套文档**：《架构设计-中国县市AI智能体-v1.0.md》（下文简称"架构文档"）  
> **上游依据**：《中国县市AI智能体-需求分析报告-v1.2.md》、《县志AI原型-v1.0.html》

| 项    | 内容                                                                           |
| ---- | ---------------------------------------------------------------------------- |
| 版本   | v1.0                                                                         |
| 日期   | 2026-10-29                                                                   |
| 覆盖范围 | 技术选型与职责划分、主要功能流程、接口全量定义、检索链路（含向量化与重排）、13 条实现要点、前端落地、追溯表                      |
| 不覆盖  | 表结构 DDL（见架构文档附录 A）、部署运维手册、评测集题目                                              |
| 编号约定 | `D-xx` 全局裁定、`C-xx` 需求矛盾裁定、`AS-xx` 设计假设，**与架构文档同一套，不重复定义**；本文新增 `A-xx` 为实现层假设 |

---

## 1 核心技术选型与职责划分

### 1.1 技术栈明细

| 层      | 组件                          | 版本          | 职责                                                | 替代方案的代价                                          |
| ------ | --------------------------- | ----------- | ------------------------------------------------- | ------------------------------------------------ |
| 语言     | Python                      | 3.11+       | 全部后端                                              | 3.9 缺 `X \| None` 语法与 `asyncio.TaskGroup`，会写得更啰嗦 |
| Web 框架 | FastAPI                     | ≥0.110      | 路由、依赖注入、校验、OpenAPI                                | Flask 需自己做异步与 SSE                                |
| ASGI   | uvicorn                     | ≥0.29       | 单进程跑 app                                          | 多 worker 会同时破坏 4 个组件（架构文档 D-1）                   |
| 校验     | Pydantic                    | v2          | 请求/响应 DTO、`extra='forbid'` 挡掉伪造字段                 | 手写校验必然漏字段                                        |
| 编排     | LangChain                   | ≥0.2        | `Runnable` 串联 8 阶段、callback 打点、输出解析               | 裸写会重复实现重试/流式/提示词管理                               |
| 关系库    | SQLite                      | ≥3.40       | 24 张表，WAL                                         | PostgreSQL 多一个运维对象                               |
| 向量库    | Chroma                      | ≥0.4        | `PersistentClient`，每索引版本一个 collection             | FAISS 无法下推过滤（违反 FR-D04）                          |
| 向量模型   | **bge-m3**                  | revision 锁定 | 1024 维、8192 ctx、本地 CPU，`sentence-transformers` 加载 | 理由与备选见架构文档 1.5；`bge-large-zh-v1.5` 会截断 800 字降级块  |
| 重排模型   | **bge-reranker-v2-m3**      | revision 锁定 | cross-encoder，20 候选约 150–400ms                    | 关闭 rerank 则阈值语义变为余弦（D-5）                         |
| 全文检索   | FTS5                        | 内置          | BM25 通路，外部内容表                                     | `LIKE` 无排序、无 BM25                                |
| 中文处理   | jieba                       | ≥0.42       | 写入侧分词（`cut_for_search`）                           | 不分词则 BM25 对 CJK 完全失效                             |
| 繁简     | opencc-python-reimplemented | ≥0.1        | 检索侧归一（展示侧保留繁体）                                    | 自建映射表覆盖率不足                                       |
| 密码     | argon2-cffi                 | ≥23         | argon2id                                          | bcrypt 需 cost≥12 才等效，且 72 字节截断                   |
| JWT    | PyJWT                       | ≥2.8        | access/refresh 签发校验                               | —                                                |
| PDF    | PyMuPDF                     | ≥1.24       | 文本层提取 + 逐页字符数判定                                   | pdfplumber 慢、内存高                                 |
| 导出     | python-docx                 | ≥1.1        | DOCX（需手设 `w:eastAsia`）                            | pandoc 依赖外部二进制                                   |
| 前端     | Vue 3 + Vite                | ≥3.4 / ≥5   | SPA，7 个视图                                         | 原生 JS 在润色 diff 页会失控                              |
| 前端状态   | Pinia                       | ≥2.1        | 会话/流式/角色/取证栏                                      | 全局事件总线难调试                                        |
| 测试     | pytest + httpx              | —           | 单测、e2e、越权用例                                       | —                                                |

### 1.2 职责划分：一件事只有一个负责人

| 关注点    | 唯一负责人                             | 其他人怎么做                  | 违反的后果                                |
| ------ | --------------------------------- | ----------------------- | ------------------------------------ |
| 算可见范围  | `chat_service.build_scope()`      | Agent 只能接收 `scope`，不能构造 | 出现第二条"算权限"的代码路径，必然与第一条不一致            |
| 生成 SQL | `app/repo/**`                     | 其他层调 repo 方法            | 事务边界失控、软删条件漏加                        |
| 判断能否检索 | `rag/retrievers`（用入参 scope）       | 不得自行查库判断                | 过滤后置，越权召回                            |
| 决定答不答  | `agent/stages/s4_judge.py`        | 生成阶段不得"看情况再说"           | 拒答标准随模型波动，无法评测                       |
| 引用有效性  | `agent/guards/citation_check.py`  | 提示词只负责"尽量挂引用"           | 靠模型自述，幻觉率不可控                         |
| 状态机流转  | `corpus_service.transition()`     | 任何地方不得直接 UPDATE status  | 出现 `parse_failed → published` 这类非法路径 |
| 任务去重   | `task.idempotency_key` 唯一索引       | 调用方不必自己判重               | 重复解析、重复发布                            |
| 轨迹结构   | `TraceCollector`                  | 各阶段只 add span/event，不写库 | 每阶段写库，主链路被拖慢                         |
| 版本号    | `app_config` + `prompts/registry` | 代码里不得硬编码 `p_v3`         | 事后无法复现                               |

### 1.3 LangChain 用在哪、不用在哪

这是本文最需要明确的一节。LangChain 的能力面很宽，但**这个项目只需要它的四件事**，其余引入的都是负担。

#### 用

| 能力                                  | 用在哪                                  | 具体写法                                                                           | 为什么值得用                                         |
| ----------------------------------- | ------------------------------------ | ------------------------------------------------------------------------------ | ---------------------------------------------- |
| `BaseChatModel` 适配                  | `llm/factory.py`                     | `init_chat_model(model, model_provider=...)`，配 `with_fallbacks([small_model])` | 换模型只改配置；降级链一行写完                                |
| `Embeddings` 接口                     | `rag/embeddings.py`                  | 实现 `embed_documents` / `embed_query`，繁转简 + 批 16 + L2 归一 + 指数退避                 | 与 Chroma、检索器解耦；换模型只改这一层与索引版本                   |
| `BaseRetriever`                     | `rag/retrievers/vector.py`、`bm25.py` | 继承并实现 `_get_relevant_documents`，返回 `list[Document]`                            | 两路召回签名一致，才能做 RRF 融合；`Document.metadata` 是天然的载体 |
| `ChatPromptTemplate`                | `agent/prompts/`                     | Jinja2 模板文件 + `prompt_version` + `sha256`                                      | 变量显式声明，缺变量在构造期就报错                              |
| `RunnableSequence`                  | `agent/pipeline.py`                  | `s0 \| s1 \| ... \| s7`，每段是 `RunnableLambda`                                   | 8 阶段的数据流一目了然；`.astream()` 天然支持流式               |
| `BaseCallbackHandler`               | `llm/callbacks.py`                   | 监听 `on_chat_model_start/end`、`on_retriever_end`，转 `AgentEvent`                 | 模型调用的 token 与耗时不用手写包裹                          |
| `PydanticOutputParser`              | `agent/stages/s5_generate.py`        | 解析引证档 JSON → `AnswerPayload`                                                   | 结构不合法立即失败，不进下游                                 |
| `RunnableWithMessageHistory` **不用** | —                                    | 自管会话（见下）                                                                       | 记忆要落库、要分层、要审计                                  |

#### 不用（且必须写明理由，否则后人会加回来）

| 不用                                            | 为什么                                                                                                                   |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `AgentExecutor` / `create_tool_calling_agent` | 由模型自主决定调哪个工具、调几次，**预算不可控**。需求要求 ≤6 次调用、≤2 轮扩检、25s 超时，这些在开放式 agent 里只能靠提示词"请求"它遵守。而且轨迹会随模型决策而变形，无法保证 5–9 个 span        |
| LangGraph                                     | 本期是线性 8 阶段 + 一层"不足则扩检一次"的循环，用状态图属于过度设计。轨迹/回放由自家三表承担，不需要 checkpointer                                                  |
| `ConversationBufferMemory` 等 Memory 类         | 记忆是三层（近 6–8 轮原文 + ≤300 字摘要 + 记忆槽），且必须落库供审计与"跨会话不串"。Memory 类的生命周期绑在进程内，重启即丢                                            |
| 用 `VectorStore` 抽象做检索主路径                      | 我们需要"两路召回共用同一 scope + RRF + rerank + 阈值"，`VectorStore` 的 `similarity_search` 表达不了这条链路。Chroma 只用其原生 `collection.query` |
| `RecursiveCharacterTextSplitter` 做首选分块        | 县志是条目体，按字数硬切会把一条记载拦腰截断。它只作为"三级识别失败"的降级兜底（800 字 + 100 重叠）                                                              |
| `LLMChain` / 旧版 `Chain`                       | 已被 LCEL 取代，混用两套写法会让 pipeline 无法统一 astream                                                                             |

#### 一句话概括

> **LangChain 在这里承担"模型适配 + 检索器接口 + 提示词 + 串行编排 + 打点"，不承担"决策"。**  
> 决策由 8 个确定性阶段 + DR-01~15 规则完成——这是本项目可复现的前提。

### 1.4 依赖清单

```toml
# pyproject.toml（节选，仅列直接依赖）
[tool.poetry.dependencies]
python = "^3.11"
fastapi = "^0.110"
uvicorn = { version = "^0.29", extras = ["standard"] }
pydantic = "^2.6"
pydantic-settings = "^2.2"

langchain-core = "^0.2"
langchain-openai = "^0.1"          # 或其他 provider 适配包，换厂商只换这一行
chromadb = "^0.4"
sentence-transformers = "^2.7"     # bge-m3 与 bge-reranker-v2-m3 的加载与推理
torch = "^2.2"                     # CPU 版即可；GPU 可选
tiktoken = "^0.6"                  # 仅用于 token 估算校准，不在热路径

argon2-cffi = "^23.1"
pyjwt = "^2.8"
python-multipart = "^0.0.9"

pymupdf = "^1.24"
python-docx = "^1.1"
jieba = "^0.42"
opencc-python-reimplemented = "^0.1"
python-magic = "^0.4"              # MIME 校验

[tool.poetry.group.dev.dependencies]
pytest = "^8.0"
pytest-asyncio = "^0.23"
httpx = "^0.27"
ruff = "^0.3"
import-linter = "^2.0"             # 架构不变量检查
```

---

## 2 接口设计

### 2.1 统一约定

| 项    | 约定                                                                                     |
| ---- | -------------------------------------------------------------------------------------- |
| 前缀   | `/api`；管理端统一 `/api/admin/**`                                                           |
| 鉴权   | `Authorization: Bearer <access>`；仅 `register`/`login`/`refresh`/`exports/{token}` 免鉴权  |
| 身份   | 请求 DTO **不含** `role`/`user_id` 字段，Pydantic `extra='forbid'` 使伪造字段直接 422（需求 AT-02 的工程化） |
| 错误体  | `{"code":"EX-07","message":"...","request_id":"...","detail":{}}`；`detail` 不回显语料内容     |
| 分页   | `?page=1&size=20`（上限 100），或游标 `?before_seq=`；响应含 `{items, total, page, size}`          |
| 幂等   | 写操作支持 `Idempotency-Key` 头；异步操作用业务幂等键（见各端点）                                             |
| 时间   | 请求/响应一律 ISO-8601 UTC 毫秒；展示层转 +08:00                                                    |
| 异步   | 返回 `202 {task_id, ...}`，客户端轮询 `GET /api/tasks/{id}`                                    |
| 版本冲突 | 带 `version` 的更新，冲突返回 `409 EX-15`                                                       |


### 2.2 端点总表

**共 54 个**（计划稿记 53 个，因 FR-C02「用户主动停止」补 1 个取消端点）。

#### auth（6）

| # | 方法与路径                     | 用途                   | 权限      | 幂等                   | 异步 |
| - | ------------------------- | -------------------- | ------- | -------------------- | -- |
| 1 | `POST /api/auth/register` | 注册（一律 user）          | 公开      | 用户名唯一 → 409          | 否  |
| 2 | `POST /api/auth/login`    | 登录                   | 公开      | 否                    | 否  |
| 3 | `POST /api/auth/refresh`  | 换 access（轮换 refresh） | refresh | 重用 → 吊销整族            | 否  |
| 4 | `POST /api/auth/logout`   | 登出                   | 登录      | 是                    | 否  |
| 5 | `GET /api/auth/me`        | 当前身份与可见县             | 登录      | 是                    | 否  |
| 6 | `PATCH /api/auth/me`      | 改密                   | 登录      | 否（`token_version+1`） | 否  |

关键字段：`register{username, password, invite_code?}` → `{user_id, role}`；`login{username, password}` → `{access_token, refresh_token, expires_in, role}`；`me` → `{user_id, username, role, regions[{id,name,doc_count}]}`。

#### regions（2）

| # | 方法与路径                   | 用途                 | 权限 |
| - | ----------------------- | ------------------ | -- |
| 7 | `GET /api/regions`      | 县列表（含有无语料，驱动顶栏选择器） | 登录 |
| 8 | `GET /api/regions/{id}` | 县详情 + 已发布书目        | 登录 |

`GET /api/regions` 响应：`[{id, name, era_note, doc_count, chunk_count, has_published}]`。原型顶栏三个选项（扶风县 4 卷/眉县解析中/岐山县 0 段）由 `doc_count` 与 `has_published` 组合渲染。

#### corpus（7，全部 admin）

| #  | 方法与路径                                                   | 用途                       | 幂等键                           | 异步  |
| -- | ------------------------------------------------------- | ------------------------ | ----------------------------- | --- |
| 9  | `POST /api/admin/corpus/documents`                      | 上传（multipart）            | `dup_key` + `Idempotency-Key` | 是   |
| 10 | `GET /api/admin/corpus/documents`                       | 列表（筛状态/县/关键词）            | 是                             | 否   |
| 11 | `GET /api/admin/corpus/documents/{doc_id}`              | 详情 + 发布就绪度               | 是                             | 否   |
| 12 | `PATCH /api/admin/corpus/documents/{doc_id}`            | 改元数据（乐观锁）                | `version`                     | 否   |
| 13 | `DELETE /api/admin/corpus/documents/{doc_id}`           | 删除（仅 draft/parse_failed） | 是                             | 否   |
| 14 | `POST /api/admin/corpus/documents/{doc_id}/transitions` | **状态机唯一入口**              | `action + version`            | 部分是 |
| 15 | `GET /api/admin/corpus/versions/{version_id}`           | 版本正文（分页）+ 段落索引           | 是                             | 否   |

|                                              |           |           |            |                                                          |
| -------------------------------------------- | --------- | --------- | ---------- | -------------------------------------------------------- |
| `POST .../transitions` 请求：\`{action: 'parse' | 'publish' | 'offline' | 'rollback' | 'reindex', payload: {checklist?, target_version_id?}}\`。 |

```
action=parse     → draft|parse_failed → parsing，返回 202 {task_id}
action=publish   → proofreading|offline → published，校验 7 项清单 + 未确认段=0，200 {status}
action=offline   → published → offline，同事务置 chunk.is_searchable=0，200
action=rollback  → published → proofreading，target_version_id 必填，200
action=reindex   → 任意已发布状态，202 {task_id}
非法转移 → 409 EX-11（例：draft 直接 publish）
```

`GET .../documents/{doc_id}` 响应含 `publish_readiness`：

```json
{
  "unconfirmed_segments": 17,
  "sensitive_segments": 4,
  "no_text_pages": 3,
  "checklist": [
    {"no": 1, "text": "润色逐段已确认，无未确认段落", "type": "system", "passed": false},
    {"no": 2, "text": "所有「需重点复核」段落已人工读过", "type": "manual", "passed": false},
    {"no": 6, "text": "随机问 3 个问题，引用均能定位", "type": "manual", "passed": false}
  ]
}
```

`type=system` 的项由后端计算并禁止人工勾选（防"闭眼全勾"）。

#### polish（3，admin）

| #  | 方法与路径                                                    | 用途         | 幂等                        | 异步 |
| -- | -------------------------------------------------------- | ---------- | ------------------------- | -- |
| 16 | `POST /api/admin/corpus/documents/{doc_id}/polish`       | 启动润色       | `doc_id + prompt_version` | 是  |
| 17 | `GET /api/admin/corpus/versions/{version_id}/segments`   | 逐段 diff 列表 | 是                         | 否  |
| 18 | `PATCH /api/admin/corpus/versions/{version_id}/segments` | 批量确认/驳回/编辑 | `segment_id + 目标状态`       | 否  |

|                                                      |          |                                                                             |
| ---------------------------------------------------- | -------- | --------------------------------------------------------------------------- |
| `PATCH` 请求：\`{items: [{segment_id, action: 'confirm' | 'reject' | 'edit', edited_text?}]}`，响应 `{confirmed, pending, rejected, can_publish}\`。 |

```
校验：edited_text 若引入原文不存在的年份/人名/地名 → 422 EX-20 并返回违禁片段
     （不静默接受，也不整批失败；合法项照常生效，非法项逐条返回）
can_publish = (pending == 0 && rejected == 0 && 自查清单 7 项全过)
```

#### chat（5）

| #  | 方法与路径                                           | 用途       | 权限                  | 幂等                         | 异步 |
| -- | ----------------------------------------------- | -------- | ------------------- | -------------------------- | -- |
| 19 | `POST /api/chat/stream`                         | 主问答（SSE） | 登录（preview 须 admin） | `X-Request-Id` 10s 去重      | 流式 |
| 20 | `GET /api/chat/stream/{message_id}`             | 断流重放     | 登录 + 归属             | 是                          | 流式 |
| 21 | `GET /api/chat/messages/{message_id}`           | 单条消息完整契约 | 登录 + 归属             | 是                          | 否  |
| 22 | `POST /api/chat/messages/{message_id}/feedback` | 点赞/点踩    | 登录 + 归属             | `(message_id, user_id)` 唯一 | 否  |
| 23 | `POST /api/chat/messages/{message_id}/cancel`   | 主动停止生成   | 登录 + 归属             | 是                          | 否  |

`POST /api/chat/stream` 请求体：

```json
{ "session_id": "s_01J...", "region_id": "610324",
  "question": "本县有哪些古建筑？", "mode": "answer", "preview": false }
```

`preview=true` 仅 admin 生效；user 传 true 时**降级为 false 并写日志**（需求 4.7.2，不报错不提示，避免探测）。

`GET /api/chat/messages/{id}` 返回与输出契约一致的 10 字段（见 3.2 的 `AnswerPayload`）：

```json
{
  "answer_type": "fact",
  "region": {"id": "610324", "name": "扶风县", "era_note": "据清乾隆本"},
  "direct_answer": "志书中有明确记载的古建筑共 3 处……",
  "explanation": "……",
  "citations": [
    {"no": 1, "doc": "扶风县志", "volume": "卷二·建置", "chunk_id": "c_10231",
     "quote": "法门寺在县北二十里……", "supports": "第 1 句：三处记载的数量与方位",
     "page": "p.46", "ocr": false}
  ],
  "confidence": "medium",
  "caveats": ["两说并存：始建年代有东汉与隋代两说，未作判定"],
  "followups": ["法门寺塔在志书里记载过几次重修？"],
  "refusal": null,
  "meta": {"trace_id": "tr_01J...", "model": "…", "prompt_version": "p_v3",
           "index_version": "idx_20261029", "token_in": 3120, "token_out": 486,
           "elapsed_ms": 6180}
}
```

#### sessions（5）

| #  | 方法与路径                             | 用途          | 幂等 | 说明                                      |
| -- | --------------------------------- | ----------- | -- | --------------------------------------- |
| 24 | `GET /api/sessions`               | 会话列表（按时间分组） | 是  | 分页，排除软删                                 |
| 25 | `POST /api/sessions`              | 新建          | 是  | 超 200 → 409 EX-10                       |
| 26 | `PATCH /api/sessions/{id}`        | 重命名 / 切县    | 否  | 切县返回 `switch_notice`（原型提示"之前的回答基于 A 县"） |
| 27 | `DELETE /api/sessions/{id}`       | 软删（7 天后物理清） | 是  | 写 `purge_after`                         |
| 28 | `GET /api/sessions/{id}/messages` | 消息列表（含引用）   | 是  | 游标 `before_seq`                         |

#### chunks（1）

| #  | 方法与路径                        | 用途                  | 权限                                              |
| -- | ---------------------------- | ------------------- | ----------------------------------------------- |
| 29 | `GET /api/chunks/{chunk_id}` | 引用回链（原文 + ±200 上下文） | 登录；**chunk 所属 doc 必须 ∈ visible_doc_ids，否则 404** |

响应：`{chunk_id, doc, volume, category, page, ocr, quote, before, after, span_start, span_end}`。

**这是取证栏的唯一数据源**。原型里取证栏给"卷次 / 页码 / 片段 id / 提取方式（OCR 或文本层）"，全部来自这里；`before/after` 即"展开上下文 ±200 字"。

#### reports（8）

| #  | 方法与路径                                         | 用途             | 幂等                         | 异步     |
| -- | --------------------------------------------- | -------------- | -------------------------- | ------ |
| 30 | `GET /api/reports/templates`                  | 3 个模板定义        | 是                          | 否      |
| 31 | `POST /api/reports/outlines`                  | 出大纲（Ask first） | 是                          | 否（≤3s） |
| 32 | `POST /api/reports`                           | 确认大纲并生成        | `report_id + outline_hash` | 是      |
| 33 | `GET /api/reports`                            | 我的报告           | 是                          | 否      |
| 34 | `GET /api/reports/{id}`                       | 详情（含各节状态）      | 是                          | 否      |
| 35 | `POST /api/reports/{id}/sections/{seq}/retry` | 单节重试           | `id + seq + attempt`       | 是      |
| 36 | `POST /api/reports/{id}/exports`              | 导出 MD/DOCX     | 同 fmt 60s 复用               | 是      |
| 37 | `GET /api/exports/{token}`                    | 下载（24h）        | 是                          | 否      |

模板响应（章节文案为设计补充，见 8.3 A-04）：

```json
[{"key":"history_evolution","name":"县域历史沿革","est_words":3600,"sections":[
   {"seq":1,"heading":"建置沿革","points":["历代隶属变化","三次改隶","与邻县边界"]},
   {"seq":2,"heading":"城池与公署","points":["城垣规模","城门数目","县署位置与兴废"]},
   {"seq":3,"heading":"祠祀与寺观","points":["坛庙始建","寺观重修","两说并列"]},
   {"seq":4,"heading":"人物与科第","points":["进士名单","仅存姓名者单列"]},
   {"seq":5,"heading":"艺文与碑记","points":["碑记存目","残缺字数"]}]}]
```

`GET /api/reports/{id}` 响应含 `sections[{seq, heading, status, error_code, content_md}]`。单节失败时 `status='failed'` + `error_code='low_score'`，前端渲染朱色段落 + "重试该节"（原型表现一致）。

#### traces（3，admin）

| #  | 方法与路径                                       | 用途                                                       | 幂等                       |
| -- | ------------------------------------------- | -------------------------------------------------------- | ------------------------ |
| 38 | `GET /api/admin/traces`                     | 列表/筛选（`message_id`/`trace_id`/`answer_type`/`status`/时间） | 是                        |
| 39 | `GET /api/admin/traces/{trace_id}`          | 详情 + 可选 `compare_to`                                     | 是                        |
| 40 | `POST /api/admin/traces/{trace_id}/to-eval` | 转评测样本                                                    | `trace_id + dataset_key` |

`GET .../traces/{trace_id}` 响应结构：

```json
{
  "trace": {"trace_id":"tr_01J...","question_text":"法门寺塔到底是东汉还是隋代建的？",
            "answer_type":"refuse","status":"degraded","total_ms":5190,
            "model":"…","prompt_version":"p_v3","index_version":"idx_20261029",
            "span_count":8,"size_bytes":12480},
  "spans": [{"seq":3,"name":"retrieve","duration_ms":3120,"status":"ok","budget_ms":900,
             "input_digest":"query=法门寺塔始建年代 region=610324 top_k=20","output_digest":"chunks=9 top1=0.51"}],
  "events": [{"span_id":"sp_04","type":"decision","decision":"insufficient",
              "reason_code":"low_score",
              "detail":{"top1":0.51,"threshold":0.62,"hits":9,"categories":3}}],
  "diff": null
}
```

**响应里不含片段正文**：`hit_chunk_ids` 只给 id 与分数（原型"命中片段"区块的说明文字即此约束）。要看正文必须走 `GET /api/chunks/{id}`，受同样的权限校验——轨迹页不能成为绕过权限的语料出口。

#### admin（9）

| #  | 方法与路径                             | 用途                          |
| -- | --------------------------------- | --------------------------- |
| 41 | `GET /api/admin/users`            | 用户列表                        |
| 42 | `PATCH /api/admin/users/{id}`     | 禁用/启用（**不提供改角色**，见 AS-03）   |
| 43 | `GET /api/admin/invites`          | 邀请码列表                       |
| 44 | `POST /api/admin/invites`         | 生成邀请码                       |
| 45 | `PATCH /api/admin/invites/{code}` | 停用/延期                       |
| 46 | `GET /api/admin/usage`            | 用量与成本（日/用户/模型）              |
| 47 | `GET /api/admin/feedback`         | 负反馈列表                       |
| 48 | `GET /api/admin/config`           | 全量配置                        |
| 49 | `PUT /api/admin/config/{key}`     | 改配置（乐观锁 `expected_version`） |

`PATCH /api/admin/users/{id}` 请求只接受 `{status}`；禁用时 `token_version+1` 使所有 access 立即失效。**管理员数量 ≤2 由初始化脚本保证，运行时无提权入口**。

#### eval（4）+ tasks（1）

| #  | 方法与路径                           | 用途                                     | 异步 |
| -- | ------------------------------- | -------------------------------------- | -- |
| 50 | `GET /api/admin/eval/cases`     | 样本列表                                   | 否  |
| 51 | `POST /api/admin/eval/cases`    | 新建/更新样本（`dataset_key + question_hash`） | 否  |
| 52 | `POST /api/admin/eval/runs`     | 启动评测（6 组实验的设备）                         | 是  |
| 53 | `GET /api/admin/eval/runs/{id}` | 运行结果与指标                                | 否  |
| 54 | `GET /api/tasks/{task_id}`      | 任务状态（所有异步的统一出口）                        | 否  |

`POST /api/admin/eval/runs` 请求体的 `config` 即"实验台账"的配置列：

```json
{"dataset_key":"qa_50","dataset_version":"v1",
 "config":{"model":"…","prompt_version":"p_v3","index_version":"idx_20261029",
           "retrieval":{"chunk_size":400,"hybrid":true,"rerank":true,"top_k":20,"threshold":0.62}}}
```

### 2.3 SSE 协议

#### 2.3.1 为什么是 POST

`EventSource` 只能 GET，问题文本会进 URL（被 Nginx 访问日志、代理、浏览器历史记录）。因此：

```
前端：fetch('/api/chat/stream', {method:'POST', headers:{Authorization, Accept:'text/event-stream'}, body})
      → response.body.getReader() → TextDecoder → 按 \n\n 切帧 → 解析 event:/data:/id:
```

代价：要自己处理帧边界（一个 chunk 可能半个帧）、重连、心跳超时。实现见 6.5。

#### 2.3.2 事件表

| 事件           | data                                                                             | 时机         | 前端动作                       | 对应原型           |
| ------------ | -------------------------------------------------------------------------------- | ---------- | -------------------------- | -------------- |
| `meta`       | `{trace_id, message_id, model, prompt_version, index_version, preview}`          | ≤50ms，首个   | 建消息骨架，badge「检索中」           | 生成中骨架          |
| `stage`      | `{seq, name, status:'start'\|'ok'\|'degraded'\|'skipped'\|'failed', ms?, note?}` | 每阶段首尾各一次   | 阶段点动画（3–4 个点）              | `.stages` 阶段序列 |
| `citation`   | `{no, chunk_id, doc, volume, quote, supports, page, ocr}`                        | 5a 完成后     | 渲染引用卡 + 页边编号               | `cite-card` 组  |
| `delta`      | `{text}`                                                                         | 5b 逐 token | 追加正文                       | 流式渲染           |
| `caveat`     | `{text, kind:'conflict'\|'suspicion'}`                                           | 冲突/存疑      | 常驻提示条                      | 朱框「两说并存」       |
| `correction` | `{action:'drop_claim'\|'downgrade', text, reason_code}`                          | 阶段 6 降级    | 该句标「未核实」                   | 编号 `is-void` 态 |
| `refuse`     | `{reason, hint, nearby:[{volume,text,score}]}`                                   | 拒答分支       | 切拒答样式 + 相近原文               | `nearby` 卡     |
| `usage`      | `{token_in, token_out, elapsed_ms}`                                              | 生成结束       | 仅埋点，不展示                    | 摘要行耗时          |
| `done`       | `{answer_type, confidence, message_id, ttft_ms, total_ms, followups[]}`          | 必发         | 解锁输入、渲染追问                  | 反馈条 + 追问       |
| `resync`     | `{message_id}`                                                                   | 重放缓冲过期     | 改拉 `GET /messages/{id}` 全量 | —              |
| `error`      | `{code, message, retryable}`                                                     | 致命错误       | 显示重试                       | EX 文案          |
| `ping`       | `{}`                                                                             | 每 15s      | 忽略（仅防代理超时断连）               | —              |

`id:` 字段为单调递增 `seq`，客户端记录最后一个，重连时作为 `Last-Event-ID` 发送。

#### 2.3.3 断流与停止的区别（FR-C02）

这两件事经常被混为一谈，必须分开：

| 场景          | 服务端行为                                  | 消息状态       | 用户回来看到          |
| ----------- | -------------------------------------- | ---------- | --------------- |
| 网络断开 / 关标签页 | **继续生成完**，`asyncio.shield()` 保护落库      | `complete` | 重连或刷新后完整答案      |
| 点「停止」       | 协作式取消（阶段边界检查 `cancel_requested`），保留已生成 | `partial`  | 已生成部分 + 「未完成」标记 |
| 模型超时 25s    | 中断，保留已生成                               | `partial`  | 同上 + 超时说明       |

判断依据：只有显式 `POST .../cancel` 才置 `cancel_requested`；`request.is_disconnected()` 只停止推送，不停止生成。理由是"用户只是切换了标签页"远比"用户放弃了这个答案"常见。

### 2.4 错误码映射

需求 `EX-01`~`EX-25` 是行为清单，这里给它 HTTP 语义。**保留原编号**以便回溯。

| 编号    | 含义          | HTTP              | 可重试 | 用户可见文案                  |
| ----- | ----------- | ----------------- | --- | ----------------------- |
| EX-01 | 扩展名不支持      | 400               | 否   | 支持 PDF / TXT / Markdown |
| EX-02 | MIME 与扩展名不符 | 400               | 否   | 文件类型与后缀不一致              |
| EX-03 | 文件超 50MB    | 413               | 否   | 单文件不超过 50MB             |
| EX-04 | 加密 PDF      | 422               | 否   | 该 PDF 有密码保护，请先解除        |
| EX-05 | 乱码率超阈值      | 422               | 是   | 识别质量过低，建议重传清晰版本         |
| EX-06 | 无文本层且未配 OCR | 422               | 是   | 共 N 页无文本层，可补录或跳过        |
| EX-07 | 未登录/令牌失效    | 401               | 是   | 登录已过期                   |
| EX-08 | 权限不足        | 403               | 否   | 无权限执行该操作                |
| EX-09 | 资源不存在或无权访问  | 404               | 否   | 内容不存在                   |
| EX-10 | 会话数超限（200）  | 409               | 否   | 会话已达上限，请先删除             |
| EX-11 | 非法状态流转      | 409               | 否   | 当前状态不允许该操作              |
| EX-12 | 邀请码无效/用尽    | 422               | 否   | 邀请码无效或已使用               |
| EX-13 | 登录失败次数超限    | 423               | 是   | 账号已锁定 15 分钟             |
| EX-14 | 刷新令牌重用      | 401               | 否   | 登录状态异常，请重新登录            |
| EX-15 | 乐观锁冲突       | 409               | 是   | 内容已被修改，请刷新后重试           |
| EX-16 | 单节生成失败      | 200（节内标记）         | 是   | 第 3 节生成失败，可单独重试         |
| EX-17 | 导出失败        | 500               | 是   | 导出失败，已重试 1 次            |
| EX-18 | 生成中断        | 200（流内 `done`）    | 是   | 回答生成超时，已保留部分内容          |
| EX-19 | 引用校验不通过     | 200（`correction`） | 否   | 此条未找到对应出处               |
| EX-20 | 润色越界        | 422               | 否   | 该段补写了原文没有的内容            |
| EX-21 | 重复上传        | 409               | 否   | 已存在同版本，可选择新增版本          |
| EX-22 | 任务超时        | 504               | 是   | 处理超时，请重试                |
| EX-23 | 轨迹写入失败      | —                 | —   | 不影响用户（仅告警）              |
| EX-24 | 轨迹已过保留期     | 410               | 否   | 该轨迹已过 30 天保留期           |
| EX-25 | 轨迹体积超限      | —                 | —   | 已截断命中列表（内部告警）           |

**注意 EX-16/18/19 的 HTTP 是 200**：它们是"业务性降级"，不是请求失败。用 5xx 表达会让前端把"部分有据"渲染成错误页——这与产品语义相反。

---

## 3 主要功能流程

### 3.1 八阶段总表

| seq | 阶段              | 输入                    | 输出                                       | 预算                     | 失败分支                                | span 必填 digest                      |
| --- | --------------- | --------------------- | ---------------------------------------- | ---------------------- | ----------------------------------- | ----------------------------------- |
| 0   | `auth` 鉴权与装配    | `AgentInput`（含 scope） | 校验通过的 `AgentState`                       | 30ms                   | scope 为空 → `refuse(permission)`，不空跑 | `scope_size, region_id, preview`    |
| 1   | `intent` 意图判定   | 问题、记忆槽                | `qa/report/chitchat/write_request` + 置信度 | 100ms（规则）/900ms（含 LLM） | 未命中规则 → 与 s2 合并一次 LLM               | `intent, conf, path=rule\|llm`      |
| 2   | `rewrite` 查询改写  | 原问题、近 4 轮、记忆槽         | 独立 query + 改写理由                          | 800ms                  | 失败用原问题 + 提示                         | `q_before_len, q_after, changed`    |
| 3   | `retrieve` 检索   | query、scope           | 片段集（5–8 条，含元数据）                          | 900ms                  | 向量路失败 → 纯 BM25；全空 → 拒答分支            | `rounds, hits, top1`                |
| 4   | `judge` 充分性判定   | 片段集、阈值                | `sufficient/insufficient` + 原因码          | 50ms                   | 不足 → 扩检 1 轮 → 仍不足拒答                 | `top1, threshold, hits, categories` |
| 5   | `generate` 生成   | 片段集、约束提示              | 引证档 + 白话解读（流式）                           | TTFT 2.5s              | 超 25s 中断保留已生成                       | `claims, citations, ttft_ms`        |
| 6   | `validate` 输出校验 | 草稿、片段集                | 终稿或降级稿                                   | 150ms                  | 删断言 / 改 `refuse` / 标未核实             | `checks[], dropped, downgraded`     |
| 7   | `deliver` 交付    | 终稿                    | 10 字段 payload + `done`                   | —                      | 断流可重放                               | `events, bytes`                     |

**一条贯穿全表的实现约定**：阶段 0–4、6 的函数**保持同步 `def`**（它们内部有 SQL 读与模型推理，全是阻塞调用，写成 async 反而要处处 to_thread），由编排器整体下沉到专用线程池执行；阶段 5（流式生成）与 7（交付）是 `async`，因为它们直接往 SSE 连接写数据。

```python
# agent/pipeline.py —— 编排器：同步阶段统一走线程池，不嵌套
async def run(c: AgentState, emit) -> AnswerPayload:
    loop = asyncio.get_running_loop()
    c = await asyncio.to_thread(s0_assemble, c.input, c.ctx)   # SQL + 装配
    c = await asyncio.to_thread(s1_intent, c)
    c = await asyncio.to_thread(s2_rewrite, c)
    # 阶段 3 指定检索池（CPU 密集），与 IO 池隔离，见 4.7.6
    c = await loop.run_in_executor(RETRIEVAL_POOL, s3_retrieve, c)
    c = await asyncio.to_thread(s4_judge, c)
    async for ev in s5_generate(c):                            # 流式，不占线程
        await emit(ev)
    c = await asyncio.to_thread(s6_validate, c)
    return await s7_deliver(c, emit)
```

这样做的代价是**每个阶段一次线程切换（约 0.1ms）**，换来的是"任何一个阶段都不可能阻塞事件循环"这条可以静态检查的强保证。反例是只给阶段 3 加 `to_thread`、其余留在事件循环里——那时 `s0` 的一次慢 SQL（可见集查询没走索引）依然能把 20 条 SSE 一起卡住，而问题只在压测时暴露。

### 3.2 阶段 0：鉴权与装配

L2 已完成鉴权，s0 只做**不变量校验**与 span 打点——这是"Agent 不碰 SQL"的实现方式。

```python
# agent/stages/s0_assemble.py
def s0_assemble(inp: AgentInput, ctx: StageCtx) -> AgentState:
    with stage("auth", seq=0, budget_ms=30) as span:
        # 这些是断言，不是判断：任何一条不成立都说明上游有 bug
        assert inp.user_id and inp.role in ("admin", "user")
        assert inp.request_id and inp.trace_id
        assert inp.region_id, "未选县必须在上游追问，不得进入流水线"
        assert inp.budget.max_tool_calls <= 6 and inp.budget.deadline_ms <= 25_000

        if not inp.visible_doc_ids:
            # 空集：不检索、不生成，直接拒答（需求 4.7.4「不空跑」）
            span.event("decision", decision="empty_scope", reason_code="permission")
            raise RefuseStage("permission", hint="该县暂无已发布语料")

        if inp.mode == "preview" and inp.role != "admin":
            # 纵深防御：上游已降级，这里再挡一次
            raise RefuseStage("permission", hint="预览模式仅管理员可用")

        span.digest(input=f"region={inp.region_id} docs={len(inp.visible_doc_ids)} "
                          f"preview={inp.mode=='preview'}",
                    output=f"turn={inp.turn_index} budget={inp.budget.context_tokens}")
        return AgentState(input=inp, ctx=ctx)
```

**关键点**：`visible_doc_ids` 是 `inp` 的字段，s0 只读。Agent 层无法构造它，因为 `AgentInput` 由 `chat_service` 创建，且 L3 不 import 任何能算权限的模块（架构不变量 1）。

### 3.3 阶段 1：意图判定

四分类：`qa`（县志问答）/ `report`（报告生成）/ `chitchat`（闲聊）/ `write_request`（越界写操作）。

```python
# 规则优先，覆盖目标 ≥70%（C-08）
RULES = [
    (r"(加进|写进|录入|帮我改|删掉|发布|下线).{0,10}(县志|语料|这段|文档)", "write_request"),
    (r"(生成|写|做).{0,6}(报告|小传|沿革|概况)", "report"),
    (r"^(你好|在吗|谢谢|今天|天气|吃了吗|讲个笑话)", "chitchat"),
]

def s1_intent(c: AgentState) -> AgentState:
    with stage("intent", seq=1, budget_ms=100) as span:
        q = c.input.question
        for pat, label in RULES:
            if re.search(pat, q):
                c.intent = label
                span.event("decision", decision=f"intent_{label}", detail={"path": "rule"})
                return c
        # 规则未命中：与改写合并为一次调用（C-08），预算 900ms
        merged = merge_intent_and_rewrite(q, c.memory_slots, c.recent_turns)  # LLM, JSON out
        c.intent = merged["intent"]
        c.rewritten = merged.get("rewritten_query")
        span.event("decision", decision=f"intent_{c.intent}",
                   detail={"path": "llm", "conf": merged["confidence"]})
        return c
```

**路由表**（对应 DR-01~DR-04）：

| intent          | 动作                               | answer_type      |
| --------------- | -------------------------------- | ---------------- |
| `chitchat`      | 不检索，直接生成边界话术 + 3 个示例问题           | `boundary`       |
| `write_request` | 不检索，说明需管理员操作，给后台入口（仅 admin 显示链接） | `boundary`       |
| `report`        | 转入报告流程（不在问答流水线里继续）               | `report_outline` |
| `qa`            | 继续 s2 → s3                       | —                |

### 3.4 阶段 2：查询改写

只在 `turn_index > 0` 时触发（首轮没有指代可消解）。三个动作：

```
1) 指代消解："第二个""他""上面那本" → 具体对象
   → 同时产出 rewrite_echo，用于向用户回显「你问的是第 2 位·张念祖」（DR-05、原型 hint-line）
2) 古今词归一："县太爷"→"知县"、"粮房"→"户房"、"城守营"→"城守"
3) 补全省略："那他呢" → "张念祖的科第与任职"
```

失败时（LLM 超时/JSON 不合法）：用原问题检索，末尾追加提示"如需更准确，可补充具体人名或年代"（DR-06）。**不重试第二次**——改写的收益远小于 800ms 的时间成本。

### 3.5 阶段 3：检索

调 T1（混合检索）与 T2（上下文扩展），细节见第 4 章。这里只写编排层：

```python
def s3_retrieve(c: AgentState) -> AgentState:
    with stage("retrieve", seq=3, budget_ms=900) as span:
        q = c.rewritten or c.input.question
        round_no, category = 1, c.infer_category()   # 首轮带类目，二轮去掉
        hits = []
        while round_no <= 2:
            # 本函数整体运行在检索线程池里（pipeline.py），
            # 内部 t1 的 embed_query / rerank 是同步 CPU 调用，无需再 to_thread（4.7.6）
            res = t1_hybrid_search(q=q, scope=c.scope, category=category,
                                   top_k=c.budget.top_k, span=span)
            hits = res.hits
            span.event("tool_call", tool_name="hybrid_search",
                       params_digest=f"q={q} region={c.scope.region_id} "
                                     f"top_k={c.budget.top_k} category={category}",
                       result_count=len(hits), top_score=res.top1,
                       detail={"hit_chunk_ids": res.top10, "round": round_no})
            if res.top1 >= c.threshold or round_no == 2 or not category:
                break
            round_no, category = 2, None      # 放宽：去掉类目限制（DR-08）
            span.status = "degraded"

        if not hits:
            raise RefuseStage("no_hit")

        expanded = t2_context_expand([h.chunk_id for h in hits[:5]], window=200, span=span)
        c.hits, c.rounds = expanded, round_no
        c.top1 = max(h.score for h in hits)
        span.digest(input=f"q={q[:60]}", output=f"rounds={round_no} hits={len(hits)} top1={c.top1:.2f}")
        return c
```

**两个刻意的设计**

1. **首轮带类目，二轮去掉**：类目由问题中的关键词粗判（"寺观"→祠祀，"进士"→选举）。带上类目能提高精度，但也会漏召回；所以二轮放宽是"扩大再失败才拒答"，而不是"一次失败就拒答"。
2. **T2 只对前 5 条做扩展**：±200 字扩展会让上下文体积翻倍。只扩展最可能要引用的前 5 条，是为了在 900ms 预算内保住"引用原文完整"这个更重要的目标。

### 3.6 阶段 4：充分性判定

```python
def s4_judge(c: AgentState) -> AgentState:
    with stage("judge", seq=4, budget_ms=50) as span:
        checks = [
            ("top1_score", c.top1, c.threshold, c.top1 >= c.threshold, "low_score"),
            ("hit_count",   len(c.hits), 2,      len(c.hits) >= 2,      "too_few"),
            ("rounds_used", c.rounds, 2,         c.rounds <= 2,         "max_round"),
            ("category_cov", c.category_count, 1, c.category_count >= 1, "no_category"),
        ]
        failed = [c3 for c3 in checks if not c3[3]]
        span.event("validation", decision="insufficient" if failed else "sufficient",
                   detail={"checks": [_as_dict(x) for x in checks]})
        if c.top1 < c.threshold and c.rounds >= 2:
            raise RefuseStage("low_score", nearby=c.hits[:3])   # DR-09
        return c
```

判定结论**不只看分数**。原型轨迹页那张判定表就是这个函数的输出（`判定项 / 阈值 / 实际 / 结论 / 原因码`），四个维度缺一不可：

- 只有分数高但命中 1 条 → 可能是"撞上一条恰好相似的片段"，不足以支撑多句断言；
- 分数中等但命中 9 条且跨 3 个类目 → 往往可以回答，只是措辞要保守（转 `partial`）。

### 3.7 阶段 5：生成（两通道）

```python
def s5_generate(c: AgentState) -> Iterator[SSEEvent]:
    with stage("generate", seq=5, budget_ms=2500) as span:
        # ---- 5a 引证档：结构化、非流式、max_tokens≈400 ----
        brief = chain_brief.invoke({                       # ChatPromptTemplate | model | PydanticOutputParser
            "question": c.question, "context": c.context_block,
            "rules": CONTRACT_RULES,
        })                                                  # -> AnswerBrief
        c.answer_type = brief.answer_type
        c.direct_answer = brief.direct_answer
        c.citations = brief.citations                       # 编号 1..n
        for cit in c.citations:
            yield SSEEvent("citation", cit.model_dump())
        span.event("tool_call", tool_name="llm_brief",
                   params_digest=f"prompt={PROMPT_VERSION} chunks={len(c.hits)}",
                   result_count=len(c.citations), detail={"claims": len(brief.claims)})

        # ---- 5b 白话解读：流式 ----
        for token in chain_explain.stream({"brief": brief, "context": c.context_block}):
            yield SSEEvent("delta", {"text": token})
        span.digest(input=f"chunks={len(c.hits)}", output=f"type={c.answer_type} cites={len(c.citations)}")
```

**为什么先引证档再解读**

| 若合成一次流式调用                    | 拆两通道                         |
| ---------------------------- | ---------------------------- |
| 引用只能在正文后面出现，与"编号挂在断言左侧"的版式冲突 | 引用先到，正文边到边挂编号                |
| 模型可能先写正文、后补引用，导致引用与断言对不齐     | 引证档是结构化输出，断言与引用在生成时就绑定       |
| TTFT 取决于第一个 token，但引用校验要等全文  | TTFT 从 `citation` 事件算，校验在后台做 |
| 输出被截断时引用可能丢失                 | 引用已在 5a 落定，即使 5b 中断也有引用      |

代价是多一次模型调用（约 +0.6s、+400 token）。这个代价换来"引用可靠 + 首字更快"，值得。

### 3.8 阶段 6：输出校验

四个检查，按"致命 → 降级"顺序：

```python
def s6_validate(c: AgentState) -> AgentState:
    with stage("validate", seq=6, budget_ms=150) as span:
        retrieved = {h.chunk_id for h in c.hits}

        # ① 引用闭环：id 必须 ∈ 本次片段集（AT-06）
        valid = [x for x in c.citations if x.chunk_id in retrieved]
        dropped_cites = len(c.citations) - len(valid)

        # ② 重编号：删除后必须连续，正文 [k] 同步重映射
        renumber = {old.no: new_no for new_no, old in enumerate(valid, 1)}

        # ③ 断言级校验：按句切分，事实性断言必须挂引用
        claims = split_claims(c.direct_answer + "\n" + c.explanation)
        kept, dropped_claims, downgraded = [], [], []
        for claim in claims:
            if not is_factual(claim):                 # 无年份/数量/人名/官职/事件动词 → 非事实性
                kept.append(claim); continue
            if not claim.citation_nos:
                downgraded.append(claim); continue    # 无引用：标"未核实"或删
            kept.append(claim)

        # ④ 数字/专名闭包：断言里的年份、人名、数字必须出现在引用片段并集中
        corpus = "".join(h.text_with_context for h in c.hits)
        for claim in kept:
            if not tokens_subset(claim, corpus):
                dropped_claims.append(claim)          # 出现语料外数字 → 删句

        span.event("validation", decision="pass" if not (dropped_cites or dropped_claims) else "fail",
                   detail={"cites_dropped": dropped_cites, "claims_dropped": len(dropped_claims),
                           "claims_downgraded": len(downgraded)})
        return apply_validation(c, kept, valid, renumber, dropped_claims, downgraded)
```

**降级梯**（顺序不可颠倒）：

```
1. 全部断言被删 → answer_type = refuse(reason='validation')，正文换成拒答话术
2. direct_answer 保留但 explanation 删句 > 50% → answer_type = partial + caveat
3. 覆盖率（有引用断言 / 事实性断言）< 70% → partial + caveat
4. 仅个别句被删 → 保留 fact，被删句位置不留空（避免"这里本来有句话"的暗示）
```

### 3.9 阶段 7：交付

组装 10 字段、下发 `done`、**不做落库**（落库由 `chat_service` 在流结束后统一做，保证"一条消息一个事务"）。

```python
def s7_deliver(c: AgentState) -> AnswerPayload:
    with stage("deliver", seq=7, budget_ms=None) as span:
        payload = AnswerPayload(
            answer_type=c.answer_type,
            region=RegionRef(id=c.region_id, name=c.region_name, era_note=c.era_note),
            direct_answer=c.direct_answer, explanation=c.explanation,
            citations=c.citations, confidence=c.confidence,
            caveats=c.caveats, followups=c.followups[:3],
            refusal=c.refusal,
            meta=Meta(trace_id=c.trace_id, model=c.model, prompt_version=PROMPT_VERSION,
                      index_version=c.index_version, token_in=c.token_in,
                      token_out=c.token_out, elapsed_ms=c.elapsed_ms))
        span.digest(output=f"type={payload.answer_type} cites={len(payload.citations)}")
        return payload
```

### 3.10 五个受限工具的签名

```python
# 五个工具都是同步函数：它们整体运行在 pipeline 下沉的检索线程里，
# 内部不再二次切线程（避免嵌套线程池）。T3–T5 的 LLM 调用虽走网络，
# 但同步 SDK 在该线程内阻塞是安全的——事件循环不在这个线程上。

# T1 混合检索
def t1_hybrid_search(*, q: str, scope: SearchScope, category: str | None,
                     top_k: int, span: Span) -> RetrievalResult: ...

# T2 上下文扩展
def t2_context_expand(*, chunk_ids: list[str], window: int = 200,
                      span: Span) -> list[ExpandedHit]: ...

# T3 查询改写
def t3_query_rewrite(*, question: str, recent_turns: list[Turn],
                     memory_slots: dict, span: Span) -> RewriteResult: ...

# T4 大纲生成
def t4_outline(*, template: ReportTemplate, region: RegionRef,
               era_range: str | None, target_words: int, span: Span) -> Outline: ...

# T5 分节成文
def t5_section_write(*, section: OutlineSection, hits: list[ExpandedHit],
                     prev_summary: str, span: Span) -> SectionDraft: ...
```

**T1 的并行不在协程层**：两路召回（向量 / BM25）在一个线程内顺序执行即可——向量路里真正耗时的只有 `embed_query` 一次（20–80ms），BM25 的 FTS 查询通常 <10ms；用 `asyncio.gather` 并行两路，只会让两个线程抢同一批 CPU 核，总耗时反而更长。真正需要并行的是"多带并发各自独立"，那由线程池自然完成。

**预算在注册表里扣减**，不在工具内部判断：

```python
# agent/registry.py
class ToolBudget:
    def __init__(self, max_calls=6, deadline_ms=25_000):
        self.left, self.deadline = max_calls, time.monotonic() + deadline_ms / 1000
    def take(self, tool: str):
        if self.left <= 0 or time.monotonic() > self.deadline:
            raise BudgetExhausted(f"tool_budget: left={self.left}")
        self.left -= 1
```

这样"某个工具忘了检查预算"就不可能发生——它必须先 `take()` 才能执行。

### 3.11 分支树：一次问答可能走向哪

```
鉴权失败 ────────────────────────────────→ 401（不进 Agent）
scope 为空 ──────────────────────────────→ refuse(permission) + 写 trace
意图=闲聊 ───────────────────────────────→ boundary + 3 例
意图=写操作 ─────────────────────────────→ boundary + 后台入口
意图=报告 ───────────────────────────────→ report_outline（转报告流程）
意图=问答
  ├ 改写失败 ────────────────────────────→ 用原问题 + 提示（DR-06）
  ├ 检索 0 命中 ─────────────────────────→ refuse(no_hit) + 2~3 条相近
  ├ 分数不足 → 扩检 → 仍不足 ────────────→ refuse(low_score) + 相近 + 换问法
  ├ 片段冲突 ────────────────────────────→ fact/partial + caveat「两说并存」
  ├ 片段含传闻 ──────────────────────────→ 加「志载（存疑）」标记（DR-11）
  ├ 要求评价 ────────────────────────────→ 只呈现记载 + 说明不作评价（DR-12）
  ├ 超预算 ──────────────────────────────→ 摘要压缩 + 丢最早轮（DR-14）
  ├ 工具达上限/超时 ─────────────────────→ 按已有结果作答，不足则拒答（DR-15）
  ├ 生成超时 25s ────────────────────────→ partial + 「已保留部分内容」
  └ 校验删光断言 ────────────────────────→ refuse(validation)
```

---

## 4 检索链路详设

### 4.1 SearchScope：权限的唯一载体

```python
# rag/scope.py
@dataclass(frozen=True)
class SearchScope:
    region_id: str
    visible_doc_ids: tuple[str, ...]     # 已排序去重，不可变
    index_version: str

    def __post_init__(self):
        if not isinstance(self.visible_doc_ids, tuple):
            raise TypeError("visible_doc_ids must be tuple (immutable)")
```

```python
# services/chat_service.py —— 全系统唯一产地
def build_scope(principal: Principal, region_id: str, preview: bool, conn) -> SearchScope:
    statuses = ["published"]
    if principal.role == "admin" and preview:
        statuses.append("proofreading")          # 预览模式并入待校对
    ph = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT id FROM document WHERE region_id = ? AND status IN ({ph}) "
        f"AND deleted_at IS NULL ORDER BY id",
        (region_id, *statuses)).fetchall()
    return SearchScope(region_id, tuple(r["id"] for r in rows), active_index_version())
```

`frozen=True` + `tuple` 不是风格问题：如果它是可变 list，某个 retriever 往里 `append` 一个 doc_id，就会造成越权，而且极难排查。

### 4.2 向量路：过滤下推

```python
# rag/retrievers/vector.py
class VectorRetriever(BaseRetriever):
    scope: SearchScope
    k: int = 20

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:
        if not self.scope.visible_doc_ids:
            return []                                   # 空集短路，永不查全库
        where = {"$and": [
            {"region_id":     {"$eq": self.scope.region_id}},
            {"doc_id":        {"$in": list(self.scope.visible_doc_ids)}},
            {"index_version": {"$eq": self.scope.index_version}},
        ]}
        res = self.collection.query(
            query_embeddings=[self.embed.embed_query(query)],   # query 侧在 Embeddings 内部转简（4.7.2）
            n_results=self.k, where=where,
            include=["metadatas", "distances", "documents"])
        return [
            # page_content 来自 documents，即原文繁体——取证栏直接用它核对纸本
            Document(page_content=doc,
                     metadata={**meta, "chunk_id": meta["chunk_id"],
                               "score": 1.0 - dist, "vec_rank": i})
            for i, (doc, meta, dist) in enumerate(zip(
                res["documents"][0], res["metadatas"][0], res["distances"][0]), 1)
        ]
```

三个必查点：

1. `where` 里必须有 `doc_id $in`，只按 `region_id` 过滤是不够的（同县可能有未发布语料）；
2. `index_version` 也进过滤，避免新旧索引用同一 collection 时召回重复片段；
3. 返回后做**防御性断言**（只告警，不作为过滤手段）：

```python
leaked = [d for d in docs if d.metadata["doc_id"] not in scope.visible_doc_ids]
if leaked:
    metrics.incr("permission_leak_total")        # 这道告警一旦非 0，说明有 bug
    log.error("scope leak", extra={"count": len(leaked)})
    docs = [d for d in docs if d not in leaked]
```

### 4.3 全文路：预分词 + 过滤

```python
# rag/retrievers/bm25.py
class FtsRetriever(BaseRetriever):
    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:
        if not self.scope.visible_doc_ids:
            return []
        tokens = [t for t in jieba.cut_for_search(to_simplified(query)) if t.strip()]
        match = build_match_expr(tokens)             # 见 4.3.2
        ph = ",".join("?" * len(self.scope.visible_doc_ids))
        sql = f"""
          SELECT c.chunk_id, c.content, c.volume, c.volume_no, c.category, c.doc_id,
                 c.char_start, c.char_end,
                 -bm25(chunk_fts, 1.0, 2.0, 1.5) AS score
          FROM chunk_fts JOIN chunk c ON c.id = chunk_fts.rowid
          WHERE chunk_fts MATCH ?
            AND c.region_id = ?
            AND c.doc_id IN ({ph})
            AND c.is_searchable = 1
          ORDER BY bm25(chunk_fts, 1.0, 2.0, 1.5)
          LIMIT ?"""
        rows = self.conn.execute(sql, (match, self.scope.region_id,
                                       *self.scope.visible_doc_ids, self.k)).fetchall()
        return [Document(page_content=r["content"],
                         metadata={**dict(r), "bm25_rank": i, "score": normalize_bm25(r["score"])})
                for i, r in enumerate(rows, 1)]
```

#### 4.3.1 为什么必须预分词

FTS5 的 `unicode61` 分词器把 CJK 字符当作**连续 token**——"法门寺在县北二十里"会被切成一个词。不预分词，BM25 通路等于完全失效，而 BM25 恰恰是文言专名（人名、地名、职官）的唯一可靠通路（向量模型对"王锡嘏"这类低频专名不敏感）。

写入侧：

```python
content_seg  = " ".join(jieba.cut_for_search(to_simplified(content)))
volume_seg   = " ".join(jieba.cut_for_search(volume or ""))
category_seg = " ".join(jieba.cut_for_search(category or ""))
# 注意：volume_seg/category_seg 必须单独分词，否则"卷二·建置"被当作一个 token
```

查询侧同样处理，且**繁简归一必须在分词前**做（否则"縣"匹配不到简体索引）。

#### 4.3.2 MATCH 表达式的三级回退

```python
def build_match_expr(tokens: list[str]) -> str:
    if len(tokens) <= 3:
        return " AND ".join(quote_fts(t) for t in tokens)
    # 4 个以上 token 用 OR 会召回噪音，用 AND 会漏；折中用"前三个 AND，其余 OR"
    head = " AND ".join(quote_fts(t) for t in tokens[:3])
    tail = " OR ".join(quote_fts(t) for t in tokens[3:])
    return f"({head}) OR ({head} AND ({tail}))"
```

命中 < 5 条时回退纯 OR；仍 0 命中则 `content LIKE '%q%'`（限 scope 内）并记 `fts_fallback=1`。三级回退的顺序是"精确 → 放宽 → 兜底"，每一级都要打点，否则调优时看不出是哪一级在起作用。

### 4.4 RRF 融合与 rerank

```python
# rag/retrievers/hybrid.py
def hybrid_search(q: str, scope: SearchScope, *, k=20, rrf_k=60,
                  top_n=8, threshold=0.62) -> RetrievalResult:
    vec = VectorRetriever(scope=scope, k=k).invoke(q)
    bm  = FtsRetriever(scope=scope, k=k).invoke(q)

    fused: dict[str, dict] = {}
    for weight, lst, tag in ((1.0, vec, "vec"), (1.0, bm, "bm25")):
        for rank, doc in enumerate(lst, 1):
            cid = doc.metadata["chunk_id"]
            e = fused.setdefault(cid, {"chunk_id": cid, "rrf": 0.0,
                                       "text": doc.page_content, "meta": doc.metadata})
            e["rrf"] += weight / (rrf_k + rank)
            e[f"{tag}_rank"] = rank

    cand = sorted(fused.values(), key=lambda x: -x["rrf"])[: max(2 * k, 40)]
    scores = reranker.score_batch([(q, normalize_for_rerank(c["text"])) for c in cand])
    for c, s in zip(cand, scores):
        c["final_score"] = float(s)                  # score_batch 内部已过 sigmoid，见 4.7.4

    cand.sort(key=lambda x: -x["final_score"])
    picked = [c for c in cand if c["final_score"] >= threshold][:top_n]
    if not picked and cand:
        picked = cand[: min(3, len(cand))]           # 全低于阈值：交给 DR-09 判拒答

    return RetrievalResult(
        hits=[to_hit(c) for c in picked],
        top1=cand[0]["final_score"] if cand else 0.0,
        all_scores=[c["final_score"] for c in cand],
        top10=[{"chunk_id": c["chunk_id"], "score": round(c["final_score"], 3)}
               for c in cand[:10]],
        rounds=1)
```

#### RRF 的量纲必须写进注释

```
rrf_k = 60 时：
  单路 rank1 贡献 = 1/61  ≈ 0.0164
  两路都 rank1     = 2/61  ≈ 0.0328   ← 上限
因此 0.62 这类阈值绝不可能作用于 rrf 列。
若有人把 threshold 改成和 rrf 比较，检索会"永远不足"→ 全部拒答，
而且日志里看不出错，因为分数确实在 0.03 附近"低于阈值"。
```

这条注释要写在代码里，不是只写在文档里——它是本项目最容易被误改的一处。

`normalize_for_rerank()` 的边界也要写清：**它只做空白压缩与全角半角归一，不做繁简转换。** 理由有两条：cross-encoder 本身对繁简都能处理；而擅自转简会引入一次可能的错字（"乾"→"干"、"著"→"着"），让打分基于一个纸本上不存在的字符串。向量化侧之所以要转简（4.7.2），是因为 embedding 模型对繁体的覆盖明显弱于简体，两者的取舍不同，不能套用同一条规则。

### 4.5 上下文扩展 ±200 字

```python
# rag/context.py
def expand(hit: Hit, window: int = 200) -> ExpandedHit:
    # 不按"字符数前后各取 200"猜，按原文偏移精确切片
    start = max(0, hit.char_start - window)
    end = min(len(hit.doc_text), hit.char_end + window)
    return ExpandedHit(
        chunk_id=hit.chunk_id,
        quote=hit.doc_text[hit.char_start:hit.char_end],   # 命中片段原文（引用展示用）
        before=hit.doc_text[start:hit.char_start],
        after=hit.doc_text[hit.char_end:end],
        span_start=hit.char_start - start,
        span_end=hit.char_end - start,
        volume=hit.volume, page=hit.page, ocr=hit.ocr_flag)
```

**为什么存 `char_start/char_end` 而不是运行时用 `content.find(quote)`**：分块时已经知道精确偏移，运行时再找会遇到"同一段话在文中出现两次"的歧义。偏移是入索引时算好的事实，不是运行时猜的结果。

### 4.6 入库侧：分块与索引

```
① 三级识别（rag/segmenter.py）
   卷：^卷[一二三四五六七八九十百]+
   类目：^（建置|祠祀|选举|艺文|金石|人物|灾异|物产|赋役|...）
   条目：类目下的短标题行
   识别成功 → 按条目切分，单块 200–500 字

② 降级（识别失败）
   按空行切段 → 累积到 800 字切块 → 保留 100 字重叠
   记 segment_strategy='fallback'，供 P0 评估分块质量

③ 每块生成
   content（原字原文，繁体保留）
   content_seg（繁→简 + jieba 空格分词）
   volume_seg / category_seg
   char_start / char_end（在 version.content 中的偏移）
   token_count（汉字数 + 非汉字/4，入库时算好，组装上下文时直接求和）

④ 向量化（doc_embed 任务）
   调用 embeddings.embed_documents([r.content ...])
     └─ 内部把 content 经 opencc t2s 变成"简体、不分词"再编码（4.7.2）
     └─ 调用方不要自己转简，否则重复一次且容易与 query 侧不一致
   批 16（CPU；GPU 可 64）→ bge-m3.encode(normalize_embeddings=True)
   → chroma.upsert(
       ids=[chunk_id],
       embeddings=[vec],
       documents=[content],              ← 存原文繁体，取证栏据此逐字核对
       metadatas=[{chunk_id, doc_id, region_id, volume_no, index_version, ocr_flag}])
   注意 1：metadatas 不放"是否已发布"——状态只由 SQL 决定（架构文档 D-6）
   注意 2：documents 与"向量化输入"是两个不同的字符串，见 4.7.2
   注意 3：失败只把 index_state 置 'stale'，不回滚已建的分块，也不阻塞 proofreading
```

**为什么向量化输入不是 `content_seg`**：`content_seg` 是"繁→简 + jieba 空格分词"，空格是给 FTS5 用的；喂给 bge-m3 会把句子切成碎片，注意力被打散。也不是 `content`（繁体原字），因为评测集与用户提问都是简体，字面空间不一致会拉低召回。所以需要一个中间产物 `content_sim`——**它不落库**，向量化时现算（4.7.2 说明为什么可以偷这个懒）。


### 4.7 向量化与重排实现

#### 4.7.1 模型生命周期：单例、预热、锁版本

两个模型都是**进程级单例**，在 FastAPI `lifespan` 启动时加载，绝不在请求里 `SentenceTransformer(...)`——那样每个请求会重新读 2.3GB 权重。

```python
# rag/models.py
from dataclasses import dataclass
from sentence_transformers import SentenceTransformer, CrossEncoder

@dataclass(frozen=True)
class EmbeddingFingerprint:
    """模型指纹：改了任何一项，向量空间就变了，必须新建索引版本（架构 D-7）"""
    model: str          # "BAAI/bge-m3"
    revision: str       # HF commit hash，不是 "main"
    dim: int            # 1024

    def index_version(self, chunk_size: int, opencc_ver: str) -> str:
        slug = self.model.split("/")[-1].replace("-", "").replace(".", "")
        return f"idx__{slug}__{self.dim}__c{chunk_size}__o{opencc_ver}__v1"


class RagModels:
    def __init__(self, cfg):
        self.fp = EmbeddingFingerprint(
            model=cfg["llm.embedding.model"],
            revision=cfg["llm.embedding.revision"],   # 必须非空，为空直接抛
            dim=int(cfg["llm.embedding.dim"]),
        )
        self.device = cfg["llm.embedding.device"]
        self.embedder = SentenceTransformer(
            self.fp.model, revision=self.fp.revision,
            device=self.device, cache_folder=cfg["llm.embedding.cache_dir"])
        # 池化方式由模型自带的 1_Pooling/config.json 决定（bge-m3 = CLS），不要手写 mean pooling
        self.reranker = (
            CrossEncoder(cfg["llm.rerank.model"], max_length=512, device=self.device)
            if cfg["llm.rerank.model"] != "none" else None)

    def warmup(self):
        """冷启动第一次编码约 1–2s，必须放在启动阶段，否则会被算进首个用户的 TTFT"""
        self.embedder.encode(["预热"], normalize_embeddings=True, show_progress_bar=False)
```

**两个必须校验的点，缺一就会在生产里以 500 的形式出现：**

```python
# app/main.py 的 lifespan 里
fp_dim = models.embedder.get_sentence_embedding_dimension()
assert fp_dim == models.fp.dim, (
    f"配置 dim={models.fp.dim} 与模型实际 {fp_dim} 不一致；"
    f"改配置前请按 4.7.5 重建索引")
collection = chroma.get_collection(cfg["index.active_version"])
probe = collection.peek(limit=1)
if probe["embeddings"] and len(probe["embeddings"][0]) != models.fp.dim:
    raise RuntimeError("collection 维度与当前模型不一致，拒绝启动（架构 D-7）")
```

#### 4.7.2 三个文本口径，一个字都不能混

这是本节最容易出错的地方。同一个 chunk 在系统里有**三个不同的字符串用途**，任何一处写串都会静默降低召回或让引文对不上纸本：

| 口径 | 内容 | 用在哪 | 落库 |
|---|---|---|---|
| `content` | 原字原文，繁体，未分词 | ① Chroma 的 `documents` ② 取证栏引文 ③ 输出校验的引用快照 | ✓ `chunk.content` |
| `content_seg` | 繁→简 **+ jieba 空格** | 仅 FTS5 的 BM25 索引 | ✓ `chunk.content_seg` |
| `content_sim` | 繁→简，**不分词** | 仅向量化输入（含 query 侧） | ✗ 现算 |

```python
# rag/embeddings.py
import opencc
from langchain_core.embeddings import Embeddings

class BgeM3Embeddings(Embeddings):
    """LangChain Embeddings 适配层：只做归一化与批处理，不做任何文本变换以外的逻辑"""
    def __init__(self, models: RagModels, cfg):
        self.m = models
        self.batch = int(cfg["llm.embedding.batch_size"])
        self.q_instr = cfg["llm.embedding.query_instruction"]  # bge-m3 为空串
        self._t2s = opencc.OpenCC("t2s")                       # 进程内复用，构造一次即可

    def _sim(self, text: str) -> str:
        return self._t2s.convert(text)          # 只转简，不分词

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vecs = self.m.embedder.encode(
            [self._sim(t) for t in texts],
            batch_size=self.batch,
            normalize_embeddings=True,           # L2 归一 → 余弦 = 内积
            show_progress_bar=False)
        return vecs.tolist()

    def embed_query(self, text: str) -> list[float]:
        # query 侧同样走简体，保证与文档侧同一字面空间
        payload = (self.q_instr + text) if self.q_instr else self._sim(text)
        v = self.m.embedder.encode([self._sim(payload)],
                                   normalize_embeddings=True)[0]
        return v.tolist()
```

三个细节的理由：

1. **`query_instruction` 必须走配置。** bge-m3 不需要它，但 `bge-large-zh-v1.5` 需要"为这个句子生成表示以用于检索相关文章："前缀，且**只加在 query 侧、不加在 document 侧**。硬编码在代码里的后果是：某次重构把两侧合并成一个方法，召回率下降 5–10 个点，而线上无任何报错、无任何告警。
2. **`normalize_embeddings=True` 必须在两侧同时开。** Chroma 用 `hnsw:space=cosine`；若只归一化文档侧，两侧范数不同，余弦计算仍正确但内积语义错位，而 `1 - distance` 的换算会偏。
3. **`opencc.OpenCC("t2s")` 是进程级对象，不要每次调用都构造。** 它要加载字典，构造开销约 10ms，放在热路径上是白扔钱。

#### 4.7.3 批量、超时与失败

```python
# workers/handlers/doc_embed.py —— 分批提交，可中断续跑
BATCH = 16            # CPU；GPU 可 64

def handle_doc_embed(task):
    rows = chunk_repo.list_pending(task.payload["doc_id"], task.payload["index_version"],
                                   limit=BATCH)
    while rows:
        texts = [r.content for r in rows]
        try:
            vecs = embeddings.embed_documents(texts)
        except Exception as e:                       # 单批失败只降级本批
            chunk_repo.mark_index_state([r.chunk_id for r in rows], "stale")
            log.warning("embed batch failed", extra={"err": str(e), "n": len(rows)})
            if _is_fatal(e):                          # OOM / 模型损坏：终止任务，不重试
                raise
            time.sleep(backoff(task.attempt))         # 1s/2s/4s，最多 3 次
            task.bump_attempt(); continue
        collection.upsert(ids=[r.chunk_id for r in rows], embeddings=vecs,
                          documents=[r.content for r in rows],   # ← 原文，不是 texts
                          metadatas=[_meta(r, task.payload["index_version"]) for r in rows])
        chunk_repo.mark_index_state([r.chunk_id for r in rows], "indexed")
        task.progress(step=len(rows))
        rows = chunk_repo.list_pending(...)
```

- **可中断续跑**：任务不保存"处理到第几条"，而靠 `chunk.index_state='pending'` 反查。进程被杀后重启，任务重新入队，已 `indexed` 的不会被重复处理。这比在任务里记游标更抗崩溃。
- **失败不阻塞润色**：`document.status` 由解析成功决定（`proofreading`），与索引成功无关。索引没完成时，预览模式降级为纯 BM25，并在文档详情标注"向量索引未就绪"（架构 4.3 已定）。
- **超时**：单批 30s 上限；整体任务走 `limits.task_timeout_ms`，超时后任务置 `failed` 但已索引的 chunk 保留。
- **不要为了"跑满 CPU"开多线程编码**：PyTorch 自身已用 `torch.set_num_threads`；再叠一层线程池会与 worker 线程争抢，反而更慢。调 `torch.set_num_threads(物理核数 - 1)`，留一个核给 API 进程。

#### 4.7.4 重排：logits 必须过 sigmoid

这是第二个"量纲坑"，与 D-5 同源。`bge-reranker-v2-m3` 是单标签 cross-encoder，**`predict()` 返回的是 logits，范围约 −10 ~ +10，不是 0–1 概率**。

```python
# rag/rerank.py
import numpy as np

def rerank(query: str, cands: list[dict], top_n: int) -> list[dict]:
    if models.reranker is None:                     # 关闭 rerank：退回余弦
        for c in cands: c["final_score"] = c["score"]
        return sorted(cands, key=lambda c: -c["final_score"])[:top_n]
    pairs = [(query, c["text"]) for c in cands]
    logits = models.reranker.predict(pairs, batch_size=16, show_progress_bar=False)
    probs = 1.0 / (1.0 + np.exp(-np.asarray(logits)))     # ← 不做这一步，0.62 就永远比不到
    for c, p in zip(cands, probs):
        c["final_score"] = float(p)
    return sorted(cands, key=lambda c: -c["final_score"])[:top_n]
```

若不转 sigmoid，`final_score` 会是 −3.2 之类的值，与 0.62 比较时**恒为不足**，系统会稳定地对所有问题拒答——而日志里看分数确实"低于阈值"，非常难排查。这条已在 `retrieval.score_threshold` 的注释里标注（架构 6.1）。

#### 4.7.5 换 embedding 模型：不停机的六步

因为 collection 维度不可变（D-7），换模型不是改配置，而是一次**索引迁移**。全程旧索引继续服务，用户无感。

| 步 | 动作 | 校验点 | 失败回退 |
|---|---|---|---|
| 1 | 改 `llm.embedding.model` / `revision` / `dim`，算出新 `index_version` | 启动自检通过（4.7.1） | 改回旧值即可，旧 collection 未动 |
| 2 | 建新 collection `chunks__{new_ver}`（`hnsw:space=cosine`） | 建完 `peek()` 为空 | 删新 collection |
| 3 | 投 `reindex` 任务：按 `chunk.index_state` 分批重算，写新 collection | 进度与 `indexed` 计数一致 | 任务可中断，已写入的保留 |
| 4 | 抽样比对：抽 20 条 chunk，用新向量自查询应命中自身（top1 = 自身） | 命中率 ≥ 19/20 | 说明归一化或池化写错了，回查 4.7.2 |
| 5 | 切 `index.active_version` 到新值（配置项，不重启） | 检索走新 collection，`index_version` 过滤生效 | 切回旧值立即恢复 |
| 6 | 用 50 题评测集跑分数分布，重标 `retrieval.score_threshold`；7 天后删旧 collection | Recall@5 与基线对比记入 `eval_run` | 旧 collection 还在，可切回 |

第 6 步的顺序不能反：**先切流量、后标阈值**，否则阈值是按新分布标的，却用在旧索引上，会有一段时间判定全部失准。反过来（先切再标）这段时间的判定会偏松或偏严，但不会越权、不会崩，属于可接受的降级。

#### 4.7.6 不能让模型推理阻塞事件循环

`embed_query`（20–80ms）与 `rerank`（150–400ms）都是**同步的 CPU 计算**。FastAPI 的事件循环是单线程的，直接在 `async def` 里调用它们，等于在这 200–480ms 内冻结整个进程——所有 SSE 连接停止推流，其他请求全部排队。20 并发的要求（约束 9）会直接失效，且现象是"偶尔某个回答卡住几秒"，极难归因。

**做法：不在工具层切线程，而是让整个阶段在线程池里跑**（3.1 的编排器）。这样 `s3_retrieve` → `t1_hybrid_search` → `embed_query` / `rerank` 全在同一个线程内顺序执行，**没有嵌套线程池**，也就没有"线程 A 等线程 B、B 的池已满"这类隐蔽死锁。

```python
# core/executors.py —— 两个池，按性质分开，互不挤占
import os
import torch
from concurrent.futures import ThreadPoolExecutor

# 检索池：CPU 密集（编码 + 重排）。大小固定 2，不随并发增长
RETRIEVAL_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="retrieval")
# 通用池：PDF 解析、DOCX 导出、文件 IO
IO_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="io")

# 进程启动时设置一次：把核留给 API，避免编码时 SSE 得不到调度
torch.set_num_threads(max(1, (os.cpu_count() or 4) - 1))
```

```python
# agent/pipeline.py（与 3.1 的编排器一致，这里显式指定池）
loop = asyncio.get_running_loop()
c = await loop.run_in_executor(RETRIEVAL_POOL, s3_retrieve, c)
```

四条约束：

1. **池大小固定为 2，不随并发增长。** `torch` 内部已经把多核用满（`set_num_threads`），再开线程只会互相抢核、整体变慢。并发高时正确行为是**排队**，不是并行。
2. **检索池与 IO 池必须分开。** 一次 DOCX 导出可能占用线程 3–5s；若与检索共池，用户会看到"问一句话等了 5 秒才出字"，而 CPU 其实是空闲的。
3. **超时包在 `wait_for` 外层。** Python 的 `Future` 没有可中断超时，超时后线程仍会跑完、结果被丢弃。因此阶段 3 的 900ms 预算应理解为"排队 + 计算"的总时长，P0 实测后写回 `limits`。
4. **`s5_generate` 绝不进线程池。** 它是 `async` 生成器，靠 `await` 等 token；放进线程池会退化成同步阻塞，流式就没了。

---

## 5 关键实现要点

13 条，每条给"做法 / 为什么 / 怎么验证"。这些是本项目最容易写错、且写错后最难发现的地方。

### G-1 权限过滤的双重保证

**做法**：`build_scope()` 一次算出 → 不可变 `SearchScope` 注入两路召回 → Chroma `where` 与 FTS SQL 各自带过滤 → 返回后断言 `doc_id ∈ scope`（仅告警）→ 空集短路。

**为什么**：需求把"过滤前置"定为第一原则。应用层补筛会在 trace 里留下"召回 200 条只用了 8 条"的假象，且一旦有人在筛选处加了 `except: pass`，越权就静默发生。

**验证**：造 3 个县 × 2 个状态（已发布/待校对）的数据，user 只应看到 1 部。断言两路召回的 `doc_id` 全集 ⊆ `visible_doc_ids`；`permission_leak_total == 0`；AT-03 用例（改 `region_id`）返回"暂无可检索语料"。

### G-2 引用闭环校验

**做法**：六步——① `chunk_id ∈ 本次片段集`，否则作废；② 重编号 `1..n` 并同步正文 `[k]`；③ 按句切分，含年份/数量/人名/官职/事件动词者判为事实性断言；④ 无引用断言进 `unverified`；⑤ 断言中的数字/年份/专名集合必须 ⊆ 引用片段（含 ±200 窗口）并集；⑥ 降级梯（全删 → refuse / 覆盖 <70% → partial / 个别删 → fact）。

**为什么**：需求把"引用必须来自本次片段集"当作防幻觉与防越权的最后一道闸。提示词只能"请求"模型遵守，校验必须由程序做。

**验证**：构造 6 条"编造卷九第 2 页"的样本，100% 被作废；`validation` event 里 `cites_dropped / claims_dropped` 与人工核对一致；600 字 + 8 引用的 P95 耗时 <150ms。

### G-3 SSE 与生成解耦

**做法**：生成跑在独立 `asyncio.Task`，写 `asyncio.Queue` + 进程内 RingBuffer（64KB / TTL 120s）；`is_disconnected()` 只停推送不停生成；`finally` 里 `asyncio.shield(落库)`；`Last-Event-ID` 决定重放起点；缓冲过期发 `resync`。

**为什么**：把生成绑在请求作用域里，客户端一断就 `CancelledError`，答案丢失且 trace 缺尾。而"切换标签页"远多于"放弃答案"。

**验证**：`curl --max-time 1` 主动断流，1 分钟后 `GET /messages/{id}` 断言 `status='complete'` 且 `total_ms` 完整；前端切标签页后回来能续上。

### G-4 队列的幂等与优雅停机

**做法**：DB 是真相源，队列只是信号（`enqueue` 先插 `task` 再 `put_nowait`，队列满则靠 poller 兜底）；`claim` 在 `BEGIN IMMEDIATE` 内用 `UPDATE ... RETURNING` + 60s 租约；SIGTERM 时停止 claim、等在飞 ≤25s、剩余 `running` 回置 `queued`。

**为什么**：`queue.Queue` 里的条目在进程崩溃时必然丢失；只存表会有唤醒延迟。幂等键 + 唯一索引是"重复上传/重复点发布"的唯一防线。

**验证**：`kill -9` 后重启，无 `running` 僵尸（租约回收 30s 内 requeue）、无重复 `succeeded`；同一 `Idempotency-Key` 连发 5 次上传，只产生 1 个 task 与 1 个 document。

### G-5 SQLite 并发写

**做法**：`WAL + synchronous=NORMAL + busy_timeout=5000`；所有写走单写线程（专用连接、`BEGIN IMMEDIATE`、30ms 微批）；读用 per-request 连接；**事务内禁止任何网络调用**；单事务 >200ms 记 warning。

**为什么**：SQLite 同一时刻只有一个写者。FastAPI 线程池 + 同步驱动极易 `database is locked`；把 LLM 调用放进事务会持锁数秒，20 并发下直接雪崩。

**验证**：20 并发混合读写 5 分钟，`SQLITE_BUSY` 计数 = 0、写 P95 <50ms；故意在事务里 sleep 3s，观察告警。

### G-6 Chroma 与 SQLite 的一致性

**做法**：① `chunk` 表是真相源，Chroma 可丢可重建；② 权限过滤只用 SQL 算的 `visible_doc_ids`，不依赖 Chroma 元数据中的状态；③ 下线：`document.status='offline'` + `chunk.is_searchable=0` **同事务**（立即不可检索）→ 再异步 `collection.delete(where={"doc_id":...})` → 失败入 `index_cleanup` 补偿任务；④ `index_reconcile` 每日比对 `chunk(index_state='indexed')` 与 `document.status`；⑤ 重建用新 collection（`chunks_<index_version>`），切 `index.active_version`，旧 collection 保留 7 天。

**为什么**：Chroma 的删除不是事务性的。若依赖它做"下线即不可检索"，中间窗口就是越权窗口。

**验证**：下线后**不等清理任务**立刻检索，断言 0 命中；手删 collection 后跑 rebuild，recall@5 与重建前差 ≤1%。

### G-7 轨迹异步写入与截断

**做法**：`TraceCollector` 全程内存累加（`perf_counter` 差值）；请求结束 `put_nowait`；有界队列 2000，满则丢弃并 `trace_dropped_total+1`（日志 1 条/秒）；写线程批量 50 条或 200ms 刷一次；单 trace `size_bytes > 100KB` → `hit_chunk_ids` 截断为前 10、`truncated=1`；失败重试 3 次后丢弃只计数。

**为什么**：要求"主链路开销 ≤5ms""写入失败绝不影响回答"。每阶段写库会把 8 次写放大到 8 倍的锁竞争。

**验证**：`timeit` 包 `submit()` 断言 P99 <1ms；塞满队列后回答仍正常且 `metric_daily['trace.write_failed']>0`；构造 500 命中的 trace，落库 `size_bytes<100KB`。

### G-8 上下文预算分配

**做法**：8K 表——系统+约束 700 / schema 提示 100 / 当前问题 400 / 记忆槽 150 / 摘要 300 / 近 2 轮原文 500 / 检索片段 4200 / 输出预留 1800。按"系统 > 问题 > 槽 > 摘要 > 片段 > 历史"从低往高截；片段最少保留 2 条，不足则先砍历史再砍摘要；`chunk.token_count` 入库时算好，组装时求和。

**为什么**：需求 FR-C05 明确了优先级。实时 `tiktoken` 每次 10–30ms，在 900ms 检索预算里是浪费；不预留输出额度会导致生成中途被截断。

**验证**：10 组极端输入（2000 字问题 + 20 轮历史 + 8 条长片段），断言 `sum(tokens) ≤ 8192` 且片段 ≥2；20 轮会话 e2e 不报 `context_length_exceeded`。

### G-9 FTS5 中文分词的三处细节

**做法**：① 入库时 `content_seg/volume_seg/category_seg` 三列都要预分词；② 查询侧同样 `to_simplified` + `cut_for_search`；③ 繁简归一**在分词前**；④ 展示永远用 `chunk.content`（原字原文）。

**为什么**：`unicode61` 不切 CJK；不单独分词 `volume_seg`，"卷二·建置"会被当作一个 token，按卷次检索失效；先分词后繁简归一会导致"縣"与"县"无法互相命中。

**验证**：20 条专名问题（"法门寺""王锡嘏"）BM25 单路 recall@5 ≥70%；与"不预分词"基线对比应为 0；繁体查询"縣"能命中简体索引。

### G-10 DOCX 中文字体

**做法**：三件事必做——① `style.font.name='宋体'` **且** `style.element.rPr.rFonts.set(qn('w:eastAsia'),'宋体')`；② 标题用内置 `Heading 1/2/3` 但重设 eastAsia 为"黑体"并去掉默认蓝色主题色；③ 正文 `first_line_indent=Pt(21)`、`line_spacing=1.5`、页边距 2.54/3.17；引用上标 `run.font.superscript=True`；页脚页码手写 `fldSimple PAGE`；文件名 `县名-模板-YYYYMMDD.docx`，响应头 `filename*=UTF-8''`。

**为什么**：中文 DOCX 90% 的"字体没生效"都出在缺 `w:eastAsia`——只设 `font.name`，Word 会用默认中文字体渲染。这是最常见的坑，且不报错。

**验证**：Word 与 WPS 各打开一次，核对正文宋体、标题黑体、首行缩进 2 字符、页码存在、文件名不乱码；自动化断言 XML 中存在 `w:eastAsia="宋体"`。

### G-11 上传与解析安全

**做法**：扩展名白名单 + `python-magic` MIME + ≤50MB + 丢弃原文件名（用 `doc_id`）落 `data/files/{doc_id}/raw.pdf`（目录不在 Web 根、`0600`）；解析在**子进程**（`join(timeout=600)`，超时 kill）；加密 PDF 先问密码（密码不入日志）；拒绝含 JS/内嵌文件的 PDF；乱码率 >0.5% 直接 `parse_failed`。

**为什么**：PDF 解析库是历史漏洞高发区，同进程解析等于把攻击面暴露给主服务。

**验证**：`.pdf.exe`、改名脚本 PDF、加密 PDF、200 页扫描 PDF 各一份，断言行为符合 EX-01/02/03/04/06；解析期间主进程内存不涨。

### G-12 时间、布尔、软删的统一

**做法**：所有写库时间走 `core.time.utcnow_iso()`（禁止 `datetime.utcnow()` 拼字符串）；布尔 `INTEGER 0/1 + CHECK`；软删在 `repo` 基类统一拼 `deleted_at IS NULL`；唯一索引用部分索引；`retention_cleanup` 按保留期矩阵清理，**会话删除不删 trace**（`question_text='[deleted]'`）。

**为什么**：混用时间格式会让 `BETWEEN` 静默漏数据；软删忘加条件会出现"删了还能检索"；轨迹含用户问题，删除权与审计必须显式取舍（C-09）。

**验证**：正则扫全表断言时间格式统一；随机 5 张表做"软删后查询"断言 0 行；删会话后 `message` 行 0、`agent_trace` 行仍在且 `question_text='[deleted]'`。

### G-13 三个文本口径与两个量纲，各有一个"静默失败"陷阱

这一条是把架构 D-5、D-7 的落地风险集中起来，因为它们共同的特征是**不报错、不掉线、只是结果变差**，比崩溃难查一个数量级。

**陷阱一：向量化输入用错了字符串。** `content`（繁体）与 `content_seg`（带空格）都能跑通，`encode()` 不会报错，但召回率会掉。**做法**：向量化入口只接受 `content_sim`，函数签名为 `embed_documents(texts: list[str])` 且在 docstring 里写死"入参必须是简体不分词文本"；`documents=` 单独传 `content`。**验证**：拿一条已知繁体 chunk，断言 `collection.get(ids=[cid])["documents"][0] == chunk.content`（不是简体），且 `embed_documents([简体])` 与库中向量余弦 ≈ 1.0。

**陷阱二：rerank 的 logits 没过 sigmoid。** `final_score` 会是 −4.7 之类的负数，与 `0.62` 比较永远"不足"，系统对所有问题**稳定拒答**，而日志显示"分数低于阈值"，完全合理。**做法**：`rerank()` 内部统一 `1/(1+exp(-logit))`，并在 `agent_span.detail` 里同时记 `logit` 与 `prob` 两个字段以自证。**验证**：单测喂一个已知高度相关的 query-passage 对，断言 `0 < final_score < 1`；再断言关闭 rerank 时 `final_score == 余弦 ∈ [−1,1]`，两者不可混。

**陷阱三：换了 embedding 却复用旧 collection。** Chroma 对维度不一致的 `upsert` 有时会静默接受（写入被丢弃或分片错位），`query` 也不一定抛错，表现为"新语料怎么都检索不到"。**做法**：`index_version` 编码模型指纹（D-7），collection 名带版本，启动自检比对维度（4.7.1）。**验证**：故意把 `dim` 改成 768 启动，断言进程**拒绝启动**并给出明确错误信息——这个负向测试必须写，否则自检代码会在某次重构中被删掉。

---

## 6 前端设计

### 6.1 工程结构

```
frontend/
  index.html
  vite.config.ts            # dev proxy: /api → http://127.0.0.1:8000
  src/
    main.ts
    router.ts               # 7 个视图 + 守卫（role 校验）
    api/
      client.ts             # fetch 封装、401 自动 refresh、错误码 → toast
      sse.ts                # POST + ReadableStream 解析（见 6.5）
      {auth,corpus,polish,chat,report,trace}.ts
    stores/
      auth.ts               # 当前用户、角色、可见县
      chat.ts               # 会话、消息流、流式状态、取证栏选中项
      polish.ts             # 段落状态、进度、草稿
      ui.ts                 # toast、标注模式、角色预览开关
    views/
      LoginView.vue ChatView.vue ReportsView.vue
      CorpusView.vue PolishView.vue TraceView.vue SpecView.vue
    components/
      layout/{TopBar,RailNav,PageHeader}.vue
      chat/{Thread,SseAnswer,ClaimParagraph,MarginCite,CiteCard,
            EvidencePanel,StageTrail,FeedbackBar,Composer,SessionList}.vue
      common/{Badge,Chip,Modal,Toast,DiffText,StatusDot,Skeleton}.vue
    styles/tokens.css       # 朱墨体系（与原型同一套变量）
```

**路由与角色**：

```ts
const routes = [
  { path: '/login',   component: LoginView,   meta: { public: true } },
  { path: '/chat',    component: ChatView },
  { path: '/reports', component: ReportsView },
  { path: '/corpus',  component: CorpusView,  meta: { admin: true } },
  { path: '/polish',  component: PolishView,  meta: { admin: true } },
  { path: '/trace',   component: TraceView,   meta: { admin: true } },
  { path: '/spec',    component: SpecView },
]
router.beforeEach((to) => {
  if (to.meta.admin && auth.role !== 'admin') return '/chat'   // 与原型 go() 的兜底一致
})
```

**这只是体验层**。真正的防线在接口：`user` 直接 `curl /api/admin/corpus/documents` 会拿到 403。两者都要有，缺一不可。

### 6.2 视图与原型对照

| 路由 | 原型视图 | 角色 | 布局 | 主要接口 |
|---|---|---|---|---|
| `/login` | `data-view=login` | 公开 | 左品牌引文 / 右表单（1.15fr / .85fr） | 1,2 |
| `/chat` | `chat` | 全部 | 导航 64 / 会话 264 / 正文 / 取证 344 | 19,20,21,22,23,24-28,29,7 |
| `/reports` | `reports` | 全部 | 左 320 / 右自适应 | 30-37 |
| `/corpus` | `corpus` | admin | 单栏 7 列表格 | 9-15 |
| `/polish` | `polish` | admin | 主区（1fr 1fr 对照）/ 抽屉 300 | 16-18 |
| `/trace` | `trace` | admin | 瀑布 / 左 218 阶段 / 右详情 | 38-40 |
| `/spec` | `spec` | 全部 | 单栏 1000px，静态 | — |

### 6.3 对话页组件树与状态

```
ChatView
├── SessionList          会话分组（今天/昨天）、软删
├── Thread
│   ├── QuestionBlock    左侧 2px 青线、右下圆角、"第 N 轮 · 单轮未触发改写"
│   ├── StageTrail       3–4 个阶段点 → 完成后收成一行摘要（召回/采用/扩检/耗时）
│   ├── SseAnswer
│   │   ├── PreviewBar      仅 preview 时，橙条 + 退出按钮
│   │   ├── ClaimParagraph  42px 页边 + 堆叠编号（一条断言多个出处时竖向排列）
│   │   ├── CiteCard[]      卷次 · 书名 + 两行截断 + "支持：第 1 句…"
│   │   └── FeedbackBar     有用/有误/整理成报告
│   └── Composer          Enter 发送 / Shift+Enter 换行 / 计数 N/2000
└── EvidencePanel         常驻，非弹层：tab [1][2]… + 原文（宋体）+ 元数据 + ±200 展开
```

**流式状态机**（`stores/chat.ts`）：

```ts
type Phase = 'idle' | 'connecting' | 'streaming' | 'done' | 'error'
interface StreamingMsg {
  messageId: string | null
  phase: Phase
  stages: StageEvent[]          // stage 事件累积，用于阶段条
  answerType: AnswerType | null
  citations: Citation[]         // citation 事件到达即渲染（先于正文）
  text: string                  // delta 累积
  caveats: string[]
  corrections: Correction[]
  refusal: Refusal | null
  selectedCite: number           // 取证栏当前 tab
}
```

**关键约束**：`citation` 可能先于 `delta` 到达。因此渲染顺序是"骨架 → 引用卡占位 → 正文逐段填入"，而不是"等正文写完再找引用"。这正是原型"编号挂在左侧页边"的版式要求。

**页边编号的三态**（与原型一致）：默认 / hover / **作废**（`opacity:.35`，对应 `correction` 事件的 `action='drop_claim'`）。作废态是"引用校验没通过"的可见证据——用户看到的不是一个坏掉的引用，而是一个明确标注"未核实"的句子。

### 6.4 润色工作台的状态

```ts
interface Segment {
  id: string; seq: number
  state: 'pending' | 'confirmed' | 'rejected' | 'edited'
  rawText: string
  polishedText: string
  diff: DiffOp[]                 // 来自后端 diff_json
  ops: ('punct'|'t2s'|'segment'|'gloss')[]
  gloss?: string
  sensitive: boolean
  warn?: string                  // "超出润色范围：原文没有「康熙十二年知县周某重修」"
}

const canPublish = computed(() =>
  segments.every(s => s.state === 'confirmed' || s.state === 'edited')
  && checklist.every(c => c.passed))
```

`rejected` 的段**必须显示"未进入发布"**，且不参与批量确认。批量确认只对 `pending && !sensitive && !warn` 的段生效——原型里"批量确认未修改段（2）"右下角的括号数字就是这个过滤结果。

### 6.5 SSE 客户端实现

```ts
// api/sse.ts
export async function streamChat(body: ChatRequest, onEvent: (e: SseEvent) => void, signal?: AbortSignal) {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
      'Authorization': `Bearer ${auth.accessToken}`,
      'X-Request-Id': crypto.randomUUID(),
    },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok) throw await toApiError(res)

  const reader = res.body!.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = '', lastId = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })

    // 帧分隔是空行；必须处理"一个 chunk 半个帧"与"一个 chunk 多个帧"
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, idx); buf = buf.slice(idx + 2)
      const ev = parseFrame(frame)          // 解析 event: / data: / id:
      if (!ev) continue
      if (ev.id) lastId = ev.id
      onEvent(ev)
      if (ev.event === 'done' || ev.event === 'error') return
    }
  }
}
```

三条实现注意：

1. **不要用 `EventSource`**（D-3）：只能 GET，问题会进 URL。
2. **`TextDecoder` 必须带 `{stream:true}`**：否则多字节汉字被 chunk 边界切断会出乱码。
3. **`lastId` 要保留**：断线重连时作为 `Last-Event-ID` 请求重放，才能从断点续而不是从头。

### 6.6 设计 token 沿用

前端不重新设计视觉，直接沿用原型的两套语义：

```css
:root {
  --paper:#eceee8; --ink:#161a18; --zhu:#a62b20; --qing:#2f4f4a; --line:#d5dad1;
  --zhu-soft:#f4e6e3; --cang:#2f6b4f; --shao:#8a5a18; --lan:#2c5d7c;
  --font-ui:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  --font-src:"Songti SC","Noto Serif SC",SimSun,serif;
  --gut:42px;                     /* 页边编号留白 */
}
```

**字体分工是语义的**：宋体只给引文与原文（别人说的话），黑体给产品生成的文字（我们说的话）。因此 `ClaimParagraph` 用黑体、`EvidencePanel` 与 `CiteCard.snip` 用宋体。这条规则一旦破坏，"哪句是史料"就分不清了。

**朱色的唯一职责**：标记"这里有可核对的证据，或需要你判断的存疑"。所以朱色只出现在引用编号、存疑框、越界警告、不可逆操作按钮上——不做装饰。

### 6.7 响应式范围

**仅 PC，最小 1024px**（C-07）。不实现移动端。

| 宽度 | 布局 |
|---|---|
| ≥1440 | 导航 64 / 会话 264 / 正文 / 取证 344 |
| 1280–1440 | 取证 308 / 会话 236 |
| 1024–1180 | 取证转右侧浮层（点编号滑出） |
| <1024 | 不做适配，显示一行提示 |

---

## 7 与原型、需求的追溯

### 7.1 原型交互 → 接口

| 原型动作（`data-act` / 属性） | 接口 | 说明 |
|---|---|---|
| 发送问题 / `data-ask` | 19 `POST /chat/stream` | 示例问题只是预填文本 |
| 点页边编号 `data-cite` | 29 `GET /chunks/{id}` | 取证栏数据源 |
| 会话切换 `data-sess` | 28 `GET /sessions/{id}/messages` | — |
| `new-session` | 25 `POST /sessions` | — |
| `preview-on` / `preview-off` | 19 的 `preview` 字段 | user 传 true 被降级 |
| `fb` up/down | 22 `POST /messages/{id}/feedback` | 点踩自动带 `trace_id` |
| `report-gen` | 31 `POST /reports/outlines` | 带当前问题作起点 |
| `upload-open` / `upload-submit` | 9 `POST /admin/corpus/documents` | 校验在服务端，前端只做提示 |
| `offline` / `offline-confirm` | 14 `transitions{action:'offline'}` | 输入书名二次确认 |
| `publish` / `publish-confirm` | 14 `transitions{action:'publish'}` | 7 项自查 |
| `retry`（解析失败） | 14 `transitions{action:'parse'}` | 同 doc 新 attempt |
| `view-doc` / `go-polish` | 15 / 17 | — |
| `data-segact` ok/no/undo/regen | 18 `PATCH segments` | 单段或批量 |
| `batch` 批量确认 | 18 批量 items | 仅非敏感且无警告段 |
| `rollback` | 14 `transitions{action:'rollback'}` | 保留当前版本 |
| `data-tpl` 换模板 | 30 `GET /reports/templates` + 31 | — |
| `ol-add` / `ol-del` / `ol-up` | 31 返回的 outline 本地编辑，32 提交 | 大纲编辑不落库直到确认 |
| `rep-generate` | 32 `POST /reports` | — |
| `retry-sec` | 35 单节重试 | — |
| `export` | 36 导出 | 24h 链接 |
| `data-stage` 点阶段 | 39 `GET /traces/{id}`（已含全部 span） | 前端切 tab，不重复请求 |
| `tr-cmp` | 39 带 `compare_to` | 返回 `diff` |
| `to-eval` | 40 转样本 | — |
| `data-role-set` | 无接口 | **原型演示开关**，真实角色来自令牌 |
| `annot` 标注模式 | 无接口 | 纯前端开发辅助 |

### 7.2 FR → 模块 → 端点 → 表

| FR | 模块 | 端点 | 主要表 |
|---|---|---|---|
| FR-A01 注册 | auth_service | 1 | user, invite_code |
| FR-A02 登录 | auth_service | 2,3 | user, auth_refresh_token |
| FR-A03 管理员初始化 | 脚本 | — | user |
| FR-A04 登出/吊销 | auth_service | 4 | auth_refresh_token |
| FR-A05 功能级权限 | deps | 全部 | user |
| FR-A06 权限一致性 | deps + 全部 service | 全部 | — |
| FR-A07 数据归属 | service 层 | 21,26,27,34 | conversation, report |
| FR-A08 操作留痕 | 各 service | — | operation_log |
| FR-B01~B04 上传/提取 | corpus_service + worker | 9 | document, task |
| FR-B05 分段 | rag/segmenter | 9（异步内） | chunk |
| FR-B06 乱码校验 | worker | 9 | document（parse_failed） |
| FR-B07 异步任务 | task_service | 54 | task |
| FR-B08 去重 | corpus_service | 9 | document.dup_key |
| FR-B09 原文不可变 | 触发器 + service | 15 | document_version |
| FR-B10 润色范围 | polish_service | 16,18 | polish_segment |
| FR-B11 逐段确认 | polish_service | 18 | polish_segment |
| FR-B12 润色留痕 | polish_service | 16 | polish_segment, operation_log |
| FR-B13 回滚 | corpus_service | 14 | document_version |
| FR-B14 敏感标记 | polish_service | 17 | polish_segment |
| FR-B15 发布自查 | corpus_service | 14 | document.publish_checklist_json |
| FR-C01 会话管理 | chat_service | 24-27 | conversation |
| FR-C02 流式 | api/sse | 19,20,23 | message |
| FR-C03 短期记忆 | agent/s2 + worker | 19 | conversation.summary |
| FR-C04 记忆槽 | chat_service | 19 | conversation.memory_slots_json |
| FR-C05 上下文预算 | agent/budget | 19 | — |
| FR-C06 县切换 | chat_service | 26 | conversation.region_id |
| FR-C07 话题漂移 | agent/s2 | 19 | — |
| FR-C08 会话归属 | chat_service | 21,28 | conversation |
| FR-D01 分块 | rag/chunking | 9 | chunk |
| FR-D02 向量化 | rag/indexer | 9 | chunk.index_version |
| FR-D03 混合检索 | rag/retrievers | 19 | chunk + Chroma |
| FR-D04 权限硬过滤 | rag/scope | 19,29 | document, chunk |
| FR-D05 阈值 | agent/s4 | 19 | app_config |
| FR-D06 引用校验 | agent/guards | 19 | citation |
| FR-D07 索引一致性 | indexer + worker | 14 | chunk.is_searchable |
| FR-D08 重建 | indexer | 14 `reindex` | chunk, Chroma |
| FR-E01~E05 报告 | report_service | 30-34 | report, report_section |
| FR-E06 导出 | worker | 36,37 | export_artifact |
| FR-E07 单节失败 | report_service | 35 | report_section |
| FR-E08 篇幅护栏 | report_service | 31 | app_config |
| FR-S01 语料列表 | corpus_service | 10 | document |
| FR-S02 用户管理 | admin_service | 41-45 | user, invite_code |
| FR-S03 用量成本 | admin_service | 46 | metric_daily |
| FR-S04 反馈列表 | admin_service | 47 | feedback |
| FR-S05 操作记录 | 各 service | — | operation_log |
| FR-S06 轨迹采集 | agent/trace | 全部问答 | agent_trace 三表 |
| FR-S07 轨迹查询 | trace_service | 38,39 | 同上 |
| FR-S08 轨迹对比 | trace_service | 39 | 同上 |
| FR-S09 转评测样本 | eval_service | 40 | eval_case |
| FR-S10 轨迹权限 | deps.require_admin | 38-40 | — |

---

## 8 假设说明

### 8.1 与架构文档共用的裁定（不重复展开）

`C-01`~`C-14`（需求矛盾裁定）与 `AS-01`~`AS-03`（设计假设）**定义在架构文档第 7 章**，本文全部沿用，编号一致。实现时若遇到与本文冲突的表述，以架构文档为准并回改本文。

最需要记住的五条：

| 编号 | 一句话 |
|---|---|
| C-01 | 文档状态 **6 态**（"5 态"是笔误） |
| C-02 | 发布自查 **7 项**（原型弹层 5 项是视觉简化，`checklist.version` 兼容两者） |
| C-03 | TTFT 设计预算 **2.5s**，P1 验收 3.0s，P3 收敛 2.5s |
| C-05 | 轨迹：采集 Must / 管理员查看 Must / 对比回放 Should / 面向用户 Won't |
| **D-7** | embedding = bge-m3（1024 维），**换模型等于换 collection**，且 `dim` / `revision` / `index_version` 三者必须一起变（本文 4.7.5 给了六步） |

### 8.2 本文新增的四个实现假设（A-01 ~ A-04）

| 编号 | 假设 | 理由 | 若推翻的影响 |
|---|---|---|---|
| **A-01** | 意图判定的规则集先覆盖"写操作 / 报告 / 闲聊"三类，其余一律按 `qa` 处理 | 漏判为 `qa` 是安全失败（多走一次检索），漏判为 `boundary` 是体验失败（拒答一个本可回答的问题）。宁可多检索 | 若规则误判率高，需把 `qa` 也交给 LLM 判定，意图阶段预算从 100ms 升到 900ms |
| **A-02** | 报告模板的章节数固定为 5 / 4 / 3，章节标题如 2.2 所示 | 需求只给"3 个模板"，未给章节。给出默认值可让 P2 直接跑通，后续按语料调整 | 若用户需要自定义模板，`report.params_json` 已预留，但大纲生成需支持"给定章节"模式 |
| **A-03** | 单节报告生成沿用 25s 工具链超时，整篇总预算 180s、节数 ≤6 | C-13 的落地。超 6 节时按"合并相近章节"处理，不拆更多节 | 若模板要 10 节，需改为并发生成（但会与在线问答抢 CPU，见架构 2.5） |
| **A-04** | `retrieval.score_threshold` 初值 0.62，作用于 **sigmoid 后的 rerank 分**；若 `llm.rerank.model='none'`，则退化为向量余弦 | 需求只在示例中出现 0.62，未定为规格（C-06）。rerank 模型已按 D-7 定为 bge-reranker-v2-m3，故 0.62 可先按该家族的概率分布起用，P0 标定后写回 | 关闭 rerank 时必须重标（余弦分布与 cross-encoder 概率分布形态不同）；换 embedding 也要重标（4.7.5 第 6 步） |

### 8.3 需求未明确、本文按工程惯例补齐的部分

| 项 | 本文的处理 | 是否需要回头补需求 |
|---|---|---|
| 接口路径与错误码 | 本文第 2 章定义 | 否（需求明确"未涉及"） |
| SSE 事件类型 | 本文 2.3 定义，覆盖原型 7 种终态 | 否 |
| 报告章节文案 | 2.2 给默认值（A-02） | 建议 P2 与内容侧确认 |
| 日志字段与保留期 | 沿用需求 6.4 的 30/180 天 | 否 |
| 备份与迁移 | 架构文档 5.7 | 否 |

---

## 附录 A 配置键速查

| 键 | 默认 | 影响 |
|---|---|---|
| `llm.chat.model` | 待定 | 主模型，换它需重标阈值 |
| `llm.chat.fallback_model` | 待定 | 限流/超时降级目标 |
| `llm.embedding.model` | `BAAI/bge-m3` | 向量模型；换它必须新建 collection + 全量 reindex（D-7、4.7.5） |
| `llm.embedding.revision` | HF commit hash | 锁死权重；留空则启动即报错 |
| `llm.embedding.dim` | 1024 | 与 collection 实际维度比对，不一致拒绝启动 |
| `llm.embedding.query_instruction` | `""` | bge-m3 为空；换 bge-large-zh-v1.5 时必须填其检索指令（只加 query 侧） |
| `llm.embedding.batch_size` | 16 | CPU 16 / GPU 64 |
| `llm.embedding.device` | `cpu` | `cuda` 可选 |
| `llm.embedding.cache_dir` | `data/models` | 配合 `HF_HUB_OFFLINE=1` 离线加载 |
| `llm.rerank.model` | `BAAI/bge-reranker-v2-m3` | `none` 时阈值语义变为余弦；输出须过 sigmoid（4.7.4） |
| `retrieval.top_k_vec` | 20 | 向量路召回数 |
| `retrieval.top_k_bm25` | 20 | 全文路召回数 |
| `retrieval.rerank_top_n` | 8 | 送生成的上限 |
| `retrieval.score_threshold` | 0.62 | **只作用于 rerank 分**（D-5） |
| `retrieval.rrf_k` | 60 | 融合平滑 |
| `index.active_version` | — | 当前 collection 版本 |
| `prompt.qa.version` | `p_v3` | 引证档/解读模板 |
| `limits.context_tokens` | 8192 | 上下文预算 |
| `limits.daily_token_budget` | 待定 | 日限额 |
| `limits.max_tool_calls` | 6 | 工具调用上限 |
| `limits.task_timeout_ms` | 25000 | 单任务超时 |
| `checklist.version` | 7 | 自查清单版本（5/7） |
| `feature.preview_mode` | true | 预览模式开关 |

## 附录 B Prompt 清单

| 版本键 | 文件 | 用途 | 输出格式 | 预算 |
|---|---|---|---|---|
| `p_v3`（qa.brief） | `gen_direct_v3.jinja2` | 引证档：结论 + 引用 + 类型 + 置信度 | Pydantic JSON | max_tokens 400 |
| `p_v3`（qa.explain） | `gen_explain_v3.jinja2` | 白话解读，逐断言挂编号 | 流式文本 | 按剩余预算 |
| `p_v2`（intent） | `intent_v2.jinja2` | 意图 + 改写合并判定 | JSON | max_tokens 200 |
| `p_v2`（rewrite） | `rewrite_v2.jinja2` | 指代消解 + 古今词归一 | JSON | max_tokens 200 |
| `p_v2`（outline） | `outline_v2.jinja2` | 报告大纲 | JSON | max_tokens 600 |
| `p_v3`（section） | `section_v3.jinja2` | 单节成文（含已写摘要） | Markdown + 引用映射 | max_tokens 1200 |
| `p_v3`（polish） | `polish_v3.jinja2` | 四类润色操作 | 结构化 diff | 按段长 |

**约束**：模板变更必须升版本号并记 `sha256`，否则历史 trace 的 `prompt_version` 会指向错误内容，版本对比失去意义。

## 附录 C P1 验收清单

| # | 项 | 判据 |
|---|---|---|
| 1 | 登录 + 2 角色 | user 访问 `/api/admin/**` 全部 403 |
| 2 | 上传 → 解析 → 分段 | 50MB / 500 页 ≤8 分钟；无文本层页被显式标记 |
| 3 | 单轮问答 | TTFT P95 ≤3s（P1 门槛，C-03）；答案含引用卡片 |
| 4 | 引用可定位 | 点编号能取到原文 + 卷次 + 页码 + 片段 id |
| 5 | 拒答 | 无答案问题返回 `refuse` + 2~3 条相近原文，不编造 |
| 6 | 越权拦截 | **AT-01~06 全部通过，拦截率 100%**（非 100% 阻断上线） |
| 7 | 轨迹覆盖 | 100% 请求产生 ≥5 个 span（含拒答/越权；快速失败路径单独统计，C-09 注） |
| 8 | 会话列表 | 新建/重命名/删除；软删 7 天后不可见 |
| 9 | 数据隔离 | user A 的会话/报告，user B 访问返回 404 |
| 10 | 评测集 | 50 题标注完成，`recall@5` 可算 |

**第 6 项是唯一的阻断项**。其余可以带缺陷上线（内测），越权不可以——哪怕只有一条用例失败，也说明权限模型有结构性漏洞，而不是"某个接口忘了加判断"。
