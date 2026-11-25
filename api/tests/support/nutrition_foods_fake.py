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
import re
import unicodedata
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from app import nutrition_access as access
from app import nutrition_external_foods as external
from app import nutrition_foods as foods
from app import nutrition_meals as meals
from app import nutrition_recipes as recipes
from app.domain.nutrition import catalog
from app.domain.nutrition.food_text import BRAND_TOKENS_MAX, normalize_name

AGORA = datetime(2026, 10, 17, 12, 0, 0)


def _search_variants() -> dict[str, tuple[bool, str, bool, bool, bool]]:
    return {
        foods.search_sql(fulltext=ft, visibility=vis, with_source=src, favorites_only=fav, exclude_recipes=exc): (
            ft, vis == access.VISIBLE_FOODS_SQL, src, fav, exc,
        )
        for exc in (False, True)
        for ft in (True, False)
        for vis in (access.VISIBLE_FOODS_SQL, access.PRIVATE_FOODS_SQL)
        for src in (True, False)
        for fav in (True, False)
    }


def _ai_ci(texto: Any) -> str:
    """Simula a collation utf8mb4_0900_ai_ci: sem acento, sem distinção de caixa."""
    nfkd = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower()


def _brand_variants() -> dict[str, tuple[bool, bool, bool, int, bool]]:
    return {
        foods.brand_search_sql(visibility=vis, with_source=src, favorites_only=fav, tokens=n, exclude_recipes=exc): (
            vis == access.VISIBLE_FOODS_SQL, src, fav, n, exc,
        )
        for exc in (False, True)
        for vis in (access.VISIBLE_FOODS_SQL, access.PRIVATE_FOODS_SQL)
        for src in (True, False)
        for fav in (True, False)
        for n in (0, *range(2, BRAND_TOKENS_MAX + 1))
    }


class FakeFoodsDB:
    def __init__(self) -> None:
        self.foods: dict[int, dict[str, Any]] = {}
        self.portions: dict[int, dict[str, Any]] = {}
        self.next_food_id = 1
        self.next_portion_id = 1
        self.favorites: list[dict[str, Any]] = []  # `user_favorite_foods` (N3): {user_id, food_id, ...}
        self.queries: list[tuple[str, tuple]] = []
        self.now = AGORA  # `created_at` das linhas gravadas pela importação externa
        # Receitas (125): `food_recipe_versions` e `food_recipe_ingredients`.
        self.recipe_versions: dict[int, dict[str, Any]] = {}
        self.recipe_ingredients: dict[int, dict[str, Any]] = {}
        self.next_version_id = 1
        self.next_ingredient_id = 1
        self._search = _search_variants()
        self._brand = _brand_variants()
        self._list = {
            foods.list_sql(bucket=b, exclude_recipes=exc): (b, exc) for b in (False, True) for exc in (False, True)
        }

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

    def _recipe_sql(self) -> dict[str, str]:
        """Statements das receitas (nutrition_recipes) e as buscas por ids que ela usa -> tipo."""
        if not hasattr(self, "_recipe_cache"):
            sqls = {
                recipes.CURRENT_VERSION_SQL: "current", recipes.MAX_VERSION_SQL: "max",
                recipes.MAX_VERSION_FOR_UPDATE_SQL: "max",
                f"{recipes.CURRENT_VERSION_SQL} FOR UPDATE": "current",
                f"{recipes.INGREDIENTS_OF_VERSION_SQL} FOR UPDATE": "ingredients",
                recipes.VERSIONS_LIST_SQL: "versions", recipes.INSERT_VERSION_SQL: "ins_version", recipes.INGREDIENTS_OF_VERSION_SQL: "ingredients",
                recipes.INSERT_INGREDIENT_SQL: "ins_ingredient", recipes.LIST_SQL: "list",
                recipes.LIST_PUBLIC_SQL: "list_public", recipes.LIST_FAVORITES_SQL: "list_favorites",
            }
            for n in range(1, 41):
                sqls[meals.recipe_versions_sql(n)] = "latest_ids"
            for n in range(1, 41):
                sqls[recipes.latest_versions_sql(n)] = "latest"
                sqls[meals.foods_by_ids_sql(n)] = "foods_ids"
                sqls[meals.portions_by_ids_sql(n)] = "portions_ids"
            self._recipe_cache = sqls
        return self._recipe_cache

    def conn(self) -> "FakeConn":
        return FakeConn(self)


class FakeConn:
    def __init__(self, db: FakeFoodsDB) -> None:
        self.db = db
        self._snapshot: Optional[tuple[Any, ...]] = None

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self.db)

    def __enter__(self) -> "FakeConn":
        d = self.db
        self._snapshot = tuple(copy.deepcopy(x) for x in (d.foods, d.portions, d.recipe_versions, d.recipe_ingredients))
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None and self._snapshot is not None:
            d = self.db
            d.foods, d.portions, d.recipe_versions, d.recipe_ingredients = self._snapshot
        return False


DECIMAL_2 = Decimal("0.01")
# Colunas DECIMAL(n,2) de `food_items` (080 e 104): o MySQL arredonda para 2 casas ao gravar.
_DECIMAL_2_COLUMNS = (*catalog.PER_100_FIELDS, "serving_size", "servings_per_package", "table_base_amount")


def _como_decimal_2(linha: dict[str, Any]) -> None:
    """Simula a precisao das colunas: valor numerico gravado vira DECIMAL com 2 casas (meio para cima)."""
    for coluna in _DECIMAL_2_COLUMNS:
        valor = linha.get(coluna)
        if isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool):
            linha[coluna] = Decimal(str(valor)).quantize(DECIMAL_2, rounding=ROUND_HALF_UP)


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

    def _buscar(self, variante: tuple[bool, str, bool, bool, bool], p: tuple) -> list[dict[str, Any]]:
        fulltext, visiveis, com_fonte, so_favoritos, sem_receita = variante
        resto = list(p)
        termo = resto.pop(0)
        usuario = resto.pop(0) if visiveis else None
        fonte = resto.pop(0) if com_fonte else None
        dono_favoritos = resto.pop(0) if so_favoritos else None
        ids_favoritos = {f["food_id"] for f in self.db.favorites if f["user_id"] == dono_favoritos}
        if fulltext:
            assert resto.pop(0) == termo
        lote = resto.pop(0)
        assert not resto
        prefixos = [t.rstrip("*") for t in termo.split()] if fulltext else []
        achados = []
        for linha in self.db.foods.values():
            if not linha["is_active"]:
                continue
            if so_favoritos and linha["id"] not in ids_favoritos:
                continue
            dono = linha["owner_user_id"]
            if visiveis and not (dono is None or dono == usuario):
                continue
            if not visiveis and dono is None:
                continue
            if fonte is not None and linha["source"] != fonte:
                continue
            if sem_receita and linha["source"] == "recipe":
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

    def _buscar_marca(self, variante: tuple[bool, bool, bool, int, bool], p: tuple) -> list[dict[str, Any]]:
        """`brand LIKE %termo%` (padrão com `\\` escapando curinga), ORDER BY name_normalized, id LIMIT; com termos (#208), cada um no nome OU na marca."""
        visiveis, com_fonte, so_favoritos, n_termos, sem_receita = variante
        resto = list(p)
        padrao = resto.pop(0)
        pares = [resto.pop(0) for _ in range(2 * n_termos)]
        termos = [resto.pop(0) for _ in range(n_termos)]
        assert pares == [t for t in termos for _ in (0, 1)]
        usuario = resto.pop(0) if visiveis else None
        fonte = resto.pop(0) if com_fonte else None
        dono_favoritos = resto.pop(0) if so_favoritos else None
        lote = resto.pop(0)
        assert not resto and padrao.startswith("%") and padrao.endswith("%")
        miolo = re.sub(r"\\(.)", r"\1", padrao[1:-1])
        ids_favoritos = {f["food_id"] for f in self.db.favorites if f["user_id"] == dono_favoritos}
        def casa(linha: dict[str, Any]) -> bool:
            marca = _ai_ci(linha["brand"])
            if _ai_ci(miolo) in marca:
                return True
            miolos = [t[1:-1] for t in termos]
            return bool(miolos) and all(m in linha["_normalized"] or m in marca for m in miolos) and any(
                m in marca for m in miolos
            )

        achados = [
            linha for linha in self.db.foods.values()
            if linha["is_active"] and linha["brand"] and casa(linha)
            and (not so_favoritos or linha["id"] in ids_favoritos)
            and ((linha["owner_user_id"] is None or linha["owner_user_id"] == usuario) if visiveis
                 else linha["owner_user_id"] is not None)
            and (fonte is None or linha["source"] == fonte)
            and not (sem_receita and linha["source"] == "recipe")
        ]
        return [_publico(linha) for linha in sorted(achados, key=lambda r: (r["_normalized"], r["id"]))[:lote]]

    def _listar(self, variante: tuple[bool, bool], p: tuple) -> list[dict[str, Any]]:
        """Listagem por letra: nome em ai_ci, `NOT(...)` no balde, ORDER BY name, id LIMIT/OFFSET."""
        bucket, sem_receita = variante
        usuario, *prefixos, limite, deslocamento = p
        letras = tuple(x.rstrip("%").lower() for x in prefixos)
        assert len(letras) == (26 if bucket else 1)
        achados = []
        for linha in self.db.foods.values():
            if not linha["is_active"] or not (linha["owner_user_id"] is None or linha["owner_user_id"] == usuario):
                continue
            if sem_receita and linha["source"] == "recipe":
                continue
            casa = any(_ai_ci(linha["name"]).startswith(letra) for letra in letras)
            if casa != bucket:
                achados.append(linha)
        achados.sort(key=lambda r: (_ai_ci(r["name"]), r["id"]))
        return [_publico(linha) for linha in achados[deslocamento:deslocamento + limite]]

    def _recipes(self, sql: str, p: tuple) -> None:
        db = self.db
        tipo = db._recipe_sql()[sql]
        if tipo == "foods_ids":
            self._rows = [_publico(db.foods[i]) for i in p if i in db.foods]
        elif tipo == "portions_ids":
            self._rows = [db.portions[i] for i in p if i in db.portions]
        elif tipo == "current":
            versoes = [v for v in db.recipe_versions.values() if v["food_id"] == p[0]]
            self._rows = sorted(versoes, key=lambda v: -v["version"])[:1]
        elif tipo == "versions":
            self._rows = sorted((v for v in db.recipe_versions.values() if v["food_id"] == p[0]), key=lambda v: -v["version"])
        elif tipo == "max":
            self._rows = [{"v": max((v["version"] for v in db.recipe_versions.values() if v["food_id"] == p[0]), default=0)}]
        elif tipo == "ins_version":
            food_id, version, final, raw, yld, instr, calc, eff, engine, user = p
            if any(v["food_id"] == food_id and v["version"] == version for v in db.recipe_versions.values()):
                raise AssertionError("UNIQUE (food_id, version) violada")
            vid = db.next_version_id
            db.next_version_id += 1
            db.recipe_versions[vid] = {
                "id": vid, "food_id": food_id, "version": version, "final_weight_g": Decimal(str(final)),
                "raw_weight_g": None if raw is None else Decimal(str(raw)),
                "yield_percent": None if yld is None else Decimal(str(yld)), "instructions": instr,
                "calculated_json": calc, "effective_json": eff, "engine_version": engine, "created_by": user,
                "created_at": db.now,
            }
            self.lastrowid, self.rowcount = vid, 1
        elif tipo == "ingredients":
            linhas = [r for r in db.recipe_ingredients.values() if r["version_id"] == p[0]]
            self._rows = sorted(linhas, key=lambda r: (r["position"], r["id"]))
        elif tipo == "ins_ingredient":
            iid = db.next_ingredient_id
            db.next_ingredient_id += 1
            colunas = ("version_id", "position", "food_id", "item_label", "quantity", "unit", "portion_id", "amount",
                       "basis", "catalog_snapshot_json")
            linha = dict(zip(colunas, p), id=iid)
            assert linha["version_id"] in db.recipe_versions  # FK
            for c in ("quantity", "amount"):
                linha[c] = Decimal(str(linha[c]))
            db.recipe_ingredients[iid] = linha
            self.lastrowid, self.rowcount = iid, 1
        elif tipo == "latest_ids":
            ultimas = {}
            for v in db.recipe_versions.values():
                if v["food_id"] in p and (v["food_id"] not in ultimas or v["version"] > ultimas[v["food_id"]]["version"]):
                    ultimas[v["food_id"]] = v
            self._rows = list(ultimas.values())
        elif tipo == "latest":
            ultimas: dict[int, dict[str, Any]] = {}
            for v in db.recipe_versions.values():
                if v["food_id"] in p and (v["food_id"] not in ultimas or v["version"] > ultimas[v["food_id"]]["version"]):
                    ultimas[v["food_id"]] = v
            self._rows = list(ultimas.values())
        elif tipo in ("list", "list_public", "list_favorites"):
            fonte = p[0]
            favoritas = {f["food_id"] for f in db.favorites if tipo == "list_favorites" and f["user_id"] == p[1]}

            def casa(r: dict[str, Any]) -> bool:
                if r["source"] != fonte or not r["is_active"]:
                    return False
                if tipo == "list":
                    return r["owner_user_id"] == p[1]
                if tipo == "list_public":
                    return r["owner_user_id"] is None
                return r["id"] in favoritas and r["owner_user_id"] in (None, p[2])

            achados = [r for r in db.foods.values() if casa(r)]
            self._rows = [_publico(r) for r in sorted(achados, key=lambda r: (r["_normalized"], r["id"]))]

    def execute(self, sql: str, params: Any = None) -> None:  # noqa: C901 - despacho por statement
        p = tuple(params or ())
        db = self.db
        db.queries.append((sql, p))
        self._rows = []
        self.rowcount = 0
        if sql in db._brand:
            self._rows = self._buscar_marca(db._brand[sql], p)
            self.rowcount = len(self._rows)
        elif sql in db._list:
            self._rows = self._listar(db._list[sql], p)
            self.rowcount = len(self._rows)
        elif sql in db._search:
            self._rows = self._buscar(db._search[sql], p)
            self.rowcount = len(self._rows)
        elif sql == foods.FAVORITE_IDS_SQL:
            self._rows = [{"food_id": f["food_id"]} for f in db.favorites if f["user_id"] == p[0]]
        elif sql in (foods.FOOD_BY_ID_SQL, f"{foods.FOOD_BY_ID_SQL} FOR UPDATE"):
            linha = db.foods.get(int(p[0]))
            self._rows = [_publico(linha)] if linha else []
        elif sql == foods.INSERT_FOOD_SQL:
            food_id = db.next_food_id
            db.next_food_id += 1
            colunas = ("source", "barcode", *foods._WRITE_COLUMNS, "owner_user_id", "created_by")
            linha = {coluna: None for coluna in foods.FOOD_COLUMNS}
            linha.update(dict(zip(colunas, p)), id=food_id, is_active=1, created_at=AGORA, updated_at=AGORA)
            _como_decimal_2(linha)
            linha["_normalized"] = linha["name_normalized"]
            db.foods[food_id] = linha
            self.lastrowid, self.rowcount = food_id, 1
        elif sql == foods.FOOD_BY_BARCODE_SQL:
            codigo, usuario = p
            achados = [
                _publico(r)
                for r in db.foods.values()
                if r["barcode"] == codigo and r["is_active"] and (r["owner_user_id"] is None or r["owner_user_id"] == usuario)
            ]
            self._rows = sorted(achados, key=lambda r: r["id"])[:1]
        elif sql == foods.UPDATE_FOOD_SQL:
            linha = db.foods[int(p[-1])]
            linha.update(dict(zip(foods._WRITE_COLUMNS, p[:-1])))
            _como_decimal_2(linha)
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
        elif sql in db._recipe_sql():
            self._recipes(sql, p)
        elif sql == foods.PROMOTE_PORTIONS_SQL:
            for r in db.portions.values():
                if r["food_id"] == p[0] and r["owner_user_id"] == p[1]:
                    r["owner_user_id"] = None
                    self.rowcount += 1
        else:
            raise AssertionError(f"SQL inesperado no fake do catálogo: {sql}")
