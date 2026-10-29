"""Executor da leitura da refeição por IA (nutrição L5b) para o snapshot do prompt
e o eval pago (`tests/support/llm_casos.py`, feature `meal_parse`).

Roda o worker REAL (`nutrition_meal_parse_worker.dispatch_meal_parse`): o prompt
e o schema de `nutrition_meal_parse_ai`, a limpeza e a decisão do motor
(`domain/nutrition/meal_parse`, `food_text`). Só o banco é trocado: o catálogo é
o seed da TACO 4ª ed. da migração 081 (os 597 alimentos que a produção tem como
públicos), e a busca imita o FULLTEXT booleano do MySQL sobre `name_normalized`
(`termo*` em OU, ordem por termos casados, lote `SEARCH_CANDIDATE_POOL`) e o
`LIKE 'prefixo%'` do termo curto. A ordem final é a nota do motor, como na
produção. O fechamento do `ai_usage_log` é capturado (modelo e tokens: o custo
do eval sai daqui), sem banco.
"""

from __future__ import annotations

import functools
import os
from typing import Any

FEATURE = "meal_parse"


@functools.lru_cache(maxsize=1)
def catalogo_taco() -> tuple[dict[str, Any], ...]:
    from app import migrate as m

    seed = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "081_food_catalog_seed_taco.py").__globals__
    colunas = seed["SEED_COLUMNS"]
    linhas = []
    for bruta in seed["SEED_ROWS"]:
        linha = dict(zip(colunas, bruta))
        linhas.append(
            {
                "id": int(linha["source_id"]),
                "name": linha["name"],
                "name_normalized": linha["name_normalized"],
                "basis": "g",
                "source": "taco",
                "brand": None,
            }
        )
    return tuple(linhas)


def nome_taco(food_id: int) -> str:
    return next(linha["name"] for linha in catalogo_taco() if linha["id"] == int(food_id))


class _Cursor:
    def __init__(self) -> None:
        self._linhas: list[dict[str, Any]] = []
        self.buscas: list[str] = []

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        from app import nutrition_foods
        from app.nutrition_access import DIARY_FOODS_SQL

        p = tuple(params or ())
        expressao, lote = p[0], int(p[-1])
        if sql == nutrition_foods.search_sql(fulltext=True, visibility=DIARY_FOODS_SQL, with_source=False):
            termos = [t.rstrip("*") for t in str(expressao).split()]
            pontuadas = []
            for linha in catalogo_taco():
                tokens = linha["name_normalized"].split()
                casados = sum(1 for termo in termos if any(tok.startswith(termo) for tok in tokens))
                if casados:
                    pontuadas.append((casados, linha))
            pontuadas.sort(key=lambda par: (-par[0], par[1]["id"]))
            self._linhas = [dict(linha) for _, linha in pontuadas[:lote]]
        elif sql == nutrition_foods.search_sql(fulltext=False, visibility=DIARY_FOODS_SQL, with_source=False):
            prefixo = str(expressao).rstrip("%")
            achadas = sorted(
                (linha for linha in catalogo_taco() if linha["name_normalized"].startswith(prefixo)),
                key=lambda linha: (linha["name_normalized"], linha["id"]),
            )
            self._linhas = [dict(linha) for linha in achadas[:lote]]
        else:  # pragma: no cover - SQL novo no worker sem o executor acompanhar
            raise AssertionError(f"SQL inesperado no worker da leitura: {sql}")
        self.buscas.append(str(expressao))

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._linhas)


class _Conexao:
    def __enter__(self) -> "_Conexao":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def cursor(self) -> _Cursor:
        return _Cursor()


def executar(entrada: dict[str, Any], monkeypatch, rag: str, modelo_padrao: str) -> dict[str, Any]:
    """Uma leitura com a entrada congelada do caso. A rede NÃO é tocada aqui:
    o snapshot mocka `urlopen`; o eval chama a OpenAI de verdade."""
    from app import nutrition_meal_parse_ai as ai_mod
    from app import nutrition_meal_parse_worker as worker

    usos: list[dict[str, Any]] = []
    monkeypatch.setattr(worker, "transaction", lambda: _Conexao())
    monkeypatch.setattr(worker, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(worker.quota, "finish_usage", lambda usage_id, **campos: usos.append(campos) or True)
    # Snapshot: modelo fixo (o corpo HTTP inclui o modelo). Eval: o da env, ou o
    # mesmo default das outras features de texto.
    if rag == "fixo" or not (os.environ.get(ai_mod.MODEL_ENV_VAR) or "").strip():
        monkeypatch.setenv(ai_mod.MODEL_ENV_VAR, modelo_padrao)
    resultado = worker.dispatch_meal_parse(
        {
            "text": entrada["text"],
            "meal_slot": entrada.get("meal_slot"),
            "local_date": entrada.get("local_date"),
            "diary_user_id": 1,
        },
        1,
        None,
    )
    return {"resultado": resultado, "uso": usos[-1] if usos else None}


__all__ = ["FEATURE", "catalogo_taco", "executar", "nome_taco"]
