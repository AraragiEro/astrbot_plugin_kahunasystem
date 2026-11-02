# -*- coding: utf-8 -*-
"""账本运行时：持有 SQLite 连接与并发锁，供 LLM 工具和 Web 接口共用。

sqlite3 是阻塞 API，统一走 ``asyncio.to_thread``，并用一把锁把写操作串行化，
避免并发确认订单时出现「SQLITE_BUSY」和跨线程共用连接的问题。
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, Callable

from . import db as bankdb

DB_FILENAME = "bank.db"


class BankRuntime:
    """一个插件实例一个 runtime，懒加载数据库连接。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / DB_FILENAME
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = bankdb.connect(self.db_path)
            bankdb.init_schema(self._conn)
        return self._conn

    async def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """在锁内、线程池中执行一次同步 DB 操作；``fn`` 的第一个参数是连接。"""
        async with self._lock:
            return await asyncio.to_thread(fn, self.conn, *args, **kwargs)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
