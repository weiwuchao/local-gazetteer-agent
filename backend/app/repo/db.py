"""SQLite 连接、版本化迁移与单写线程队列（WriteQueue）。

设计依据：
- 架构文档 5.1（SQLite 七条约定）、5.7（迁移与备份）；
- 详细设计 G-5（WAL + 单写线程 + BEGIN IMMEDIATE + 30ms 微批）。

三条硬规则（改动本文件前先读）：
1. 写操作只能走 WriteQueue：SQLite 同一时刻只有一个写者，多线程直写必然
   ``database is locked``；读用 ``read_connection()``，每次请求独立连接。
2. 事务内禁止任何网络调用（LLM/HTTP/文件下载）。慢事务会持锁数秒，
   20 并发下会把 SSE 一起拖死；超过 200ms 会打 warning。
3. 迁移只允许"加表/加列/加索引"，删列走"新表 + 数据搬迁 + 改名"，
   且必须在备份后执行（架构 5.7）。版本号由文件名序号维护，不手工改。
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

log = logging.getLogger(__name__)

T = TypeVar("T")

# backend/app/repo/db.py → parents[2] = backend/
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# 连接级设置（5.1 最后一行）。busy_timeout 是最后一道缓冲，不是并发方案；
# 真正的串行化靠 WriteQueue 的单写线程。
_BUSY_TIMEOUT_MS = 5_000
_CONNECT_TIMEOUT_S = _BUSY_TIMEOUT_MS / 1000
_PRAGMAS: tuple[str, ...] = (
    "PRAGMA journal_mode=WAL",       # 读写并发；写与写仍串行
    "PRAGMA synchronous=NORMAL",     # WAL 下兼顾安全与吞吐
    "PRAGMA foreign_keys=ON",        # 外键约束必须显式开启
    f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}",
    "PRAGMA temp_store=MEMORY",      # 临时表/排序不落盘
)

# 写批次参数：首个任务入队后最多再等 30ms，凑满 100 条即提交。
# 30ms 是"延迟换吞吐"的折中：单条写 P95 仍 <50ms，批量导入则显著减少 fsync。
_BATCH_WINDOW_S = 0.03
_MAX_BATCH = 100
_QUEUE_MAXSIZE = 2_000
_SLOW_TX_MS = 200

# 队列停止信号。用哨兵对象而不是 None，避免与正常任务混淆。
_STOP = object()


# ---------------------------------------------------------------- 连接与迁移


def default_db_path() -> Path:
    """默认库位置：``<backend>/data/app.db``。

    相对路径按 backend 目录解析，而不是进程 cwd——避免从仓库根目录启动时
    把库建到 ``./data``，与测试/运维脚本读到两个不同的库。
    """
    # 延迟导入：只用显式 db_path 的脚本（迁移、评测）不必依赖配置层。
    from app.core.config import get_settings

    data_dir = Path(get_settings().data_dir).expanduser()
    if not data_dir.is_absolute():
        data_dir = (_BACKEND_ROOT / data_dir).resolve()
    return data_dir / "app.db"


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开连接并应用连接级 PRAGMA。

    - ``isolation_level=None``：关闭 sqlite3 的隐式事务，由调用方显式
      ``BEGIN IMMEDIATE`` / ``COMMIT``；否则 Python 会在 DML 前偷偷开事务，
      事务边界不可见，正是 G-5 要避免的。
    - ``row_factory=Row``：repo 层按列名取值，避免下标错位。
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        timeout=_CONNECT_TIMEOUT_S,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    return conn


def split_statements(sql: str) -> Iterator[str]:
    """把迁移脚本切成单条语句。

    不能简单 ``sql.split(";")``：``CREATE TRIGGER ... BEGIN ... END;`` 的
    触发器体内含分号，按分号硬切会得到语法残缺的片段。
    ``sqlite3.complete_statement`` 能识别触发器边界与注释，因此逐行累积，
    直到当前缓冲是一条完整语句再产出。
    """
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement and statement != ";" and not _is_comment_only(statement):
                yield statement
            buffer = ""
    tail = buffer.strip()
    if tail and not _is_comment_only(tail):
        yield tail


def _is_comment_only(statement: str) -> bool:
    """纯注释块不作为 SQL 执行（sqlite3 对空语句会报错）。"""
    return all(
        not line.strip() or line.strip().startswith("--")
        for line in statement.splitlines()
    )


def _migration_files() -> list[tuple[int, Path]]:
    """发现迁移文件：``0001_init.sql`` → (1, path)，按序号升序。

    序号必须从 1 开始连续；重号或跳号直接拒绝启动——迁移序列一旦有洞，
    ``user_version`` 就无法表达"应用到哪一步"。
    """
    migrations: list[tuple[int, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        prefix = path.stem.split("_", 1)[0]
        if not prefix.isdigit():
            raise RuntimeError(f"迁移文件命名不合法（应为 0001_xxx.sql）：{path.name}")
        migrations.append((int(prefix), path))

    versions = [version for version, _ in migrations]
    if len(versions) != len(set(versions)):
        raise RuntimeError("迁移文件序号重复，拒绝启动")
    return migrations


def migrate(db_path: str | Path | None = None) -> int:
    """按 ``PRAGMA user_version`` 逐个应用未执行的迁移，返回最终版本号。

    每个迁移文件是一个事务：DDL 与 ``user_version`` 同时提交。中途失败则整体
    回滚，不会留下"表建了一半、版本却已前进"的库。DDL 本身全部 ``IF NOT EXISTS``，
    因此即使上次在提交前崩溃，重跑也安全。

    只应在进程启动、worker 启动之前调用（此时没有并发写）。
    """
    path = Path(db_path) if db_path is not None else default_db_path()
    files = _migration_files()

    conn = connect(path)
    try:
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        for version, file in files:
            if version <= current:
                continue
            if version != current + 1:
                raise RuntimeError(
                    f"迁移序号不连续：当前 user_version={current}，下一个是 {version}"
                )

            statements = list(split_statements(file.read_text(encoding="utf-8")))
            log.info("applying migration %s (%d statements)", file.name, len(statements))
            started = time.perf_counter()

            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                # 版本号与 DDL 同事务：rollback 时 user_version 也会回退。
                # 版本号来自已校验为数字的文件名，不存在注入面。
                conn.execute(f"PRAGMA user_version = {int(version)}")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

            elapsed_ms = (time.perf_counter() - started) * 1000
            log.info("migration %s applied in %.1fms", file.name, elapsed_ms)
            current = version

        return current
    finally:
        conn.close()


# ---------------------------------------------------------------- 写队列


@dataclass(slots=True)
class _Job:
    """一个待执行的写任务：在写线程的事务里调用 ``fn(conn)``。"""

    fn: Callable[[sqlite3.Connection], Any]
    future: Future


class WriteQueue:
    """所有写操作的唯一入口。

    工作方式：
    1. 调用方 ``submit(fn)`` 把函数放进有界队列，并阻塞等待 Future；
    2. 写线程取到首个任务后，最多再等 ``batch_window_s`` 收集同批任务；
    3. 整批包在 ``BEGIN IMMEDIATE`` 里，每个任务再套一个 SAVEPOINT——
       单条失败只回滚它自己，不影响同批其他写；
    4. 整批 COMMIT 成功后，才把结果逐个交还给调用方；提交失败则全批报错。

    为什么用 SAVEPOINT 而不是"一条一事务"：100 条 fsync 会把导入吞吐打回石器时代；
    为什么不整批一错全回滚：任务之间通常无业务关联，一条坏数据不该拖垮整批。
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        maxsize: int = _QUEUE_MAXSIZE,
        batch_window_s: float = _BATCH_WINDOW_S,
        max_batch: int = _MAX_BATCH,
    ) -> None:
        self.db_path = Path(db_path)
        self.batch_window_s = batch_window_s
        self.max_batch = max_batch

        self._queue: queue.Queue[_Job | object] = queue.Queue(maxsize=maxsize)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._stopping = False

        # 轻量计数，仅用于停机日志与排障；需要指标时由 core.metrics 采集。
        self._submitted = 0
        self._failed = 0

    # ---- 生命周期

    def start(self) -> None:
        """启动写线程（幂等）。启动失败抛 RuntimeError，不留下半活线程。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._stopping:
                raise RuntimeError("WriteQueue 已停止，不能重启")

            self._ready.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._run,
                name="sqlite-writer",
                daemon=True,  # 进程退出时不阻塞；正常路径仍应调用 stop()
            )
            self._thread.start()

        # 等连接就绪，避免首个写请求把连接错误误判成业务错误。
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("SQLite 写线程启动超时")
        if self._start_error is not None:
            raise RuntimeError("SQLite 写线程启动失败") from self._start_error

    def stop(self, timeout: float = 10.0) -> None:
        """优雅停机：处理完哨兵之前的任务后退出。

        哨兵走 FIFO，排在它前面的写任务都会被处理；停机期间拒绝新任务。
        """
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            if thread is threading.current_thread():
                return  # 防止在写线程内部调用 stop 造成自我 join
            self._stopping = True

        try:
            self._queue.put(_STOP, timeout=timeout)
        except queue.Full:
            # 队列满时哨兵进不去，只能等超时后由 _fail_pending 兜底。
            log.error("停止信号无法入队：SQLite 写队列已满")

        thread.join(timeout)
        if thread.is_alive():
            log.error("SQLite 写线程在 %.1fs 内未退出，仍有在途写入", timeout)
            return

        with self._lock:
            if self._thread is thread:
                self._thread = None
        log.info(
            "SQLite 写线程已停止（submitted=%d failed=%d）",
            self._submitted,
            self._failed,
        )

    # ---- 对外 API

    def submit(self, fn: Callable[[sqlite3.Connection], T], *, timeout: float | None = None) -> T:
        """在写线程事务中执行 ``fn(conn)`` 并返回结果。

        多条语句需要原子性时，把逻辑封装进一个 ``fn``，不要连续调两次
        ``execute`` 再假设它们同事务——那是两个批次。
        """
        self.start()
        if self._stopping:
            raise RuntimeError("WriteQueue 正在停止，拒绝新写入")

        future: Future = Future()
        try:
            self._queue.put_nowait(_Job(fn, future))
        except queue.Full as exc:
            raise RuntimeError("SQLite 写队列已满（2000），请检查慢事务") from exc

        self._submitted += 1
        return cast(T, future.result(timeout=timeout))

    def execute(
        self,
        sql: str,
        params: Sequence[Any] = (),
        *,
        timeout: float | None = None,
    ) -> int:
        """执行单条写 SQL，返回受影响行数。"""

        def _do(conn: sqlite3.Connection) -> int:
            return conn.execute(sql, params).rowcount

        return self.submit(_do, timeout=timeout)

    def executemany(
        self,
        sql: str,
        rows: Sequence[Sequence[Any]],
        *,
        timeout: float | None = None,
    ) -> int:
        """批量写，返回受影响行数（一条 SQL + N 组参数，单事务）。"""

        def _do(conn: sqlite3.Connection) -> int:
            return conn.executemany(sql, rows).rowcount

        return self.submit(_do, timeout=timeout)

    def insert(
        self,
        sql: str,
        params: Sequence[Any] = (),
        *,
        timeout: float | None = None,
    ) -> int:
        """INSERT 并返回 ``lastrowid``（chunk 表用 INTEGER 主键，需要它）。"""

        def _do(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(sql, params)
            return int(cursor.lastrowid or 0)

        return self.submit(_do, timeout=timeout)

    # ---- 写线程内部

    def _run(self) -> None:
        conn: sqlite3.Connection | None = None
        try:
            conn = connect(self.db_path)
            self._ready.set()

            while True:
                first = self._queue.get()
                if first is _STOP:
                    break

                batch = [cast(_Job, first)]
                deadline = time.monotonic() + self.batch_window_s
                while len(batch) < self.max_batch:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        item = self._queue.get(timeout=remaining)
                    except queue.Empty:
                        break
                    if item is _STOP:
                        self._stopping = True
                        break
                    batch.append(cast(_Job, item))

                self._flush(conn, batch)
                if self._stopping:
                    break
        except BaseException as exc:  # 连接失败或写线程自身异常
            self._start_error = exc
            self._ready.set()
            log.exception("SQLite 写线程异常退出")
            self._fail_pending(exc)
        finally:
            self._ready.set()
            if conn is not None:
                conn.close()
            # 兜底：任何残留任务都必须拿到异常，不能让调用方永久阻塞。
            self._fail_pending(RuntimeError("SQLite 写线程已停止"))

    def _flush(self, conn: sqlite3.Connection, batch: list[_Job]) -> None:
        """整批提交：BEGIN IMMEDIATE → 逐任务 SAVEPOINT → COMMIT。"""
        started = time.perf_counter()
        succeeded: list[tuple[_Job, Any]] = []
        failed: list[tuple[_Job, BaseException]] = []

        try:
            # IMMEDIATE 立即拿写锁，避免"先读后写"时在 COMMIT 才发现冲突。
            conn.execute("BEGIN IMMEDIATE")
            for index, job in enumerate(batch):
                savepoint = f"job_{index}"
                conn.execute(f"SAVEPOINT {savepoint}")
                try:
                    result = job.fn(conn)
                except Exception as exc:
                    # 单条失败只回滚该任务，同批其他写继续。
                    conn.execute(f"ROLLBACK TO {savepoint}")
                    conn.execute(f"RELEASE {savepoint}")
                    failed.append((job, exc))
                else:
                    conn.execute(f"RELEASE {savepoint}")
                    succeeded.append((job, result))
            conn.commit()
        except BaseException as exc:
            # 提交级失败（磁盘满、锁超时等）：整批都没有落盘，全部报错。
            conn.rollback()
            for job in batch:
                job.future.set_exception(exc)
            self._failed += len(batch)
            log.exception("SQLite 写批次提交失败（batch=%d）", len(batch))
            return

        # 必须在 commit 之后再交还结果：提前 set_result 会让调用方
        # 在数据真正落盘前就继续执行。
        for job, result in succeeded:
            job.future.set_result(result)
        for job, exc in failed:
            job.future.set_exception(exc)
            log.warning("SQLite 写任务失败：%s", exc)

        self._failed += len(failed)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms > _SLOW_TX_MS:
            log.warning(
                "SQLite 写事务耗时 %.1fms（batch=%d），检查事务内是否混入网络调用",
                elapsed_ms,
                len(batch),
            )

    def _fail_pending(self, exc: BaseException) -> None:
        """停机/崩溃时把队列里剩下的任务全部置为异常。"""
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is _STOP:
                continue
            cast(_Job, item).future.set_exception(exc)


# ---------------------------------------------------------------- 全局单例


_writer_lock = threading.Lock()
_writer: WriteQueue | None = None
_writer_path: Path | None = None


def get_write_queue(db_path: str | Path | None = None) -> WriteQueue:
    """获取进程内唯一的 WriteQueue（D-1：单进程单写线程）。

    同一进程只允许一个库路径。测试或脚本切换库时，先 ``shutdown()``。
    """
    global _writer, _writer_path

    path = (Path(db_path) if db_path is not None else default_db_path()).resolve()
    with _writer_lock:
        if _writer is None:
            _writer = WriteQueue(path)
            _writer_path = path
        elif _writer_path != path:
            raise RuntimeError(
                f"已存在指向 {_writer_path} 的 WriteQueue，"
                f"不能同时使用 {path}；请先调用 shutdown()"
            )
        return _writer


def shutdown(timeout: float = 10.0) -> None:
    """停止全局写线程（应用 lifespan 退出时调用）。"""
    global _writer, _writer_path

    with _writer_lock:
        writer = _writer
        _writer = None
        _writer_path = None

    if writer is not None:
        writer.stop(timeout)


# ---------------------------------------------------------------- 读连接与启动


@contextmanager
def read_connection(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """每次读取打开独立连接，用完即关。

    WAL 下读不阻塞写；不要跨请求复用连接（会持有快照，导致 WAL 无法 checkpoint）。
    """
    conn = connect(Path(db_path) if db_path is not None else default_db_path())
    try:
        yield conn
    finally:
        conn.close()


def query_all(
    sql: str,
    params: Sequence[Any] = (),
    *,
    db_path: str | Path | None = None,
) -> list[sqlite3.Row]:
    """只读查询：返回全部行。"""
    with read_connection(db_path) as conn:
        return conn.execute(sql, params).fetchall()


def query_one(
    sql: str,
    params: Sequence[Any] = (),
    *,
    db_path: str | Path | None = None,
) -> sqlite3.Row | None:
    """只读查询：返回首行或 None。"""
    with read_connection(db_path) as conn:
        return conn.execute(sql, params).fetchone()


def init(db_path: str | Path | None = None) -> None:
    """应用启动入口：先迁移，再启动写线程。

    顺序不可颠倒：写线程一旦开始服务，迁移的 DDL 就会与业务写抢锁。
    由 ``app/main.py`` 的 lifespan 在最前面调用一次。
    """
    migrate(db_path)
    get_write_queue(db_path).start()
