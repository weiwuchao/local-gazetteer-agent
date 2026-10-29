# 中国县市 AI 智能体（local-gazetteer-agent）

面向县志语料的检索增强问答与报告生成系统，按 `doc/设计文档/` 落地。

## 文档

- `doc/需求文档/中国县市AI智能体-需求分析报告-v1.2.md`
- `doc/设计文档/架构设计-中国县市AI智能体-v1.0.md`
- `doc/设计文档/详细设计-中国县市AI智能体-v1.0.md`

## 目录

```
backend/     FastAPI 单进程单体（api / services / agent / rag / workers / repo / core / llm）
frontend/    Vue 3 + Vite SPA（7 个视图）
doc/         需求与设计文档
```

## 本地启动

```bash
# 后端（Python 3.11+，单进程是硬约束，见架构 D-1）
cd backend
poetry install
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --reload

# 前端
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173，/api 代理到 8000
```

首次启动会按 `app/repo/migrations/*.sql` 建库到 `backend/data/app.db`。

## 实施顺序

P0 检索验证 → P1 最小闭环 → P2 多轮与生产 → P3 评测调优，详见架构文档 3.4。
