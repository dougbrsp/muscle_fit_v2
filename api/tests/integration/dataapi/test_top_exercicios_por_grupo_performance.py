"""M19 - ranking de exercicios por grupo muscular em UMA passada
(`VolumeService.get_top_exercises_by_muscle_group`) no dataset de 3 anos
(400 sessoes / 4.000 exercicios / 16.000 series, semeado por
`test_dashboard_drilldown_performance.big_dataset`), nos DOIS backends.

Historico: o arquivo nasceu como medicao de `GET /analytics/dashboard-alerts`
(M19), cujo alerta de estagnacao chamava
`VolumeService.get_top_exercises_for_muscle_group` uma vez POR GRUPO
MUSCULAR - cada chamada reemitia as MESMAS duas consultas e refazia a
resolucao de contribuicao das ~16.000 series. A correcao
(`get_top_exercises_by_muscle_group`) faz 1 passada e 2 statements para todos
os grupos. O endpoint e o `DashboardAlertService` sairam no M76 Lote 2c (sem
consumidor); o ranking continua vivo no drill-down muscular
(`get_top_exercises_for_muscle_group` e uma fatia dele), entao a prova de
EQUIVALENCIA e de cache fica aqui.

A versao ANTIGA esta copiada literalmente em
`tests/support/legacy_top_exercises.py` e serve de oraculo: o ranking de CADA
grupo da janela tem de ser identico campo a campo, com o relogio congelado.

Roda so sob demanda (marcador `slow`, desmarcado em `pytest.ini`):

    cd api && python -m pytest -s -m slow \
        tests/integration/dataapi/test_top_exercicios_por_grupo_performance.py
"""

from __future__ import annotations

import pytest

from app.intelligence.volume_service import VolumeService
from tests.support.legacy_top_exercises import top_exercises_for_muscle_group_legacy

from .test_dashboard_drilldown_performance import (  # noqa: F401 - fixtures
    _diferencas,
    big_dataset,
    frozen_clock,
)

pytestmark = pytest.mark.slow

DAYS = 30
# Top-N por grupo (era o `STAGNATION_TOP_EXERCISES_PER_GROUP` do alerta).
TOP_POR_GRUPO = 3


def _grupos_da_janela(user_id: int) -> list[int]:
    rows = VolumeService().get_muscle_group_overview(days=DAYS, limit=100, user_id=user_id)
    return [int(row["muscle_group_id"]) for row in rows if row.get("muscle_group_id") is not None]


# ---------------------------------------------------------------------------
# 1) Equivalencia: o payload nao pode mudar
# ---------------------------------------------------------------------------


def test_payload_nao_muda_ranking_por_grupo(db_backend, big_dataset, frozen_clock):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Para CADA grupo da janela: `get_top_exercises_by_muscle_group` (uma
    passada, todos os grupos) devolve EXATAMENTE o ranking que a versao
    legada (uma passada por grupo) devolvia - inclusive a ordem de desempate
    entre exercicios com o mesmo `stimulus_equivalent_sets`."""
    user_id = big_dataset["user_id"]
    grupos = _grupos_da_janela(user_id)
    assert grupos, "cenario vazio nao provaria nada"

    novo = VolumeService().get_top_exercises_by_muscle_group(
        days=DAYS, user_id=user_id, limit=TOP_POR_GRUPO
    )
    legado_service = VolumeService()
    problemas: list[str] = []
    for group_id in grupos:
        legado = top_exercises_for_muscle_group_legacy(
            legado_service, group_id, DAYS, user_id=user_id, limit=TOP_POR_GRUPO
        )
        assert legado, f"grupo {group_id} sem exercicio nao provaria nada"
        problemas.extend(_diferencas(legado, novo.get(group_id, []), f"grupo[{group_id}]", 0.0))
    assert not problemas, f"{len(problemas)} divergencias:\n" + "\n".join(problemas[:40])


def test_limit_menor_e_prefixo_do_limit_maior(db_backend, big_dataset, frozen_clock):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """O cache guarda o ranking COMPLETO por grupo e fatia por `limit` - o
    que so e equivalente porque `limit` sempre foi truncamento do mesmo
    ranking ordenado (mesma propriedade ja provada para o overview)."""
    user_id = big_dataset["user_id"]
    service = VolumeService()
    completo = service.get_top_exercises_by_muscle_group(days=DAYS, user_id=user_id, limit=100)
    curto = service.get_top_exercises_by_muscle_group(days=DAYS, user_id=user_id, limit=2)
    assert completo, "cenario vazio nao provaria nada"
    for group_id, items in curto.items():
        assert items == completo[group_id][:2]


def test_cache_nao_e_compartilhado_entre_instancias(db_backend, big_dataset, frozen_clock):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """O cache e de INSTANCIA (vive o tempo de uma requisicao): uma instancia
    nova recalcula e chega no mesmo resultado."""
    user_id = big_dataset["user_id"]
    primeira = VolumeService().get_top_exercises_by_muscle_group(days=DAYS, user_id=user_id, limit=3)
    segunda = VolumeService().get_top_exercises_by_muscle_group(days=DAYS, user_id=user_id, limit=3)
    assert primeira == segunda


def test_alterar_o_retorno_nao_contamina_o_cache(db_backend, big_dataset, frozen_clock):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Copia profunda na saida: o chamador pode mexer no que recebe."""
    user_id = big_dataset["user_id"]
    service = VolumeService()
    primeira = service.get_top_exercises_by_muscle_group(days=DAYS, user_id=user_id, limit=3)
    group_id = next(iter(primeira))
    primeira[group_id][0]["exercise_name"] = "LIXO"
    segunda = service.get_top_exercises_by_muscle_group(days=DAYS, user_id=user_id, limit=3)
    assert segunda[group_id][0]["exercise_name"] != "LIXO"
