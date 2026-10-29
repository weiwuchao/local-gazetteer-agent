# backend

FastAPI 单进程应用（`--workers 1` 是硬约束，见架构文档 D-1）。

## 分层与依赖方向

```
api → services → repo / agent / rag / workers / llm → core
```

- `api` 只做协议适配、鉴权依赖、SSE 编码、错误映射。
- `services` 是业务规则与事务边界的唯一位置。
- `agent` 不写 SQL、不碰 IO，只做 8 阶段编排与守卫。
- `rag` 只接收 `SearchScope`，不自行判断权限。
- `repo` 是 SQL 的唯一出口。
- `workers` 只管领取/租约/幂等/重试，业务委托 `services`。

## 启动

```bash
poetry install
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --reload
```

当前为脚手架：路由已注册，业务函数为 `NotImplementedError` 占位。
实现顺序按架构文档 3.4（P0 检索验证 → P1 最小闭环 → P2 → P3）。
