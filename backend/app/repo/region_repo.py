"""region 表：县市维度。"""

from __future__ import annotations

import sqlite3

from app.core.ids import new_id
from app.repo import db


def get(region_id: str) -> sqlite3.Row | None:
    return db.query_one("SELECT * FROM region WHERE id = ?", (region_id,))


def get_by_name(name: str) -> sqlite3.Row | None:
    return db.query_one("SELECT * FROM region WHERE name = ?", (name,))


def list_all() -> list[sqlite3.Row]:
    return db.query_all("SELECT * FROM region WHERE status = 'active' ORDER BY sort_order, name")


def create(
    *,
    name: str,
    short_name: str | None = None,
    province: str | None = None,
    city: str | None = None,
    era_note: str | None = None,
) -> str:
    region_id = new_id("rg")

    def _do(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO region(id, name, short_name, province, city, era_note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (region_id, name, short_name, province, city, era_note),
        )

    db.get_write_queue().submit(_do)
    return region_id


def get_or_create(
    *,
    name: str,
    short_name: str | None = None,
    province: str | None = None,
    city: str | None = None,
) -> str:
    row = get_by_name(name)
    if row is not None:
        return str(row["id"])
    return create(name=name, short_name=short_name, province=province, city=city)
