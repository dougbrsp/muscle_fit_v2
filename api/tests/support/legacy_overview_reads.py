"""Copia LITERAL das duas leituras que o M29 encurtou no `overview`.

Serve de ORACULO do teste de equivalencia (mesmo papel de
`legacy_top_exercises.py` no M19 e de `legacy_volume_queries.py` no
drill-down): a mudanca e exclusivamente de DESEMPENHO, entao o payload do
caminho novo precisa ser identico campo a campo ao do caminho antigo, nos dois
backends. Nada aqui e importado pela aplicacao.

As duas leituras:

1. `current_schema` (`app/load_conventions.py`) - era SEMPRE um
   `SELECT DATABASE() AS db`, uma ida ao banco por verificacao de
   "a tabela/coluna X existe neste schema?", inclusive quando o cache dessa
   verificacao ja tinha a resposta. Sete por requisicao no `overview`. O
   caminho novo le o nome do schema do proprio objeto de conexao (que o abriu
   com esse nome) e so cai neste SELECT quando a conexao nao sabe dizer.

2. `muscle_group_names` (`muscle_summary_service`) - a visao geral chama
   `build_muscle_summary` tres vezes (janela, semana atual, semana anterior) e
   cada chamada relia `SELECT id, name FROM muscle_groups`. O caminho novo
   resolve uma vez por requisicao e repassa em `group_names=`.

`patch_overview_com_leituras_legadas` devolve as duas ao comportamento
anterior, para o teste medir e comparar "antes" e "depois" no MESMO processo e
no MESMO dataset.
"""

from __future__ import annotations

from typing import Any

CURRENT_SCHEMA_SQL_LEGADO = "SELECT DATABASE() AS db"


def current_schema_legado(cursor: Any) -> str:
    """Copia literal de `load_conventions.current_schema` antes do M29."""
    cursor.execute(CURRENT_SCHEMA_SQL_LEGADO)
    row = cursor.fetchone()
    return str((row or {}).get("db") or "")


def patch_overview_com_leituras_legadas(monkeypatch) -> None:
    """Faz o `overview` voltar a emitir as leituras de antes do M29.

    `current_schema` e trocado em TODOS os modulos que o importaram por nome
    (`from ...load_conventions import current_schema`), porque em Python esse
    import fixa a referencia no modulo importador - trocar so no modulo de
    origem nao alcancaria nenhum dos chamadores.

    `build_muscle_summary` passa a ignorar `group_names`, que e exatamente o
    que ela fazia antes de o parametro existir: cada chamada relê
    `muscle_groups`.
    """
    from app import (
        body_measurements_schema,
        context_events_schema,
        daily_checkins_schema,
        equipment_exercises,
        gym_equipment_sync,
        load_conventions,
    )
    from app.intelligence.dashboard import alert_state_service, muscle_summary_service
    from app.intelligence.dashboard import overview_service

    for modulo in (
        load_conventions,
        body_measurements_schema,
        context_events_schema,
        daily_checkins_schema,
        equipment_exercises,
        gym_equipment_sync,
        alert_state_service,
    ):
        if hasattr(modulo, "current_schema"):
            monkeypatch.setattr(modulo, "current_schema", current_schema_legado)

    original = muscle_summary_service.build_muscle_summary

    def sem_group_names(*args, **kwargs):
        kwargs.pop("group_names", None)
        return original(*args, **kwargs)

    monkeypatch.setattr(muscle_summary_service, "build_muscle_summary", sem_group_names)
    monkeypatch.setattr(overview_service, "build_muscle_summary", sem_group_names)
    monkeypatch.setattr(overview_service, "muscle_group_names", lambda conn: None)


__all__ = [
    "CURRENT_SCHEMA_SQL_LEGADO",
    "current_schema_legado",
    "patch_overview_com_leituras_legadas",
]
