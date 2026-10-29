"""Banco FALSO (SQLite em memoria) para os testes UNITARIOS dos cards
ilustrados (`app/exercise_card_service.py`, `app/exercise_card_jobs.py`).

Por que SQLite e nao um cursor de mentira por regex: a maquina de estados
vive em UPDATEs condicionais (`WHERE id = %s AND status = %s`), contagens e
indices UNIQUE. Um banco de verdade executa a SEMANTICA desses statements -
`rowcount`, `lastrowid`, violacao de UNIQUE - sem o teste reimplementar SQL.
O que e especifico do MySQL (`FOR UPDATE`, mensagem "Duplicate entry") e
traduzido aqui; o SQL dos cards evita funcoes so do MySQL de proposito
(`NOW()`, `CURDATE()`, `FIELD()`), entao roda igual. A prova nos dois backends
REAIS (PyMySQL e Data API) e da integracao
(`tests/integration/dataapi/test_exercise_cards_fluxo.py`).

Uso:

    db = CardsSqlite()
    monkeypatch.setattr(exercise_card_service, "transaction", db.transaction)
    monkeypatch.setattr(exercise_card_jobs, "transaction", db.transaction)
    db.add_exercise(42, "Supino reto")
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterator, Optional

from app.exercise_card_prompt import PROMPT_VERSION

_DT_FORMAT = "%Y-%m-%d %H:%M:%S"


def _adapt_datetime(value: datetime) -> str:
    return value.strftime(_DT_FORMAT)


def _convert_datetime(raw: bytes) -> datetime:
    return datetime.strptime(raw.decode("utf-8")[:19], _DT_FORMAT)


sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_adapter(Decimal, str)
sqlite3.register_converter("DATETIME", _convert_datetime)
sqlite3.register_converter("DECIMAL", lambda raw: Decimal(raw.decode("utf-8")))

_SCHEMA = (
    """
    CREATE TABLE exercise_catalog (
      id INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      card_generation_id INTEGER NULL,
      card_version INTEGER NULL,
      card_published_at DATETIME NULL
    )
    """,
    """
    CREATE TABLE exercise_card_generations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      exercise_id INTEGER NOT NULL,
      card_type TEXT NOT NULL DEFAULT 'exercise_execution',
      status TEXT NOT NULL,
      version INTEGER NULL,
      parent_generation_id INTEGER NULL,
      idempotency_key TEXT NOT NULL,
      job_id TEXT NULL,
      provider TEXT NOT NULL DEFAULT 'openai',
      model TEXT NULL, quality TEXT NULL, size TEXT NULL,
      prompt_version TEXT NOT NULL,
      text_level TEXT NOT NULL DEFAULT 'minimal',
      prompt_text TEXT NULL, request_instruction TEXT NULL, reject_reason TEXT NULL,
      preview_s3_key TEXT NULL, card_s3_key TEXT NULL, card_png_s3_key TEXT NULL, thumb_s3_key TEXT NULL,
      width INTEGER NULL, height INTEGER NULL, bytes INTEGER NULL,
      tokens_in INTEGER NULL, tokens_out INTEGER NULL, cost_usd DECIMAL NULL,
      error_code TEXT NULL, error_message TEXT NULL,
      created_by INTEGER NOT NULL, approved_by INTEGER NULL,
      created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
      started_at DATETIME NULL, generated_at DATETIME NULL, approved_at DATETIME NULL,
      published_at DATETIME NULL, rejected_at DATETIME NULL, failed_at DATETIME NULL,
      UNIQUE (exercise_id, version),
      UNIQUE (exercise_id, idempotency_key)
    )
    """,
)

_FOR_UPDATE_RE = re.compile(r"\s+FOR\s+UPDATE\s*$", re.IGNORECASE)


class _Cursor:
    def __init__(self, db: "CardsSqlite"):
        self._db = db
        self._cursor = db.raw.cursor()
        self.rowcount = -1
        self.lastrowid: Optional[int] = None

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *args) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> "_Cursor":
        text = _FOR_UPDATE_RE.sub("", sql.strip())
        if text != sql.strip():
            self._db.locks.append(text)
        self._db.statements.append(" ".join(text.split()))
        try:
            self._cursor.execute(text.replace("%s", "?"), tuple(params or ()))
        except sqlite3.IntegrityError as exc:
            # Mesma mensagem que `app.db.is_duplicate_key_error` reconhece.
            raise RuntimeError(f"(1062, \"Duplicate entry for key: {exc}\")") from exc
        self.rowcount = self._cursor.rowcount
        self.lastrowid = self._cursor.lastrowid
        return self

    def fetchone(self) -> Optional[dict[str, Any]]:
        row = self._cursor.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._cursor.fetchall()]


class _Conn:
    def __init__(self, db: "CardsSqlite"):
        self._db = db

    def cursor(self) -> _Cursor:
        return _Cursor(self._db)


class CardsSqlite:
    def __init__(self) -> None:
        # `check_same_thread=False`: as rotas sincronas do FastAPI rodam no
        # threadpool do TestClient (uma requisicao por vez).
        self.raw = sqlite3.connect(
            ":memory:", detect_types=sqlite3.PARSE_DECLTYPES, isolation_level=None, check_same_thread=False
        )
        self.raw.row_factory = sqlite3.Row
        for statement in _SCHEMA:
            self.raw.execute(statement)
        self.statements: list[str] = []
        self.locks: list[str] = []
        self.transactions = 0
        self._open = False

    @contextmanager
    def transaction(self) -> Iterator[_Conn]:
        """Mesmo contrato de `app.db.transaction`: commit ao sair, rollback
        com excecao. Transacao aninhada e erro (o codigo dos cards nunca
        abre uma dentro da outra)."""
        if self._open:
            raise AssertionError("transacao aninhada")
        self._open = True
        self.transactions += 1
        self.raw.execute("BEGIN")
        try:
            yield _Conn(self)
            self.raw.execute("COMMIT")
        except BaseException:
            self.raw.execute("ROLLBACK")
            raise
        finally:
            self._open = False

    # -- conveniencias -------------------------------------------------------

    def add_exercise(self, exercise_id: int, name: str = "Supino reto") -> None:
        self.raw.execute("INSERT INTO exercise_catalog (id, name) VALUES (?, ?)", (exercise_id, name))

    def generation(self, generation_id: int) -> dict[str, Any]:
        row = self.raw.execute("SELECT * FROM exercise_card_generations WHERE id = ?", (generation_id,)).fetchone()
        assert row is not None, f"geracao {generation_id} nao existe"
        return dict(row)

    def catalog(self, exercise_id: int) -> dict[str, Any]:
        return dict(self.raw.execute("SELECT * FROM exercise_catalog WHERE id = ?", (exercise_id,)).fetchone())

    def insert_generation(self, **fields: Any) -> int:
        """Linha crua (estado arbitrario) para testes de sweep/limites."""
        base = {
            "exercise_id": 42,
            "status": "PENDING",
            "idempotency_key": fields.pop("idempotency_key", None) or str(uuid.uuid4()),
            # Versao vigente do prompt: geracoes V1 (sem composicao) nao sao
            # aprovaveis - teste que quiser uma V1 passa `prompt_version`.
            "prompt_version": PROMPT_VERSION,
            "created_by": 1,
        }
        base.update(fields)
        base.setdefault("created_at", datetime(2026, 10, 3, 12, 0, 0))
        base.setdefault("updated_at", base["created_at"])
        columns = ", ".join(base)
        marks = ", ".join("?" for _ in base)
        cursor = self.raw.execute(
            f"INSERT INTO exercise_card_generations ({columns}) VALUES ({marks})", tuple(base.values())
        )
        return int(cursor.lastrowid)

    def close(self) -> None:
        self.raw.close()
