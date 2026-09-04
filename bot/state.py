"""SQLite kalici durum.

Bot yeniden baslatildiginda gunluk zarar sayaci, soguma suresi ve acik
pozisyon bilgisi kaybolmamali; aksi halde limitler her restart'ta sifirlanir
ve "gunluk %4 zarar limiti" pratikte hicbir sey ifade etmez.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

from .models import Position, Trade
from .risk import RiskState

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    opened_at INTEGER NOT NULL,
    closed_at INTEGER NOT NULL,
    pnl REAL NOT NULL,
    fees REAL NOT NULL,
    r_multiple REAL NOT NULL,
    exit_reason TEXT,
    entry_reason TEXT,
    mode TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    ts INTEGER PRIMARY KEY,
    equity REAL NOT NULL,
    mode TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_closed ON trades(closed_at);
"""


class Store:
    def __init__(self, path: str, mode: str = "paper"):
        self.mode = mode
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(p), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------ kv
    def get_kv(self, key: str) -> Optional[dict]:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def set_kv(self, key: str, value: dict) -> None:
        self.conn.execute(
            "INSERT INTO kv(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        self.conn.commit()

    def delete_kv(self, key: str) -> None:
        self.conn.execute("DELETE FROM kv WHERE key=?", (key,))
        self.conn.commit()

    # -------------------------------------------------------------- risk
    def load_risk_state(self) -> RiskState:
        data = self.get_kv(f"risk_state:{self.mode}")
        return RiskState(**data) if data else RiskState()

    def save_risk_state(self, state: RiskState) -> None:
        self.set_kv(f"risk_state:{self.mode}", asdict(state))

    # --------------------------------------------------------- pozisyonlar
    def save_position(self, pos: Position) -> None:
        self.set_kv(f"pos:{self.mode}:{pos.symbol}", asdict(pos))

    def load_positions(self) -> Dict[str, Position]:
        prefix = f"pos:{self.mode}:"
        rows = self.conn.execute(
            "SELECT key, value FROM kv WHERE key LIKE ?", (prefix + "%",)
        ).fetchall()
        out: Dict[str, Position] = {}
        for row in rows:
            data = json.loads(row["value"])
            pos = Position(**data)
            out[pos.symbol] = pos
        return out

    def clear_position(self, symbol: str) -> None:
        self.delete_kv(f"pos:{self.mode}:{symbol}")

    # -------------------------------------------------------------- islem
    def record_trade(self, t: Trade) -> None:
        self.conn.execute(
            """INSERT INTO trades(symbol, side, qty, entry_price, exit_price, opened_at,
               closed_at, pnl, fees, r_multiple, exit_reason, entry_reason, mode)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (t.symbol, t.side, t.qty, t.entry_price, t.exit_price, t.opened_at,
             t.closed_at, t.pnl, t.fees, t.r_multiple, t.exit_reason, t.entry_reason, self.mode),
        )
        self.conn.commit()

    def record_equity(self, equity: float, ts_ms: Optional[int] = None) -> None:
        ts = ts_ms or int(time.time() * 1000)
        self.conn.execute(
            "INSERT INTO equity(ts, equity, mode) VALUES(?,?,?) "
            "ON CONFLICT(ts) DO UPDATE SET equity=excluded.equity",
            (ts, equity, self.mode),
        )
        self.conn.commit()

    def recent_trades(self, limit: int = 20) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM trades WHERE mode=? ORDER BY closed_at DESC LIMIT ?",
            (self.mode, limit),
        ).fetchall()

    def stats(self) -> dict:
        rows = self.conn.execute(
            "SELECT pnl, r_multiple, fees FROM trades WHERE mode=?", (self.mode,)
        ).fetchall()
        if not rows:
            return {"trades": 0}
        pnls = [r["pnl"] for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        return {
            "trades": len(rows),
            "win_rate": 100.0 * len(wins) / len(rows),
            "net_pnl": sum(pnls),
            "fees": sum(r["fees"] for r in rows),
            "profit_factor": (gross_win / gross_loss) if gross_loss else float("inf"),
            "avg_r": sum(r["r_multiple"] for r in rows) / len(rows),
            "best": max(pnls),
            "worst": min(pnls),
        }

    def close(self) -> None:
        self.conn.close()
