-- 0001_init.sql
-- 中国县市 AI 智能体 · 初始库结构（24 张实体表 + chunk_fts 虚表）
-- 来源：架构设计 v1.0 附录 A；执行顺序即文件顺序，全部语句幂等。
-- 迁移约定：只允许加表/加列/加索引；删列走"新表 + 搬迁 + 改名"（架构 5.7）。
-- user_version 由 app/repo/db.py 按文件名序号统一维护，本文件不写。
-- 字段注释与架构文档附录 A 保持一致，改这里必须同步改附录 A。

PRAGMA foreign_keys = ON;

-- ---------- A 账号与权限 ----------

-- 账号：登录凭据、角色、JWT 版本号与登录失败锁定
CREATE TABLE IF NOT EXISTS user (
  id             TEXT PRIMARY KEY,                                            -- 主键，ULID（u_ 前缀）
  username       TEXT NOT NULL,                                                -- 登录名；软删后允许重建同名
  password_hash  TEXT NOT NULL,                                                -- argon2id 哈希，不存明文
  role           TEXT NOT NULL CHECK (role IN ('admin','user')),               -- 角色：admin 管理员 / user 普通用户
  status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),  -- 账号状态：active 可用 / disabled 停用
  email          TEXT,                                                         -- 邮箱，仅用于通知，不是登录凭据
  token_version  INTEGER NOT NULL DEFAULT 1,                                   -- JWT 版本号；改密/踢下线时 +1，令旧 token 立即失效
  fail_count     INTEGER NOT NULL DEFAULT 0,                                   -- 连续登录失败次数，达到阈值锁号
  locked_until   TEXT,                                                         -- 锁定到期时间；NULL 表示未锁定
  last_login_at  TEXT,                                                         -- 最近一次登录成功时间
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),  -- 创建时间（UTC 毫秒）
  updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),  -- 更新时间（UTC 毫秒）
  deleted_at     TEXT                                                          -- 软删时间；NULL 表示有效
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_user_name ON user(username) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_user_role ON user(role, status);

-- 刷新令牌：按"令牌族"轮换，检测到重放时整族吊销
CREATE TABLE IF NOT EXISTS auth_refresh_token (
  id             TEXT PRIMARY KEY,                                            -- 主键，ULID
  user_id        TEXT NOT NULL REFERENCES user(id),                            -- 归属用户 → user.id
  token_hash     TEXT NOT NULL,                                               -- 刷新令牌的 sha256，库内不存明文
  family_id      TEXT NOT NULL,                                               -- 令牌族 ID；一次登录一个族，重放时整族吊销
  parent_id      TEXT,                                                        -- 轮换前的令牌 ID，串成链便于追查
  user_agent_hash TEXT,                                                       -- UA 哈希，仅用于异常登录识别
  ip_hash        TEXT,                                                        -- 客户端 IP 哈希，脱敏存储
  issued_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),  -- 签发时间
  expires_at     TEXT NOT NULL,                                               -- 过期时间
  revoked_at     TEXT,                                                        -- 吊销时间；NULL 表示未吊销
  revoked_reason TEXT CHECK (revoked_reason IN ('logout','rotate','admin','reuse'))  -- 吊销原因：logout 登出 / rotate 轮换 / admin 管理员踢出 / reuse 重放检测
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_token_hash ON auth_refresh_token(token_hash);
CREATE INDEX IF NOT EXISTS ix_token_user ON auth_refresh_token(user_id, expires_at DESC);
CREATE INDEX IF NOT EXISTS ix_token_family ON auth_refresh_token(family_id);

-- 邀请码：内测准入，带次数上限与过期时间
CREATE TABLE IF NOT EXISTS invite_code (
  code        TEXT PRIMARY KEY,                                               -- 邀请码本身
  note        TEXT,                                                           -- 备注，如"发给谁"
  max_uses    INTEGER NOT NULL DEFAULT 1,                                     -- 最大可用次数
  used_count  INTEGER NOT NULL DEFAULT 0 CHECK (used_count <= max_uses),       -- 已用次数，不得超过 max_uses
  expires_at  TEXT,                                                           -- 过期时间；NULL 表示不过期
  enabled     INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),             -- 是否启用：1 启用 / 0 停用
  created_by  TEXT,                                                           -- 创建人 → user.id
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))     -- 创建时间
);
CREATE INDEX IF NOT EXISTS ix_invite_enabled ON invite_code(enabled, expires_at);

-- ---------- B 区域与语料 ----------

-- 县市：内容的维度，不是租户隔离维度
CREATE TABLE IF NOT EXISTS region (
  id          TEXT PRIMARY KEY,                                               -- 主键，rg_ 前缀
  name        TEXT NOT NULL,                                                  -- 县市全名，全局唯一
  short_name  TEXT,                                                           -- 简称，用于文案与列表
  province    TEXT,                                                           -- 所属省
  city        TEXT,                                                           -- 所属地级市
  aliases_json TEXT CHECK (aliases_json IS NULL OR json_valid(aliases_json)),  -- 历史别名数组（JSON），用于问句里的别名识别
  era_note    TEXT,                                                           -- 默认年代口径说明，如"以 1992 年版为准"
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','hidden')),  -- active 对用户可见 / hidden 隐藏
  sort_order  INTEGER NOT NULL DEFAULT 0,                                     -- 列表排序值，越小越靠前
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))     -- 创建时间
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_region_name ON region(name);

-- 语料主表：文档状态机、发布就绪度、当前检索版本的载体
CREATE TABLE IF NOT EXISTS document (
  id                    TEXT PRIMARY KEY,                                     -- 主键，ULID
  region_id             TEXT NOT NULL REFERENCES region(id),                  -- 所属县市 → region.id
  title                 TEXT NOT NULL,                                        -- 书名，如"扶风县志"
  edition               TEXT,                                                 -- 版本说明，如"陕西人民出版社1992年版"，参与去重
  year_text             TEXT,                                                 -- 成书年代原文，如"民国二十二年"
  year_num              INTEGER,                                              -- 成书年份数字，用于排序与筛选
  source_note           TEXT NOT NULL,                                        -- 来源说明（何处获得、何种扫描件），必填
  status                TEXT NOT NULL CHECK (status IN
                          ('draft','parsing','parse_failed','proofreading','published','offline')),  -- 状态机：draft 草稿 / parsing 解析中 / parse_failed 解析失败 / proofreading 校对中 / published 已发布 / offline 已下线
  version               INTEGER NOT NULL DEFAULT 1,                           -- 乐观锁版本号；并发改元数据时后提交者拿 409
  page_count            INTEGER,                                              -- 总页数
  no_text_pages         INTEGER NOT NULL DEFAULT 0,                           -- 无文本层页数（扫描件未 OCR），不静默跳过
  chunk_count           INTEGER NOT NULL DEFAULT 0,                           -- 片段数，冗余计数以免列表页 count(*)
  searchable_version_id TEXT REFERENCES document_version(id),                 -- 当前参与检索的版本；下线时置 NULL（与 is_searchable=0 同事务）
  active_index_version  TEXT,                                                 -- 当前生效的索引版本（命名里已编码模型指纹）
  dup_key               TEXT NOT NULL,                                        -- sha256(region_id+title+edition)，配部分唯一索引防重复上传
  published_at          TEXT,                                                 -- 发布时间
  publish_checklist_json TEXT CHECK (publish_checklist_json IS NULL OR json_valid(publish_checklist_json)),  -- 发布时 7 项勾选的快照（JSON）
  created_by            TEXT NOT NULL,                                        -- 上传人 → user.id
  created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),  -- 创建时间
  updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),  -- 更新时间
  deleted_at            TEXT                                                  -- 软删时间；NULL 表示有效
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_dup ON document(region_id, dup_key) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_doc_list ON document(status, region_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_doc_search ON document(region_id, status) WHERE deleted_at IS NULL;

-- 文档版本：raw 原文 / polished 润色稿 / published 发布快照，原文只读
CREATE TABLE IF NOT EXISTS document_version (
  id            TEXT PRIMARY KEY,                                             -- 主键，ULID
  doc_id        TEXT NOT NULL REFERENCES document(id),                        -- 所属文档 → document.id
  version_type  TEXT NOT NULL CHECK (version_type IN ('raw','polished','published')),  -- 版本类型：raw 原始提取 / polished 润色稿 / published 发布快照
  content       TEXT NOT NULL,                                                -- 该版本全文
  source_version_id TEXT REFERENCES document_version(id),                     -- 派生来源版本；润色稿指向对应 raw
  model         TEXT,                                                         -- 生成该版本所用模型
  prompt_version TEXT,                                                        -- 生成该版本所用 prompt 版本
  stats_json    TEXT CHECK (stats_json IS NULL OR json_valid(stats_json)),     -- 段数/字数/确认数/AI 采纳率等统计（JSON）
  publish_checklist_json TEXT CHECK (publish_checklist_json IS NULL OR json_valid(publish_checklist_json)),  -- 发布清单快照，仅 published 版本有值
  created_by    TEXT,                                                         -- 创建人 → user.id
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))   -- 创建时间
);
CREATE INDEX IF NOT EXISTS ix_ver_doc ON document_version(doc_id, version_type, created_at DESC);

-- 原文不可变的最后一道闸
CREATE TRIGGER IF NOT EXISTS trg_version_raw_immutable
BEFORE UPDATE ON document_version
WHEN OLD.version_type = 'raw' AND NEW.content <> OLD.content
BEGIN
  SELECT RAISE(ABORT, 'raw version is immutable');
END;

-- 片段：检索最小单元；一张表同时供关键词、向量、引用三种用途
CREATE TABLE IF NOT EXISTS chunk (
  id            INTEGER PRIMARY KEY,                                          -- 主键，同时是 FTS5 外部内容表的 content_rowid
  chunk_id      TEXT NOT NULL,                                                -- 对外片段 ID（c_ 前缀）；引用与前端只用它
  doc_id        TEXT NOT NULL REFERENCES document(id),                        -- 所属文档 → document.id
  version_id    TEXT NOT NULL REFERENCES document_version(id),                -- 所属文档版本 → document_version.id
  region_id     TEXT NOT NULL,                                                -- 冗余县市，供检索可见范围过滤
  seq           INTEGER NOT NULL,                                             -- 文档内片段序号，从 1 开始
  char_start    INTEGER,                                                      -- 在原文中的起始字符偏移
  char_end      INTEGER,                                                      -- 在原文中的结束字符偏移（不含）
  content       TEXT NOT NULL,                                                -- 原字原文，繁体不转；引用展示必须与纸本一致
  content_seg   TEXT NOT NULL,                                                -- 原文 + jieba 空格分词，FTS5 正文索引列
  token_count   INTEGER NOT NULL DEFAULT 0,                                   -- token 估算数，用于拼 prompt 的预算控制
  ocr_flag      INTEGER NOT NULL DEFAULT 0 CHECK (ocr_flag IN (0,1)),          -- 是否来自 OCR：1 是 / 0 否
  sensitive_flag INTEGER NOT NULL DEFAULT 0 CHECK (sensitive_flag IN (0,1)),   -- 是否命中敏感内容：1 是 / 0 否
  is_searchable INTEGER NOT NULL DEFAULT 1 CHECK (is_searchable IN (0,1)),     -- 是否参与检索：1 是 / 0 否（下线时置 0，立即生效）
  index_version TEXT,                                                         -- 写入向量库时使用的索引版本
  index_state   TEXT NOT NULL DEFAULT 'pending' CHECK (index_state IN ('pending','indexed','stale')),  -- 向量状态：pending 待向量化 / indexed 已向量化 / stale 向量已过期
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))   -- 创建时间
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_chunk_cid ON chunk(chunk_id);
CREATE INDEX IF NOT EXISTS ix_chunk_doc ON chunk(doc_id, seq);
CREATE INDEX IF NOT EXISTS ix_chunk_scope ON chunk(region_id, doc_id, is_searchable);
CREATE INDEX IF NOT EXISTS ix_chunk_index ON chunk(index_version, index_state);

-- FTS5 外部内容表：不复制正文，只建索引
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
  content_seg,                                                                -- 正文分词列（BM25 主索引列）
  content='chunk', content_rowid='id',
  tokenize="unicode61 remove_diacritics 2"
);
CREATE TRIGGER IF NOT EXISTS trg_chunk_ai AFTER INSERT ON chunk BEGIN
  INSERT INTO chunk_fts(rowid, content_seg) VALUES (new.id, new.content_seg);
END;
CREATE TRIGGER IF NOT EXISTS trg_chunk_ad AFTER DELETE ON chunk BEGIN
  INSERT INTO chunk_fts(chunk_fts, rowid, content_seg)
  VALUES ('delete', old.id, old.content_seg);
END;
CREATE TRIGGER IF NOT EXISTS trg_chunk_au AFTER UPDATE ON chunk BEGIN
  INSERT INTO chunk_fts(chunk_fts, rowid, content_seg)
  VALUES ('delete', old.id, old.content_seg);
  INSERT INTO chunk_fts(rowid, content_seg) VALUES (new.id, new.content_seg);
END;

-- 润色段：逐段状态机，与文档状态机严格分离
CREATE TABLE IF NOT EXISTS polish_segment (
  id            TEXT PRIMARY KEY,                                             -- 主键，ULID
  doc_id        TEXT NOT NULL REFERENCES document(id),                        -- 所属文档 → document.id
  version_id    TEXT NOT NULL REFERENCES document_version(id),                -- 所属润色版本 → document_version.id
  raw_chunk_id  TEXT,                                                         -- 对应原文片段 → chunk.chunk_id
  seq           INTEGER NOT NULL,                                             -- 版本内段落序号
  raw_text      TEXT NOT NULL,                                                -- 原文段落
  polished_text TEXT,                                                         -- 润色后文本；未生成时为空
  diff_json     TEXT CHECK (diff_json IS NULL OR json_valid(diff_json)),       -- 三色 diff 的操作序列（JSON）
  ops_json      TEXT CHECK (ops_json IS NULL OR json_valid(ops_json)),         -- 白名单操作标签 punct/t2s/segment/gloss（JSON）
  gloss_text    TEXT,                                                         -- 注释/白话对照文本
  state         TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','confirmed','rejected','edited')),  -- 段状态：pending 待确认 / confirmed 已确认 / rejected 已驳回 / edited 人工已改
  sensitive_flag INTEGER NOT NULL DEFAULT 0 CHECK (sensitive_flag IN (0,1)),   -- 是否标记为敏感段：1 是 / 0 否
  sensitive_tags_json TEXT CHECK (sensitive_tags_json IS NULL OR json_valid(sensitive_tags_json)),  -- 敏感标签（JSON）
  edited_by     TEXT,                                                         -- 最后一次人工编辑者 → user.id
  edited_at     TEXT,                                                         -- 最后一次人工编辑时间
  model         TEXT,                                                         -- 生成润色稿所用模型
  prompt_version TEXT,                                                        -- 生成润色稿所用 prompt 版本
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))   -- 创建时间
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_seg ON polish_segment(version_id, seq);
CREATE INDEX IF NOT EXISTS ix_seg_state ON polish_segment(doc_id, state);
CREATE INDEX IF NOT EXISTS ix_seg_sensitive ON polish_segment(version_id, sensitive_flag);

-- ---------- C 异步任务 ----------

-- 异步任务：DB 是真相源，进程内队列只是唤醒信号
CREATE TABLE IF NOT EXISTS task (
  id              TEXT PRIMARY KEY,                                           -- 主键，ULID；对外即 task_id
  kind            TEXT NOT NULL,                                              -- 任务类型，对应 workers/registry 里注册的 handler
  idempotency_key TEXT NOT NULL,                                              -- 幂等键；重复提交返回已有任务而非新建
  status          TEXT NOT NULL DEFAULT 'queued' CHECK (status IN
                    ('queued','running','succeeded','failed','timeout','cancelled')),  -- 状态：queued 排队 / running 执行中 / succeeded 成功 / failed 失败 / timeout 超时 / cancelled 取消
  priority        INTEGER NOT NULL DEFAULT 0,                                 -- 优先级，越大越先被领取
  payload_json    TEXT CHECK (payload_json IS NULL OR json_valid(payload_json)),  -- 任务入参（JSON）
  result_json     TEXT CHECK (result_json IS NULL OR json_valid(result_json)),  -- 任务结果（JSON）
  progress        INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),  -- 进度百分比 0–100
  attempt         INTEGER NOT NULL DEFAULT 0,                                 -- 已尝试次数
  max_attempts    INTEGER NOT NULL DEFAULT 3,                                 -- 最大尝试次数，超过即转 failed
  worker_id       TEXT,                                                       -- 当前执行该任务的 worker 标识
  lease_expires_at TEXT,                                                      -- 租约到期时间；过期后由回收任务重新入队
  scheduled_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),  -- 最早可执行时间（失败退避用）
  started_at      TEXT,                                                       -- 开始执行时间
  finished_at     TEXT,                                                       -- 结束时间
  error_code      TEXT,                                                       -- 错误码（分层错误码，见架构 6.4）
  error_detail    TEXT,                                                       -- 错误详情，便于排障
  created_by      TEXT,                                                       -- 创建人 → user.id
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))  -- 创建时间
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_task_idem ON task(idempotency_key);
CREATE INDEX IF NOT EXISTS ix_task_claim ON task(status, scheduled_at, priority DESC);
CREATE INDEX IF NOT EXISTS ix_task_lease ON task(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS ix_task_owner ON task(created_by, created_at DESC);

-- ---------- D 会话、消息、引用、反馈 ----------

-- 会话：一个会话只聊一个县，承载摘要与记忆槽
CREATE TABLE IF NOT EXISTS conversation (
  id             TEXT PRIMARY KEY,                                            -- 主键，ULID
  user_id        TEXT NOT NULL REFERENCES user(id),                           -- 归属用户 → user.id
  region_id      TEXT NOT NULL REFERENCES region(id),                         -- 绑定的县市 → region.id；一个会话只聊一个县
  title          TEXT NOT NULL DEFAULT '新会话',                              -- 会话标题，可由首轮问题生成
  mode           TEXT NOT NULL DEFAULT 'answer' CHECK (mode IN ('answer','original_only','report','preview')),  -- 会话模式：answer 正常问答 / original_only 只给原文 / report 报告 / preview 预览
  summary        TEXT,                                                        -- 历史摘要；超窗口后压缩写入
  summary_turn   INTEGER,                                                     -- 摘要已覆盖到第几轮
  memory_slots_json TEXT CHECK (memory_slots_json IS NULL OR json_valid(memory_slots_json)),  -- 记忆槽（追问对象、偏好等，JSON）
  turn_count     INTEGER NOT NULL DEFAULT 0,                                  -- 轮次数
  last_message_at TEXT,                                                       -- 最后一条消息时间，列表排序用
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),  -- 创建时间
  updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),  -- 更新时间
  deleted_at     TEXT,                                                        -- 软删时间；NULL 表示有效
  purge_after    TEXT                                                         -- 物理清理时间（软删 7 天后）
);
CREATE INDEX IF NOT EXISTS ix_conv_user ON conversation(user_id, deleted_at, last_message_at DESC);
CREATE INDEX IF NOT EXISTS ix_conv_purge ON conversation(purge_after) WHERE deleted_at IS NOT NULL;

-- 消息：正文 + 结果级指标（延迟、token、三版本号）
CREATE TABLE IF NOT EXISTS message (
  id              TEXT PRIMARY KEY,                                           -- 主键，ULID
  conversation_id TEXT NOT NULL REFERENCES conversation(id),                  -- 所属会话 → conversation.id
  seq             INTEGER NOT NULL,                                           -- 会话内序号，从 1 开始
  role            TEXT NOT NULL CHECK (role IN ('user','assistant')),          -- 角色：user 用户 / assistant 助手
  content         TEXT NOT NULL,                                              -- 消息正文
  answer_type     TEXT CHECK (answer_type IN ('fact','partial','refuse','boundary','report_outline')),  -- 回答类型：fact 有据可答 / partial 部分可答 / refuse 拒答 / boundary 越界 / report_outline 报告大纲
  confidence      TEXT CHECK (confidence IN ('high','medium','low')),          -- 置信度：high / medium / low
  caveats_json    TEXT CHECK (caveats_json IS NULL OR json_valid(caveats_json)),  -- 限定说明（JSON）
  followups_json  TEXT CHECK (followups_json IS NULL OR json_valid(followups_json)),  -- 推荐追问（JSON）
  refusal_json    TEXT CHECK (refusal_json IS NULL OR json_valid(refusal_json)),  -- 拒答信息，含原因码与引导语（JSON）
  citation_count  INTEGER NOT NULL DEFAULT 0,                                 -- 引用条数
  ttft_ms         INTEGER,                                                    -- 首字延迟（毫秒）
  total_ms        INTEGER,                                                    -- 整轮总耗时（毫秒）
  token_in        INTEGER,                                                    -- 输入 token 数
  token_out       INTEGER,                                                    -- 输出 token 数
  model           TEXT,                                                       -- 生成该消息所用模型
  prompt_version  TEXT,                                                       -- prompt 版本
  index_version   TEXT,                                                       -- 检索时使用的索引版本
  trace_id        TEXT,                                                       -- 对应轨迹 → agent_trace.trace_id
  status          TEXT NOT NULL DEFAULT 'complete' CHECK (status IN ('streaming','complete','partial','failed')),  -- 状态：streaming 流式中 / complete 完成 / partial 部分完成 / failed 失败
  error_code      TEXT,                                                       -- 错误码
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),  -- 创建时间
  deleted_at      TEXT                                                        -- 软删时间；NULL 表示有效
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_msg_seq ON message(conversation_id, seq);
CREATE INDEX IF NOT EXISTS ix_msg_trace ON message(trace_id);
CREATE INDEX IF NOT EXISTS ix_msg_created ON message(created_at DESC);

-- 引用：存原文快照 + 片段内字符区间，保证重索引后历史回答仍可复现
CREATE TABLE IF NOT EXISTS citation (
  id             TEXT PRIMARY KEY,                                            -- 主键，ULID
  message_id     TEXT NOT NULL REFERENCES message(id),                        -- 所属消息 → message.id
  no             INTEGER NOT NULL,                                            -- 消息内的引用编号，从 1 开始
  chunk_id       TEXT NOT NULL,                                               -- 命中的片段 → chunk.chunk_id
  doc_id         TEXT NOT NULL,                                               -- 片段所属文档 → document.id
  version_id     TEXT,                                                        -- 片段所属文档版本 → document_version.id
  quote_snapshot TEXT NOT NULL,                                               -- 引用原文快照；重切分后仍能还原当时看到的文字
  span_start     INTEGER,                                                     -- 引用在片段内的起始字符偏移
  span_end       INTEGER,                                                     -- 引用在片段内的结束字符偏移
  supports       TEXT,                                                        -- 该引用支撑答案里的哪句话
  valid          INTEGER NOT NULL DEFAULT 1 CHECK (valid IN (0,1)),            -- 引用是否仍有效：1 有效 / 0 失效
  clicked_at     TEXT,                                                        -- 用户点击引用的时间
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))   -- 创建时间
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cit ON citation(message_id, no);
CREATE INDEX IF NOT EXISTS ix_cit_chunk ON citation(chunk_id);
CREATE INDEX IF NOT EXISTS ix_cit_click ON citation(clicked_at) WHERE clicked_at IS NOT NULL;

-- 反馈：点赞/点踩 + 原因码 + 处理状态
CREATE TABLE IF NOT EXISTS feedback (
  id          TEXT PRIMARY KEY,                                               -- 主键，ULID
  message_id  TEXT NOT NULL REFERENCES message(id),                           -- 被评价的消息 → message.id
  user_id     TEXT NOT NULL REFERENCES user(id),                              -- 评价人 → user.id
  type        TEXT NOT NULL CHECK (type IN ('up','down')),                    -- 态度：up 赞 / down 踩
  reason_code TEXT CHECK (reason_code IN
                ('wrong_fact','wrong_citation','incomplete','refuse_too_strict','unreadable','other')),  -- 原因码：wrong_fact 事实错 / wrong_citation 引用错 / incomplete 不完整 / refuse_too_strict 拒答过严 / unreadable 答非所读 / other 其他
  note        TEXT,                                                           -- 用户补充说明
  trace_id    TEXT,                                                           -- 关联轨迹 → agent_trace.trace_id
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','fixed','invalid')),  -- 处理状态：open 待处理 / fixed 已修复 / invalid 无效反馈
  handled_by  TEXT,                                                           -- 处理人 → user.id
  handled_at  TEXT,                                                           -- 处理时间
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))     -- 创建时间
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_fb ON feedback(message_id, user_id);
CREATE INDEX IF NOT EXISTS ix_fb_list ON feedback(status, created_at DESC);

-- ---------- E 报告与导出 ----------

-- 报告：模板化长文生成的状态载体
CREATE TABLE IF NOT EXISTS report (
  id           TEXT PRIMARY KEY,                                              -- 主键，ULID
  user_id      TEXT NOT NULL REFERENCES user(id),                             -- 发起人 → user.id
  region_id    TEXT NOT NULL REFERENCES region(id),                           -- 报告所属县市 → region.id
  template_key TEXT NOT NULL CHECK (template_key IN ('county_profile','history_evolution','person_profile')),  -- 模板：county_profile 县情概况 / history_evolution 历史沿革 / person_profile 人物小传
  title        TEXT NOT NULL,                                                 -- 报告标题
  status       TEXT NOT NULL CHECK (status IN
                 ('outline_pending','generating','partial','succeeded','failed','cancelled')),  -- 状态：outline_pending 待确认大纲 / generating 生成中 / partial 部分完成 / succeeded 成功 / failed 失败 / cancelled 取消
  outline_json TEXT CHECK (outline_json IS NULL OR json_valid(outline_json)),  -- 大纲结构（JSON）
  params_json  TEXT CHECK (params_json IS NULL OR json_valid(params_json)),    -- 生成参数（JSON）
  word_count   INTEGER,                                                       -- 成文字数
  token_in     INTEGER,                                                       -- 输入 token 数
  token_out    INTEGER,                                                       -- 输出 token 数
  error_code   TEXT,                                                          -- 错误码
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),   -- 创建时间
  finished_at  TEXT,                                                          -- 结束时间
  deleted_at   TEXT                                                           -- 软删时间；NULL 表示有效
);
CREATE INDEX IF NOT EXISTS ix_rep_user ON report(user_id, deleted_at, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_rep_status ON report(status);

-- 报告章节：逐节生成、可单节重试
CREATE TABLE IF NOT EXISTS report_section (
  id            TEXT PRIMARY KEY,                                             -- 主键，ULID
  report_id     TEXT NOT NULL REFERENCES report(id),                          -- 所属报告 → report.id
  seq           INTEGER NOT NULL,                                             -- 章节序号
  heading       TEXT NOT NULL,                                                -- 章节标题
  points_json   TEXT CHECK (points_json IS NULL OR json_valid(points_json)),   -- 该节要点（JSON）
  content_md    TEXT,                                                         -- 章节正文（Markdown）
  status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                  ('pending','running','succeeded','failed','skipped')),       -- 状态：pending 待生成 / running 生成中 / succeeded 成功 / failed 失败 / skipped 跳过
  attempt       INTEGER NOT NULL DEFAULT 0,                                   -- 已尝试次数
  error_code    TEXT,                                                         -- 错误码
  citations_json TEXT CHECK (citations_json IS NULL OR json_valid(citations_json)),  -- 该节引用的片段（JSON）
  token_out     INTEGER,                                                      -- 输出 token 数
  updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))   -- 更新时间
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sec ON report_section(report_id, seq);

-- 导出件：落盘文件 + 限时下载令牌
CREATE TABLE IF NOT EXISTS export_artifact (
  id          TEXT PRIMARY KEY,                                               -- 主键，ULID
  report_id   TEXT NOT NULL REFERENCES report(id),                            -- 所属报告 → report.id
  fmt         TEXT NOT NULL CHECK (fmt IN ('md','docx')),                      -- 导出格式：md / docx
  file_path   TEXT NOT NULL,                                                  -- 文件落盘路径（data/exports 下）
  size_bytes  INTEGER,                                                        -- 文件大小（字节）
  sha256      TEXT,                                                           -- 文件校验和
  token       TEXT NOT NULL,                                                  -- 下载令牌，唯一且限时
  expires_at  TEXT NOT NULL,                                                  -- 令牌过期时间（默认 24 小时）
  status      TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','expired','deleted')),  -- 状态：ready 可下载 / expired 已过期 / deleted 已删除
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))     -- 创建时间
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_exp_token ON export_artifact(token);
CREATE INDEX IF NOT EXISTS ix_exp_report ON export_artifact(report_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_exp_expire ON export_artifact(expires_at);

-- ---------- F 轨迹三表 ----------

-- 轨迹：一条 = 一次请求；用结构替代"模型自述"
CREATE TABLE IF NOT EXISTS agent_trace (
  trace_id       TEXT PRIMARY KEY,                                            -- 主键，一次请求一条
  request_id     TEXT NOT NULL,                                               -- 请求 ID，用于与访问日志关联
  user_id        TEXT,                                                        -- 发起人 → user.id
  role           TEXT,                                                        -- 发起时的角色快照
  session_id     TEXT,                                                        -- 会话 ID → conversation.id
  message_id     TEXT,                                                        -- 对应的助手消息 → message.id
  region_id      TEXT,                                                        -- 请求的县市 → region.id
  mode           TEXT,                                                        -- 会话模式快照
  question_text  TEXT,                                                        -- 脱敏后的问题原文（手机号、证件号已打码）
  question_hash  TEXT,                                                        -- 脱敏后问题的哈希，用于聚合"同一问题反复失败"
  intent         TEXT CHECK (intent IN ('qa','report','chitchat','write_request','unknown')),  -- 意图：qa 问答 / report 报告 / chitchat 闲聊 / write_request 改写请求 / unknown 未知
  answer_type    TEXT,                                                        -- 最终回答类型，取值同 message.answer_type
  status         TEXT NOT NULL CHECK (status IN ('success','degraded','failed')),  -- 结果：success 成功 / degraded 降级 / failed 失败
  total_ms       INTEGER,                                                     -- 端到端总耗时（毫秒）
  token_in       INTEGER,                                                     -- 输入 token 数
  token_out      INTEGER,                                                     -- 输出 token 数
  model          TEXT,                                                        -- 使用的模型
  prompt_version TEXT,                                                        -- prompt 版本
  index_version  TEXT,                                                        -- 索引版本
  span_count     INTEGER NOT NULL DEFAULT 0,                                  -- 阶段数
  event_count    INTEGER NOT NULL DEFAULT 0,                                  -- 事件数
  size_bytes     INTEGER NOT NULL DEFAULT 0,                                  -- 轨迹体积（字节）
  truncated      INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0,1)),        -- 是否因超限截断：1 是 / 0 否
  replay_of      TEXT,                                                        -- 若为重放产生，指向原始 trace_id
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))   -- 创建时间
);
CREATE INDEX IF NOT EXISTS ix_tr_time ON agent_trace(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_tr_msg ON agent_trace(message_id);
CREATE INDEX IF NOT EXISTS ix_tr_filter ON agent_trace(answer_type, status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_tr_user ON agent_trace(user_id, created_at DESC);

-- 阶段：一条 = 一个流水线阶段；存预算与实际耗时，可直接算超预算
CREATE TABLE IF NOT EXISTS agent_span (
  span_id      TEXT PRIMARY KEY,                                              -- 主键，ULID
  trace_id     TEXT NOT NULL REFERENCES agent_trace(trace_id),                -- 所属轨迹 → agent_trace.trace_id
  name         TEXT NOT NULL CHECK (name IN
                 ('auth','intent','rewrite','retrieve','judge','generate','validate','deliver')),  -- 阶段名：auth 鉴权 / intent 意图 / rewrite 改写 / retrieve 检索 / judge 判定 / generate 生成 / validate 校验 / deliver 交付
  seq          INTEGER NOT NULL,                                              -- 阶段顺序号
  duration_ms  INTEGER,                                                       -- 实际耗时（毫秒）
  status       TEXT NOT NULL CHECK (status IN ('ok','degraded','failed','skipped')),  -- 阶段结果：ok 正常 / degraded 降级 / failed 失败 / skipped 跳过
  budget_ms    INTEGER,                                                       -- 该阶段的时间预算（毫秒）
  input_digest TEXT,                                                          -- 输入摘要（哈希），不落原文
  output_digest TEXT,                                                         -- 输出摘要（哈希），不落原文
  error_code   TEXT,                                                          -- 错误码
  started_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))    -- 开始时间
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_span ON agent_span(trace_id, seq);
CREATE INDEX IF NOT EXISTS ix_span_name ON agent_span(name, started_at DESC);

-- 事件：一条 = 一次工具调用/决策/校验；只存摘要与白名单字段
CREATE TABLE IF NOT EXISTS agent_event (
  event_id      TEXT PRIMARY KEY,                                             -- 主键，ULID
  trace_id      TEXT NOT NULL,                                                -- 所属轨迹，冗余一列以省掉轨迹页最常见的 join
  span_id       TEXT NOT NULL REFERENCES agent_span(span_id),                  -- 所属阶段 → agent_span.span_id
  seq           INTEGER NOT NULL,                                             -- 事件顺序号
  type          TEXT NOT NULL CHECK (type IN ('tool_call','decision','validation')),  -- 事件类型：tool_call 工具调用 / decision 决策 / validation 校验
  tool_name     TEXT,                                                         -- 工具名，type=tool_call 时有值
  params_digest TEXT,                                                         -- 入参摘要（哈希），不落原文
  result_count  INTEGER,                                                      -- 结果条数
  top_score     REAL,                                                         -- 最高分，指 rerank 分（0–1），不是 RRF 分
  hit_chunk_ids TEXT CHECK (hit_chunk_ids IS NULL OR json_valid(hit_chunk_ids)),  -- 命中片段前 10 条 {id,score}（JSON），不存正文
  decision      TEXT,                                                         -- 决策结论
  reason_code   TEXT,                                                         -- 决策原因码
  detail        TEXT CHECK (detail IS NULL OR json_valid(detail)),              -- 白名单字段的详情（JSON）；禁止写入自由文本推理（CoT）
  duration_ms   INTEGER,                                                      -- 耗时（毫秒）
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))    -- 创建时间
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ev ON agent_event(trace_id, seq);
CREATE INDEX IF NOT EXISTS ix_ev_tool ON agent_event(tool_name, created_at DESC);

-- ---------- G 运营与配置 ----------

-- 管理操作留痕；与轨迹严格分离（一个是"谁改了什么"，一个是"系统怎么答的"）
CREATE TABLE IF NOT EXISTS operation_log (
  id          TEXT PRIMARY KEY,                                               -- 主键，ULID
  operator_id TEXT NOT NULL,                                                  -- 操作人 → user.id
  action      TEXT NOT NULL CHECK (action IN
                ('doc.upload','doc.parse','doc.publish','doc.offline','doc.rollback','doc.delete',
                 'polish.confirm','user.disable','user.enable','invite.create','config.update',
                 'export.download')),                                         -- 动作：doc.upload 上传 / doc.parse 解析 / doc.publish 发布 / doc.offline 下线 / doc.rollback 回滚 / doc.delete 删除 / polish.confirm 润色确认 / user.disable 停用用户 / user.enable 启用用户 / invite.create 生成邀请码 / config.update 改配置 / export.download 下载导出
  target_type TEXT NOT NULL,                                                  -- 操作对象类型（document / user / config …）
  target_id   TEXT,                                                           -- 操作对象 ID
  result      TEXT NOT NULL CHECK (result IN ('ok','denied','failed')),        -- 结果：ok 成功 / denied 无权限 / failed 失败
  ip_hash     TEXT,                                                           -- 操作人 IP 哈希，脱敏存储
  detail_json TEXT CHECK (detail_json IS NULL OR json_valid(detail_json)),     -- 变更详情（JSON），如发布前后的字段差异
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))     -- 创建时间
);
CREATE INDEX IF NOT EXISTS ix_oplog_target ON operation_log(target_type, target_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_oplog_time ON operation_log(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_oplog_actor ON operation_log(operator_id, created_at DESC);

-- 评测样本：可人工编写，也可由真实轨迹转成
CREATE TABLE IF NOT EXISTS eval_case (
  id                TEXT PRIMARY KEY,                                         -- 主键，ULID
  dataset_key       TEXT NOT NULL,                                            -- 数据集标识
  dataset_version   TEXT NOT NULL,                                            -- 数据集版本
  region_id         TEXT,                                                     -- 所属县市 → region.id
  question          TEXT NOT NULL,                                            -- 问题
  expected_answer   TEXT,                                                     -- 期望答案
  expected_chunk_ids TEXT CHECK (expected_chunk_ids IS NULL OR json_valid(expected_chunk_ids)),  -- 期望命中的片段（JSON）
  answerable        INTEGER NOT NULL DEFAULT 1 CHECK (answerable IN (0,1)),     -- 是否可答：1 语料里有答案 / 0 应拒答
  tags_json         TEXT CHECK (tags_json IS NULL OR json_valid(tags_json)),   -- 标签（JSON），如题型、难度
  source_trace_id   TEXT,                                                     -- 若由轨迹转样本，记原始 trace_id
  label_status      TEXT NOT NULL DEFAULT 'draft' CHECK (label_status IN ('draft','reviewed','frozen')),  -- 标注状态：draft 草稿 / reviewed 已复核 / frozen 已冻结
  created_by        TEXT,                                                     -- 创建人 → user.id
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),  -- 创建时间
  deleted_at        TEXT                                                      -- 软删时间；NULL 表示有效
);
CREATE INDEX IF NOT EXISTS ix_case_set ON eval_case(dataset_key, dataset_version, label_status);
CREATE INDEX IF NOT EXISTS ix_case_src ON eval_case(source_trace_id);

-- 评测运行：一次实验台账，快照当次配置与逐条结果
CREATE TABLE IF NOT EXISTS eval_run (
  id                TEXT PRIMARY KEY,                                         -- 主键，ULID
  dataset_key       TEXT NOT NULL,                                            -- 评测的数据集标识
  dataset_version   TEXT NOT NULL,                                            -- 评测的数据集版本
  config_json       TEXT NOT NULL CHECK (json_valid(config_json)),             -- 本次运行的配置快照（JSON），必填
  status            TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed')),  -- 状态：queued 排队 / running 运行中 / succeeded 成功 / failed 失败
  metrics_json      TEXT CHECK (metrics_json IS NULL OR json_valid(metrics_json)),  -- 汇总指标（JSON），如召回率、引用准确率
  case_results_json TEXT CHECK (case_results_json IS NULL OR json_valid(case_results_json)),  -- 逐条用例结果（JSON）
  started_at        TEXT,                                                     -- 开始时间
  finished_at       TEXT,                                                     -- 结束时间
  created_by        TEXT                                                      -- 发起人 → user.id
);
CREATE INDEX IF NOT EXISTS ix_run_set ON eval_run(dataset_key, dataset_version, started_at DESC);

-- 配置中心：阈值、prompt 版本、模型参数、限额
CREATE TABLE IF NOT EXISTS app_config (
  key         TEXT PRIMARY KEY,                                               -- 配置键，如 llm.embedding.model
  value_json  TEXT NOT NULL CHECK (json_valid(value_json)),                     -- 配置值，统一 JSON 编码存储
  value_type  TEXT NOT NULL CHECK (value_type IN ('int','float','string','bool','json')),  -- 值类型：int / float / string / bool / json
  version     INTEGER NOT NULL DEFAULT 1,                                     -- 配置版本，每次更新 +1（用于缓存失效与审计）
  description TEXT,                                                           -- 配置说明
  updated_by  TEXT,                                                           -- 最后修改人 → user.id
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))     -- 最后修改时间
);

-- 日聚合指标：按天 + 指标 + 两个维度预聚合，避免实时扫全表
CREATE TABLE IF NOT EXISTS metric_daily (
  stat_date    TEXT NOT NULL,                                                 -- 统计日期，YYYY-MM-DD
  metric_key   TEXT NOT NULL,                                                 -- 指标名
  dim1         TEXT NOT NULL DEFAULT '',                                      -- 维度 1（如 region_id）；空串表示无此维度
  dim2         TEXT NOT NULL DEFAULT '',                                      -- 维度 2（如 status）；空串表示无此维度
  value_num    REAL,                                                          -- 标量结果
  value_json   TEXT CHECK (value_json IS NULL OR json_valid(value_json)),      -- 非标量结果（JSON）
  sample_count INTEGER NOT NULL DEFAULT 0,                                    -- 参与聚合的样本数
  updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),    -- 最后更新时间
  PRIMARY KEY (stat_date, metric_key, dim1, dim2)
);
CREATE INDEX IF NOT EXISTS ix_metric_key ON metric_daily(metric_key, stat_date DESC);
