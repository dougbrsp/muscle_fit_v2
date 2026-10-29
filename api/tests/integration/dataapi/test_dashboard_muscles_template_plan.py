"""`GET /analytics/dashboard/muscles` com Modelo POR EQUIPAMENTO atribuido
(`volume_mode = 'equipment_series'`, `target_source = template_plan`), nos
DOIS backends (PyMySQL de dev e shim da RDS Data API de producao).

Defeito observado em producao (2026-09-24): usuario com Modelo por
equipamento via todos os grupos com meta generica (6/9/27) e qualidade LOW.
Esse Modelo nao tem Meta de Ativacao porque a propria especificacao dele e a
meta. O que este arquivo prova contra MySQL real:

- a meta de cada grupo e a ativacao planejada do Modelo, na MESMA unidade do
  realizado (series efetivas por semana), com `min`/`max` nulos;
- grupo que o Modelo nao trabalha entra com alvo 0 e limitacao declarada;
- o payload e IDENTICO nos dois backends;
- Modelo `percent_budget` continua EXATAMENTE na cadeia antiga;
- **IDOR**: o Modelo atribuido ao dono nao vaza meta, id nem nome para o
  outro usuario.

Fixtures, seed e helpers sao os de `test_dashboard_muscles.py` - um unico
seed, uma unica definicao de "dois usuarios, quatro grupos, tres exercicios".
"""

from __future__ import annotations

import datetime as _dt

import pytest

from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.isolamento_v3 import congelar_relogio
from tests.support.template_versions import versao_v1

from .test_dashboard_muscles import (  # noqa: F401 - fixtures
    HOJE,
    REMADA,
    SUPINO,
    TRABALHO,
    _drilldown,
    _item,
    _muscles,
    _sessao,
    seed,
)


def _modelo(raw, *, criado_por, atribuido_a, slots, volume_mode="equipment_series"):
    """Modelo + atribuicao. `slots` = lista de
    `(nome, slot_type, [(muscle_group_id, mapping_id, sets_count), ...])`.
    Itens gravados como `item_kind = 'equipment'` (modo por equipamento)."""
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_templates (name, volume_mode, created_by_user_id) VALUES (%s, %s, %s)",
            (f"Modelo {volume_mode}", volume_mode, criado_por),
        )
        template_id = cur.lastrowid
        for sequence, (nome, slot_type, itens) in enumerate(slots, start=1):
            cur.execute(
                "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
                "VALUES (%s, %s, %s, %s, %s)",
                (versao_v1(cur, template_id), template_id, sequence, nome, slot_type),
            )
            slot_id = cur.lastrowid
            for ordem, (grupo_id, mapping_id, series) in enumerate(itens, start=1):
                cur.execute(
                    "INSERT INTO workout_plan_template_slot_items "
                    "(slot_id, item_order, item_kind, muscle_group_id, mapping_id, sets_count) "
                    "VALUES (%s, %s, 'equipment', %s, %s, %s)",
                    (slot_id, ordem, grupo_id, mapping_id, series),
                )
        cur.execute(
            "INSERT INTO workout_plan_template_assignments "
            "(template_id, user_id, assigned_by_user_id, assigned_at) VALUES (%s, %s, %s, %s)",
            (template_id, atribuido_a, criado_por, _dt.datetime.combine(HOJE, _dt.time(8, 0))),
        )
    return template_id


def _modelo_so_supino(raw, seed):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Slot A: supino 4 series; slot B: descanso. Ciclo = 2 slots = 2 dias (um
    slot por dia, descanso incluso - convencao do motor de geracao).
    Ativacao do ciclo: peitoral 4x1,0 = 4; triceps 4x0,5 = 2; ombro 4x0,25 = 1.
    Meta semanal (x 7 / 2): peitoral 14,0; triceps 7,0; ombro 3,5. Costas
    fica FORA do Modelo."""
    return _modelo(
        raw,
        criado_por=seed["dono"],
        atribuido_a=seed["dono"],
        slots=[
            ("A", "normal", [(seed["Peitoral"], SUPINO, 4)]),
            ("B", "descanso", []),
        ],
    )


def test_modelo_por_equipamento_da_a_meta_do_proprio_plano(seed, raw, db_backend):  # noqa: F811 - fixture do pytest importada (falso positivo)
    template_id = _modelo_so_supino(raw, seed)
    # 2 series de supino em 28 dias = 0,5/sem de peitoral (planejado 14,0);
    # 2 series de remada = 0,5/sem de costas, grupo que o Modelo NAO trabalha.
    _sessao(raw, user_id=seed["dono"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)
    _sessao(raw, user_id=seed["dono"], dias_atras=1, mapping_id=REMADA, series=TRABALHO)

    corpo = _muscles(seed["dono"], window="28d").json()

    assert corpo["target_source"] == "template_plan", db_backend
    assert corpo["training_type_id"] is None
    assert corpo["template_id"] == template_id
    assert corpo["template_name"] == "Modelo equipment_series"
    # Triceps e ombro so tem trabalho indireto/estabilizador (MEDIUM, regra
    # ja existente); o que NAO pode mais acontecer e LOW por meta generica.
    assert corpo["data_quality"] in ("HIGH", "MEDIUM")
    assert not any("meta cadastrada" in limitacao for item in corpo["items"] for limitacao in item["limitations"])
    assert any("ciclo de 2 dias" in limitacao for limitacao in corpo["limitations"])

    peitoral = _item(corpo, seed["Peitoral"])
    assert peitoral["targets"]["source"] == "template_plan"
    assert peitoral["targets"]["target"]["value"] == pytest.approx(14.0)
    assert peitoral["targets"]["min"]["value"] is None
    assert peitoral["targets"]["max"]["value"] is None
    assert peitoral["targets"]["training_type_id"] is None
    assert peitoral["realized"]["weekly_effective_sets"]["value"] == pytest.approx(0.5)
    assert peitoral["status"] == "BELOW_TARGET"
    assert peitoral["status_basis"] == "realized_only"
    assert peitoral["data_quality"] == "HIGH"
    assert not any("meta cadastrada" in limitacao for limitacao in peitoral["limitations"])

    assert _item(corpo, seed["Triceps"])["targets"]["target"]["value"] == pytest.approx(7.0)
    assert _item(corpo, seed["Ombro"])["targets"]["target"]["value"] == pytest.approx(3.5)

    costas = _item(corpo, seed["Costas"])
    assert costas["targets"]["source"] == "template_plan"
    assert costas["targets"]["target"]["value"] == 0.0
    assert costas["targets"]["min"]["value"] is None
    assert costas["status"] == "ABOVE_TARGET"  # trabalho extra, nao deficit
    assert any("fora do Modelo" in limitacao for limitacao in costas["limitations"])
    assert costas["data_quality"] == "HIGH"


def test_payload_identico_nos_dois_backends(seed, raw, monkeypatch, fake_rds, mysql_schema):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """M32: o relogio fica CONGELADO durante a comparacao.

    As duas leituras acontecem em instantes diferentes, e campos derivados do
    relogio (`hours_since_last_stimulus` e companhia) andam entre elas: sem
    congelar, este teste ja reprovou um PR por 0,01 h (74,92 contra 74,91) -
    ruido do cronometro, nao divergencia entre backends. Mesmo padrao dos
    demais testes de paridade de payload da suite."""
    _modelo_so_supino(raw, seed)
    _sessao(raw, user_id=seed["dono"], dias_atras=3, mapping_id=SUPINO, series=TRABALHO)
    congelar_relogio(monkeypatch)

    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resposta = _muscles(seed["dono"], window="28d")
        assert resposta.status_code == 200, (backend, resposta.text)
        resultados[backend] = resposta.json()

    assert resultados["pymysql"] == resultados["data-api"]
    assert resultados["pymysql"]["target_source"] == "template_plan"


def test_grupo_do_modelo_sem_treino_aparece_abaixo_do_planejado(seed, raw, db_backend):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """"Nao treinei nada" e a resposta mais importante: peitoral entra mesmo
    sem serie, abaixo do planejado - nunca some da lista."""
    _modelo_so_supino(raw, seed)

    corpo = _muscles(seed["dono"], window="28d").json()
    peitoral = _item(corpo, seed["Peitoral"])
    assert peitoral["realized"]["physical_sets"]["value"] == 0
    assert peitoral["targets"]["target"]["value"] == pytest.approx(14.0)
    assert peitoral["status"] == "BELOW_TARGET", db_backend


def test_drilldown_carrega_a_meta_do_modelo(seed, raw, db_backend):  # noqa: F811 - fixture do pytest importada (falso positivo)
    template_id = _modelo_so_supino(raw, seed)
    _sessao(raw, user_id=seed["dono"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)

    corpo = _drilldown(seed["dono"], seed["Peitoral"], window="28d").json()
    assert corpo["target_source"] == "template_plan"
    assert corpo["template_id"] == template_id
    assert corpo["item"]["targets"]["target"]["value"] == pytest.approx(14.0)
    assert corpo["item"]["targets"]["max"]["value"] is None
    assert corpo["item"]["status"] == "BELOW_TARGET"

    # Grupo existente, fora do Modelo e sem serie: 200 com alvo 0.
    costas = _drilldown(seed["dono"], seed["Costas"], window="28d").json()
    assert costas["item"]["targets"]["source"] == "template_plan"
    assert costas["item"]["targets"]["target"]["value"] == 0.0
    assert costas["item"]["status"] == "ON_TARGET"
    assert any("fora do Modelo" in limitacao for limitacao in costas["item"]["limitations"])


def test_modelo_percentual_continua_na_cadeia_antiga(seed, raw, db_backend):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """`percent_budget` sem Meta de Ativacao: NADA muda - fallback generico
    (6/9/27), qualidade LOW, cinco estados."""
    _modelo(
        raw,
        criado_por=seed["dono"],
        atribuido_a=seed["dono"],
        slots=[("A", "normal", [])],
        volume_mode="percent_budget",
    )
    _sessao(raw, user_id=seed["dono"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)

    corpo = _muscles(seed["dono"], window="28d").json()
    assert corpo["target_source"] == "fallback_generic", db_backend
    assert corpo["template_id"] is None
    peitoral = _item(corpo, seed["Peitoral"])
    assert (
        peitoral["targets"]["min"]["value"],
        peitoral["targets"]["target"]["value"],
        peitoral["targets"]["max"]["value"],
    ) == (6.0, 9.0, 27.0)
    assert peitoral["status"] == "BELOW_MIN"
    assert peitoral["data_quality"] == "LOW"


def test_idor_meta_do_modelo_nao_vaza_para_outro_usuario(seed, raw, db_backend):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """O Modelo e atribuido ao dono. O outro usuario, sem Modelo, continua na
    meta generica e nao ve alvo, id nem nome do Modelo alheio - e o realizado
    dele nao aparece no dono."""
    template_id = _modelo_so_supino(raw, seed)
    _sessao(raw, user_id=seed["outro"], dias_atras=2, mapping_id=SUPINO, series=TRABALHO)

    do_outro = _muscles(seed["outro"], window="28d").json()
    assert do_outro["target_source"] == "fallback_generic", db_backend
    assert do_outro["template_id"] is None and do_outro["template_name"] is None
    assert _item(do_outro, seed["Peitoral"])["targets"]["source"] == "fallback_generic"

    do_dono = _muscles(seed["dono"], window="28d").json()
    assert do_dono["template_id"] == template_id
    assert _item(do_dono, seed["Peitoral"])["realized"]["physical_sets"]["value"] == 0
