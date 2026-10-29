"""Banco em memória para os testes do catálogo de alimentos (L3a).

Responde EXATAMENTE os statements de `app.nutrition_foods` e, desde o L3b, de
`app.nutrition_external_foods` (despacho pela constante SQL do módulo ou por
`search_sql`, não por regex solta): SQL novo ou alterado sem o fake acompanhar
falha alto. A importação externa simula a chave única `(source, source_id)` com
`ON DUPLICATE KEY UPDATE` sem efeito. Simula o que importa do MySQL para
estas rotas: `lastrowid`, `rowcount`, o FULLTEXT em modo booleano com prefixo
(`termo*`, OU entre termos, relevância = termos que casam) sobre
`name_normalized`, o LIKE de prefixo, o filtro de visibilidade e o rollback da
conexão quando a rota levanta.
"""

from __future__ import annotations

import copy
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from app import nutrition_access as access
from app import nutrition_external_foods as external
from app import nutrition_foods as foods
from app.domain.nutrition import catalog
from app.domain.nutrition.food_text import normalize_name

AGORA = datetime(2026, 10, 17, 12, 0, 0)


def _search_variants() -> dict[str, tuple[bool, str, bool]]:
    return {
        foods.search_sql(fulltext=ft, visibility=vis, with_source=src): (ft, vis == access.VISIBLE_FOODS_SQL, src)
        for ft in (True, False)
        for vis in (access.VISIBLE_FOODS_SQL, access.PRIVATE_FOODS_SQL)
        for src in (True, False)
    }


class FakeFoodsDB:
    def __init__(self) -> None:
        self.foods: dict[int, dict[str, Any]] = {}
        self.portions: dict[int, dict[str, Any]] = {}
        self.next_food_id = 1
        self.next_portion_id = 1
        self.queries: list[tuple[str, tuple]] = []
        self.now = AGORA  # `created_at` das linhas gravadas pela importação externa
        self._search = _search_variants()

    # --- cenário -------------------------------------------------------------
    def add_food(self, name: str, *, source: str = "taco", owner: Optional[int] = None, active: bool = True,
                 source_id: Optional[str] = None, created_by: Optional[int] = None,
                 created_at: datetime = AGORA, **valores: Any) -> int:
        food_id = self.next_food_id
        self.next_food_id += 1
        linha = {coluna: None for coluna in foods.FOOD_COLUMNS}
        linha.update(
            id=food_id, source=source, name=name, basis="g", owner_user_id=owner, is_active=1 if active else 0,
            created_at=created_at, updated_at=created_at, created_by=created_by,
            citation=catalog.citation_for(source), license=catalog.license_for(source),
            source_id=source_id if source_id is not None else (str(food_id) if source == "taco" else None),
        )
        for campo, valor in valores.items():
            linha[campo] = Decimal(str(valor)) if isinstance(valor, (int, float)) else valor
        linha["_normalized"] = normalize_name(name)
        self.foods[food_id] = linha
        return food_id

    def add_portion(self, food_id: int, label: str, amount: float, owner: Optional[int] = None) -> int:
        portion_id = self.next_portion_id
        self.next_portion_id += 1
        self.portions[portion_id] = {
            "id": portion_id, "food_id": food_id, "label": label, "amount": Decimal(str(amount)), "source": "user",
            "owner_user_id": owner, "created_at": AGORA,
        }
        return portion_id

    def conn(self) -> "FakeConn":
        return FakeConn(self)


class FakeConn:
    def __init__(self, db: FakeFoodsDB) -> None:
        self.db = db
        self._snapshot: Optional[tuple[Any, Any]] = None

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self.db)

    def __enter__(self) -> "FakeConn":
        self._snapshot = (copy.deepcopy(self.db.foods), copy.deepcopy(self.db.portions))
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None and self._snapshot is not None:
            self.db.foods, self.db.portions = self._snapshot
        return False


def _publico(linha: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in linha.items() if not k.startswith("_")}


class FakeCursor:
    def __init__(self, db: FakeFoodsDB) -> None:
        self.db = db
        self._rows: list[dict[str, Any]] = []
        self.rowcount = 0
        self.lastrowid: Optional[int] = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def fetchone(self) -> Optional[dict[str, Any]]:
        return dict(self._rows[0]) if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]

    def _buscar(self, variante: tuple[bool, str, bool], p: tuple) -> list[dict[str, Any]]:
        fulltext, visiveis, com_fonte = variante
        resto = list(p)
        termo = resto.pop(0)
        usuario = resto.pop(0) if visiveis else None
        fonte = resto.pop(0) if com_fonte else None
        if fulltext:
            assert resto.pop(0) == termo
        lote = resto.pop(0)
        assert not resto
        prefixos = [t.rstrip("*") for t in termo.split()] if fulltext else []
        achados = []
        for linha in self.db.foods.values():
            if not linha["is_active"]:
                continue
            dono = linha["owner_user_id"]
            if visiveis and not (dono is None or dono == usuario):
                continue
            if not visiveis and dono is None:
                continue
            if fonte is not None and linha["source"] != fonte:
                continue
            palavras = linha["_normalized"].split()
            if fulltext:
                relevancia = sum(1 for pre in prefixos if any(w.startswith(pre) for w in palavras))
                if relevancia:
                    achados.append((-relevancia, linha["id"], linha))
            elif linha["_normalized"].startswith(termo.rstrip("%")):
                achados.append((linha["_normalized"], linha["id"], linha))
        achados.sort(key=lambda t: (t[0], t[1]))
        return [_publico(linha) for *_chave, linha in achados[:lote]]

    def execute(self, sql: str, params: Any = None) -> None:  # noqa: C901 - despacho por statement
        p = tuple(params or ())
        db = self.db
        db.queries.append((sql, p))
        self._rows = []
        self.rowcount = 0
        if sql in db._search:
            self._rows = self._buscar(db._search[sql], p)
            self.rowcount = len(self._rows)
        elif sql == foods.FOOD_BY_ID_SQL:
            linha = db.foods.get(int(p[0]))
            self._rows = [_publico(linha)] if linha else []
        elif sql == foods.INSERT_FOOD_SQL:
            food_id = db.next_food_id
            db.next_food_id += 1
            colunas = ("source", *foods._WRITE_COLUMNS, "owner_user_id", "created_by")
            linha = {coluna: None for coluna in foods.FOOD_COLUMNS}
            linha.update(dict(zip(colunas, p)), id=food_id, is_active=1, created_at=AGORA, updated_at=AGORA)
            linha["_normalized"] = linha["name_normalized"]
            db.foods[food_id] = linha
            self.lastrowid, self.rowcount = food_id, 1
        elif sql == foods.UPDATE_FOOD_SQL:
            linha = db.foods[int(p[-1])]
            linha.update(dict(zip(foods._WRITE_COLUMNS, p[:-1])))
            linha["_normalized"] = linha["name_normalized"]
            self.rowcount = 1
        elif sql == foods.PROMOTE_FOOD_SQL:
            linha = db.foods.get(int(p[0]))
            if linha and linha["owner_user_id"] == p[1]:
                linha["owner_user_id"] = None
                self.rowcount = 1
        elif sql == foods.PORTIONS_OF_FOOD_SQL:
            food_id, *donos = p
            linhas = [r for r in db.portions.values()
                      if r["food_id"] == food_id and (r["owner_user_id"] is None or r["owner_user_id"] in donos)]
            self._rows = sorted(linhas, key=lambda r: (r["amount"], r["id"]))
        elif sql == foods.PORTION_BY_ID_SQL:
            linha = db.portions.get(int(p[0]))
            self._rows = [linha] if linha else []
        elif sql == foods.INSERT_PORTION_SQL:
            portion_id = db.next_portion_id
            db.next_portion_id += 1
            food_id, label, amount, source, owner, _criado_por = p
            db.portions[portion_id] = {"id": portion_id, "food_id": food_id, "label": label,
                                       "amount": Decimal(str(amount)), "source": source, "owner_user_id": owner,
                                       "created_at": AGORA}
            self.lastrowid, self.rowcount = portion_id, 1
        elif sql == external.FOOD_BY_SOURCE_SQL:
            fonte, source_id = p
            self._rows = [_publico(r) for r in db.foods.values() if r["source"] == fonte and r["source_id"] == source_id]
        elif sql == external.OFF_LOOKUPS_TODAY_SQL:
            fonte, usuario, inicio = p
            n = sum(1 for r in db.foods.values()
                    if r["source"] == fonte and r["created_by"] == usuario and r["created_at"] >= inicio)
            self._rows = [{"n": n}]
        elif sql == external.INSERT_EXTERNAL_FOOD_SQL:
            dados = dict(zip(external._IMPORT_COLUMNS, p))
            if any(r["source"] == dados["source"] and r["source_id"] == dados["source_id"] for r in db.foods.values()):
                self.rowcount = 0  # ON DUPLICATE KEY UPDATE id = id: sem efeito
                return
            food_id = db.next_food_id
            db.next_food_id += 1
            linha = {coluna: None for coluna in foods.FOOD_COLUMNS}
            linha.update(dados, id=food_id, owner_user_id=None, is_active=1, created_at=db.now, updated_at=db.now)
            linha["_normalized"] = linha["name_normalized"]
            db.foods[food_id] = linha
            self.lastrowid, self.rowcount = food_id, 1
        elif sql == foods.PROMOTE_PORTIONS_SQL:
            for r in db.portions.values():
                if r["food_id"] == p[0] and r["owner_user_id"] == p[1]:
                    r["owner_user_id"] = None
                    self.rowcount += 1
        else:
            raise AssertionError(f"SQL inesperado no fake do catálogo: {sql}")
