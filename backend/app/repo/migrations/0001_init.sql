-- 0001_init.sql
-- 中国县市 AI 智能体 · 初始库结构（24 张实体表 + chunk_fts 虚表）
-- 来源：架构设计 v1.0 附录 A；执行顺序即文件顺序，全部语句幂等。
-- 迁移约定：只允许加表/加列/加索引；删列走"新表 + 搬迁 + 改名"（架构 5.7）。
-- user_version 由 app/repo/db.py 按文件名序号统一维护，本文件不写。

PRAGMA foreign_keys = ON;

-- ---------- A 账号与权限 ----------
CREATE TABLE IF NOT EXISTS user (
  id             TEXT PRIMARY KEY,
  username       TEXT NOT NULL,
  password_hash  TEXT NOT NULL,
  role           TEXT NOT NULL CHECK (role IN ('admin','user')),
  status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  email          TEXT,
  token_version  INTEGER NOT NULL DEFAULT 1,
  fail_count     INTEGER NOT NULL DEFAULT 0,
  locked_until   TEXT,
  last_login_at  TEXT,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  deleted_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_user_name ON user(username) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_user_role ON user(role, status);

CREATE TABLE IF NOT EXISTS auth_refresh_token (
  id             TEXT PRIMARY KEY,
  user_id        TEXT NOT NULL REFERENCES user(id),
  token_hash     TEXT NOT NULL,
  family_id      TEXT NOT NULL,
  parent_id      TEXT,
  user_agent_hash TEXT,
  ip_hash        TEXT,
  issued_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  expires_at     TEXT NOT NULL,
  revoked_at     TEXT,
  revoked_reason TEXT CHECK (revoked_reason IN ('logout','rotate','admin','reuse'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_token_hash ON auth_refresh_token(token_hash);
CREATE INDEX IF NOT EXISTS ix_token_user ON auth_refresh_token(user_id, expires_at DESC);
CREATE INDEX IF NOT EXISTS ix_token_family ON auth_refresh_token(family_id);

CREATE TABLE IF NOT EXISTS invite_code (
  code        TEXT PRIMARY KEY,
  note        TEXT,
  max_uses    INTEGER NOT NULL DEFAULT 1,
  used_count  INTEGER NOT NULL DEFAULT 0 CHECK (used_count <= max_uses),
  expires_at  TEXT,
  enabled     INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
  created_by  TEXT,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_invite_enabled ON invite_code(enabled, expires_at);

-- ---------- B 区域与语料 ----------
CREATE TABLE IF NOT EXISTS region (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  short_name  TEXT,
  province    TEXT,
  city        TEXT,
  aliases_json TEXT CHECK (aliases_json IS NULL OR json_valid(aliases_json)),
  era_note    TEXT,
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','hidden')),
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_region_name ON region(name);

CREATE TABLE IF NOT EXISTS document (
  id                    TEXT PRIMARY KEY,
  region_id             TEXT NOT NULL REFERENCES region(id),
  title                 TEXT NOT NULL,
  edition               TEXT,
  year_text             TEXT,
  year_num              INTEGER,
  source_note           TEXT NOT NULL,
  status                TEXT NOT NULL CHECK (status IN
                          ('draft','parsing','parse_failed','proofreading','published','offline')),
  version               INTEGER NOT NULL DEFAULT 1,
  page_count            INTEGER,
  no_text_pages         INTEGER NOT NULL DEFAULT 0,
  chunk_count           INTEGER NOT NULL DEFAULT 0,
  searchable_version_id TEXT REFERENCES document_version(id),
  active_index_version  TEXT,
  dup_key               TEXT NOT NULL,
  published_at          TEXT,
  publish_checklist_json TEXT CHECK (publish_checklist_json IS NULL OR json_valid(publish_checklist_json)),
  created_by            TEXT NOT NULL,
  created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  deleted_at            TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_dup ON document(region_id, dup_key) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_doc_list ON document(status, region_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_doc_search ON document(region_id, status) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS document_version (
  id            TEXT PRIMARY KEY,
  doc_id        TEXT NOT NULL REFERENCES document(id),
  version_type  TEXT NOT NULL CHECK (version_type IN ('raw','polished','published')),
  content       TEXT NOT NULL,
  source_version_id TEXT REFERENCES document_version(id),
  model         TEXT,
  prompt_version TEXT,
  stats_json    TEXT CHECK (stats_json IS NULL OR json_valid(stats_json)),
  publish_checklist_json TEXT CHECK (publish_checklist_json IS NULL OR json_valid(publish_checklist_json)),
  created_by    TEXT,
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_ver_doc ON document_version(doc_id, version_type, created_at DESC);

-- 原文不可变的最后一道闸
CREATE TRIGGER IF NOT EXISTS trg_version_raw_immutable
BEFORE UPDATE ON document_version
WHEN OLD.version_type = 'raw' AND NEW.content <> OLD.content
BEGIN
  SELECT RAISE(ABORT, 'raw version is immutable');
END;

CREATE TABLE IF NOT EXISTS chunk (
  id            INTEGER PRIMARY KEY,
  chunk_id      TEXT NOT NULL,
  doc_id        TEXT NOT NULL REFERENCES document(id),
  version_id    TEXT NOT NULL REFERENCES document_version(id),
  region_id     TEXT NOT NULL,
  volume        TEXT,
  volume_no     INTEGER,
  category      TEXT,
  seq           INTEGER NOT NULL,
  char_start    INTEGER,
  char_end      INTEGER,
  content       TEXT NOT NULL,
  content_seg   TEXT NOT NULL,
  volume_seg    TEXT,
  category_seg  TEXT,
  token_count   INTEGER NOT NULL DEFAULT 0,
  ocr_flag      INTEGER NOT NULL DEFAULT 0 CHECK (ocr_flag IN (0,1)),
  sensitive_flag INTEGER NOT NULL DEFAULT 0 CHECK (sensitive_flag IN (0,1)),
  is_searchable INTEGER NOT NULL DEFAULT 1 CHECK (is_searchable IN (0,1)),
  index_version TEXT,
  index_state   TEXT NOT NULL DEFAULT 'pending' CHECK (index_state IN ('pending','indexed','stale')),
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_chunk_cid ON chunk(chunk_id);
CREATE INDEX IF NOT EXISTS ix_chunk_doc ON chunk(doc_id, seq);
CREATE INDEX IF NOT EXISTS ix_chunk_scope ON chunk(region_id, doc_id, is_searchable);
CREATE INDEX IF NOT EXISTS ix_chunk_index ON chunk(index_version, index_state);

-- FTS5 外部内容表：不复制正文，只建索引
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
  content_seg, volume_seg, category_seg,
  content='chunk', content_rowid='id',
  tokenize="unicode61 remove_diacritics 2"
);
CREATE TRIGGER IF NOT EXISTS trg_chunk_ai AFTER INSERT ON chunk BEGIN
  INSERT INTO chunk_fts(rowid, content_seg, volume_seg, category_seg)
  VALUES (new.id, new.content_seg, new.volume_seg, new.category_seg);
END;
CREATE TRIGGER IF NOT EXISTS trg_chunk_ad AFTER DELETE ON chunk BEGIN
  INSERT INTO chunk_fts(chunk_fts, rowid, content_seg, volume_seg, category_seg)
  VALUES ('delete', old.id, old.content_seg, old.volume_seg, old.category_seg);
END;
CREATE TRIGGER IF NOT EXISTS trg_chunk_au AFTER UPDATE ON chunk BEGIN
  INSERT INTO chunk_fts(chunk_fts, rowid, content_seg, volume_seg, category_seg)
  VALUES ('delete', old.id, old.content_seg, old.volume_seg, old.category_seg);
  INSERT INTO chunk_fts(rowid, content_seg, volume_seg, category_seg)
  VALUES (new.id, new.content_seg, new.volume_seg, new.category_seg);
END;

CREATE TABLE IF NOT EXISTS polish_segment (
  id            TEXT PRIMARY KEY,
  doc_id        TEXT NOT NULL REFERENCES document(id),
  version_id    TEXT NOT NULL REFERENCES document_version(id),
  raw_chunk_id  TEXT,
  seq           INTEGER NOT NULL,
  raw_text      TEXT NOT NULL,
  polished_text TEXT,
  diff_json     TEXT CHECK (diff_json IS NULL OR json_valid(diff_json)),
  ops_json      TEXT CHECK (ops_json IS NULL OR json_valid(ops_json)),
  gloss_text    TEXT,
  state         TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','confirmed','rejected','edited')),
  sensitive_flag INTEGER NOT NULL DEFAULT 0 CHECK (sensitive_flag IN (0,1)),
  sensitive_tags_json TEXT CHECK (sensitive_tags_json IS NULL OR json_valid(sensitive_tags_json)),
  edited_by     TEXT,
  edited_at     TEXT,
  model         TEXT,
  prompt_version TEXT,
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_seg ON polish_segment(version_id, seq);
CREATE INDEX IF NOT EXISTS ix_seg_state ON polish_segment(doc_id, state);
CREATE INDEX IF NOT EXISTS ix_seg_sensitive ON polish_segment(version_id, sensitive_flag);

-- ---------- C 异步任务 ----------
CREATE TABLE IF NOT EXISTS task (
  id              TEXT PRIMARY KEY,
  kind            TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'queued' CHECK (status IN
                    ('queued','running','succeeded','failed','timeout','cancelled')),
  priority        INTEGER NOT NULL DEFAULT 0,
  payload_json    TEXT CHECK (payload_json IS NULL OR json_valid(payload_json)),
  result_json     TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
  progress        INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  attempt         INTEGER NOT NULL DEFAULT 0,
  max_attempts    INTEGER NOT NULL DEFAULT 3,
  worker_id       TEXT,
  lease_expires_at TEXT,
  scheduled_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  started_at      TEXT,
  finished_at     TEXT,
  error_code      TEXT,
  error_detail    TEXT,
  created_by      TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_task_idem ON task(idempotency_key);
CREATE INDEX IF NOT EXISTS ix_task_claim ON task(status, scheduled_at, priority DESC);
CREATE INDEX IF NOT EXISTS ix_task_lease ON task(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS ix_task_owner ON task(created_by, created_at DESC);

-- ---------- D 会话、消息、引用、反馈 ----------
CREATE TABLE IF NOT EXISTS conversation (
  id             TEXT PRIMARY KEY,
  user_id        TEXT NOT NULL REFERENCES user(id),
  region_id      TEXT NOT NULL REFERENCES region(id),
  title          TEXT NOT NULL DEFAULT '新会话',
  mode           TEXT NOT NULL DEFAULT 'answer' CHECK (mode IN ('answer','original_only','report','preview')),
  summary        TEXT,
  summary_turn   INTEGER,
  memory_slots_json TEXT CHECK (memory_slots_json IS NULL OR json_valid(memory_slots_json)),
  turn_count     INTEGER NOT NULL DEFAULT 0,
  last_message_at TEXT,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  deleted_at     TEXT,
  purge_after    TEXT
);
CREATE INDEX IF NOT EXISTS ix_conv_user ON conversation(user_id, deleted_at, last_message_at DESC);
CREATE INDEX IF NOT EXISTS ix_conv_purge ON conversation(purge_after) WHERE deleted_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS message (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversation(id),
  seq             INTEGER NOT NULL,
  role            TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content         TEXT NOT NULL,
  answer_type     TEXT CHECK (answer_type IN ('fact','partial','refuse','boundary','report_outline')),
  confidence      TEXT CHECK (confidence IN ('high','medium','low')),
  caveats_json    TEXT CHECK (caveats_json IS NULL OR json_valid(caveats_json)),
  followups_json  TEXT CHECK (followups_json IS NULL OR json_valid(followups_json)),
  refusal_json    TEXT CHECK (refusal_json IS NULL OR json_valid(refusal_json)),
  citation_count  INTEGER NOT NULL DEFAULT 0,
  ttft_ms         INTEGER,
  total_ms        INTEGER,
  token_in        INTEGER,
  token_out       INTEGER,
  model           TEXT,
  prompt_version  TEXT,
  index_version   TEXT,
  trace_id        TEXT,
  status          TEXT NOT NULL DEFAULT 'complete' CHECK (status IN ('streaming','complete','partial','failed')),
  error_code      TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  deleted_at      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_msg_seq ON message(conversation_id, seq);
CREATE INDEX IF NOT EXISTS ix_msg_trace ON message(trace_id);
CREATE INDEX IF NOT EXISTS ix_msg_created ON message(created_at DESC);

CREATE TABLE IF NOT EXISTS citation (
  id             TEXT PRIMARY KEY,
  message_id     TEXT NOT NULL REFERENCES message(id),
  no             INTEGER NOT NULL,
  chunk_id       TEXT NOT NULL,
  doc_id         TEXT NOT NULL,
  version_id     TEXT,
  volume         TEXT,
  quote_snapshot TEXT NOT NULL,
  span_start     INTEGER,
  span_end       INTEGER,
  supports       TEXT,
  valid          INTEGER NOT NULL DEFAULT 1 CHECK (valid IN (0,1)),
  clicked_at     TEXT,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cit ON citation(message_id, no);
CREATE INDEX IF NOT EXISTS ix_cit_chunk ON citation(chunk_id);
CREATE INDEX IF NOT EXISTS ix_cit_click ON citation(clicked_at) WHERE clicked_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS feedback (
  id          TEXT PRIMARY KEY,
  message_id  TEXT NOT NULL REFERENCES message(id),
  user_id     TEXT NOT NULL REFERENCES user(id),
  type        TEXT NOT NULL CHECK (type IN ('up','down')),
  reason_code TEXT CHECK (reason_code IN
                ('wrong_fact','wrong_citation','incomplete','refuse_too_strict','unreadable','other')),
  note        TEXT,
  trace_id    TEXT,
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','fixed','invalid')),
  handled_by  TEXT,
  handled_at  TEXT,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_fb ON feedback(message_id, user_id);
CREATE INDEX IF NOT EXISTS ix_fb_list ON feedback(status, created_at DESC);

-- ---------- E 报告与导出 ----------
CREATE TABLE IF NOT EXISTS report (
  id           TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES user(id),
  region_id    TEXT NOT NULL REFERENCES region(id),
  template_key TEXT NOT NULL CHECK (template_key IN ('county_profile','history_evolution','person_profile')),
  title        TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN
                 ('outline_pending','generating','partial','succeeded','failed','cancelled')),
  outline_json TEXT CHECK (outline_json IS NULL OR json_valid(outline_json)),
  params_json  TEXT CHECK (params_json IS NULL OR json_valid(params_json)),
  word_count   INTEGER,
  token_in     INTEGER,
  token_out    INTEGER,
  error_code   TEXT,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  finished_at  TEXT,
  deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_rep_user ON report(user_id, deleted_at, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_rep_status ON report(status);

CREATE TABLE IF NOT EXISTS report_section (
  id            TEXT PRIMARY KEY,
  report_id     TEXT NOT NULL REFERENCES report(id),
  seq           INTEGER NOT NULL,
  heading       TEXT NOT NULL,
  points_json   TEXT CHECK (points_json IS NULL OR json_valid(points_json)),
  content_md    TEXT,
  status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                  ('pending','running','succeeded','failed','skipped')),
  attempt       INTEGER NOT NULL DEFAULT 0,
  error_code    TEXT,
  citations_json TEXT CHECK (citations_json IS NULL OR json_valid(citations_json)),
  token_out     INTEGER,
  updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sec ON report_section(report_id, seq);

CREATE TABLE IF NOT EXISTS export_artifact (
  id          TEXT PRIMARY KEY,
  report_id   TEXT NOT NULL REFERENCES report(id),
  fmt         TEXT NOT NULL CHECK (fmt IN ('md','docx')),
  file_path   TEXT NOT NULL,
  size_bytes  INTEGER,
  sha256      TEXT,
  token       TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','expired','deleted')),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_exp_token ON export_artifact(token);
CREATE INDEX IF NOT EXISTS ix_exp_report ON export_artifact(report_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_exp_expire ON export_artifact(expires_at);

-- ---------- F 轨迹三表 ----------
CREATE TABLE IF NOT EXISTS agent_trace (
  trace_id       TEXT PRIMARY KEY,
  request_id     TEXT NOT NULL,
  user_id        TEXT,
  role           TEXT,
  session_id     TEXT,
  message_id     TEXT,
  region_id      TEXT,
  mode           TEXT,
  question_text  TEXT,
  question_hash  TEXT,
  intent         TEXT CHECK (intent IN ('qa','report','chitchat','write_request','unknown')),
  answer_type    TEXT,
  status         TEXT NOT NULL CHECK (status IN ('success','degraded','failed')),
  total_ms       INTEGER,
  token_in       INTEGER,
  token_out      INTEGER,
  model          TEXT,
  prompt_version TEXT,
  index_version  TEXT,
  span_count     INTEGER NOT NULL DEFAULT 0,
  event_count    INTEGER NOT NULL DEFAULT 0,
  size_bytes     INTEGER NOT NULL DEFAULT 0,
  truncated      INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0,1)),
  replay_of      TEXT,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_tr_time ON agent_trace(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_tr_msg ON agent_trace(message_id);
CREATE INDEX IF NOT EXISTS ix_tr_filter ON agent_trace(answer_type, status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_tr_user ON agent_trace(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_span (
  span_id      TEXT PRIMARY KEY,
  trace_id     TEXT NOT NULL REFERENCES agent_trace(trace_id),
  name         TEXT NOT NULL CHECK (name IN
                 ('auth','intent','rewrite','retrieve','judge','generate','validate','deliver')),
  seq          INTEGER NOT NULL,
  duration_ms  INTEGER,
  status       TEXT NOT NULL CHECK (status IN ('ok','degraded','failed','skipped')),
  budget_ms    INTEGER,
  input_digest TEXT,
  output_digest TEXT,
  error_code   TEXT,
  started_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_span ON agent_span(trace_id, seq);
CREATE INDEX IF NOT EXISTS ix_span_name ON agent_span(name, started_at DESC);

CREATE TABLE IF NOT EXISTS agent_event (
  event_id      TEXT PRIMARY KEY,
  trace_id      TEXT NOT NULL,
  span_id       TEXT NOT NULL REFERENCES agent_span(span_id),
  seq           INTEGER NOT NULL,
  type          TEXT NOT NULL CHECK (type IN ('tool_call','decision','validation')),
  tool_name     TEXT,
  params_digest TEXT,
  result_count  INTEGER,
  top_score     REAL,
  hit_chunk_ids TEXT CHECK (hit_chunk_ids IS NULL OR json_valid(hit_chunk_ids)),
  decision      TEXT,
  reason_code   TEXT,
  detail        TEXT CHECK (detail IS NULL OR json_valid(detail)),
  duration_ms   INTEGER,
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ev ON agent_event(trace_id, seq);
CREATE INDEX IF NOT EXISTS ix_ev_tool ON agent_event(tool_name, created_at DESC);

-- ---------- G 运营与配置 ----------
CREATE TABLE IF NOT EXISTS operation_log (
  id          TEXT PRIMARY KEY,
  operator_id TEXT NOT NULL,
  action      TEXT NOT NULL CHECK (action IN
                ('doc.upload','doc.parse','doc.publish','doc.offline','doc.rollback','doc.delete',
                 'polish.confirm','user.disable','user.enable','invite.create','config.update',
                 'export.download')),
  target_type TEXT NOT NULL,
  target_id   TEXT,
  result      TEXT NOT NULL CHECK (result IN ('ok','denied','failed')),
  ip_hash     TEXT,
  detail_json TEXT CHECK (detail_json IS NULL OR json_valid(detail_json)),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_oplog_target ON operation_log(target_type, target_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_oplog_time ON operation_log(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_oplog_actor ON operation_log(operator_id, created_at DESC);

CREATE TABLE IF NOT EXISTS eval_case (
  id                TEXT PRIMARY KEY,
  dataset_key       TEXT NOT NULL,
  dataset_version   TEXT NOT NULL,
  region_id         TEXT,
  question          TEXT NOT NULL,
  expected_answer   TEXT,
  expected_chunk_ids TEXT CHECK (expected_chunk_ids IS NULL OR json_valid(expected_chunk_ids)),
  answerable        INTEGER NOT NULL DEFAULT 1 CHECK (answerable IN (0,1)),
  tags_json         TEXT CHECK (tags_json IS NULL OR json_valid(tags_json)),
  source_trace_id   TEXT,
  label_status      TEXT NOT NULL DEFAULT 'draft' CHECK (label_status IN ('draft','reviewed','frozen')),
  created_by        TEXT,
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  deleted_at        TEXT
);
CREATE INDEX IF NOT EXISTS ix_case_set ON eval_case(dataset_key, dataset_version, label_status);
CREATE INDEX IF NOT EXISTS ix_case_src ON eval_case(source_trace_id);

CREATE TABLE IF NOT EXISTS eval_run (
  id                TEXT PRIMARY KEY,
  dataset_key       TEXT NOT NULL,
  dataset_version   TEXT NOT NULL,
  config_json       TEXT NOT NULL CHECK (json_valid(config_json)),
  status            TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed')),
  metrics_json      TEXT CHECK (metrics_json IS NULL OR json_valid(metrics_json)),
  case_results_json TEXT CHECK (case_results_json IS NULL OR json_valid(case_results_json)),
  started_at        TEXT,
  finished_at       TEXT,
  created_by        TEXT
);
CREATE INDEX IF NOT EXISTS ix_run_set ON eval_run(dataset_key, dataset_version, started_at DESC);

CREATE TABLE IF NOT EXISTS app_config (
  key         TEXT PRIMARY KEY,
  value_json  TEXT NOT NULL CHECK (json_valid(value_json)),
  value_type  TEXT NOT NULL CHECK (value_type IN ('int','float','string','bool','json')),
  version     INTEGER NOT NULL DEFAULT 1,
  description TEXT,
  updated_by  TEXT,
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS metric_daily (
  stat_date    TEXT NOT NULL,
  metric_key   TEXT NOT NULL,
  dim1         TEXT NOT NULL DEFAULT '',
  dim2         TEXT NOT NULL DEFAULT '',
  value_num    REAL,
  value_json   TEXT CHECK (value_json IS NULL OR json_valid(value_json)),
  sample_count INTEGER NOT NULL DEFAULT 0,
  updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (stat_date, metric_key, dim1, dim2)
);
CREATE INDEX IF NOT EXISTS ix_metric_key ON metric_daily(metric_key, stat_date DESC);
