"""Coluna "Planejado" de `/dashboard/musculos`: prescricao da ficha.

Ate 2026-10-02 o planejado somava so `raw_payload.template_slot_targets`, uma
chave que a geracao `equipment_series` nunca grava - nenhuma das fichas 113 a
126 do usuario (Modelos 21 e 28, ambos por equipamento) a tem, e a coluna
ficava vazia. Agora a fonte e a prescricao salva em `workout_plan_exercises`
(`sets_count x stimulus_weight` por contribuicao ativa, com o equivalente da
tecnica avancada) e a chave antiga so vale para a ficha sem prescricao.

Sem banco: `fetch_all` falso, datas fixas.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.intelligence import volume_service as vs
from app.intelligence.volume_service import VolumeService, aggregate_planned_effective_sets

COSTAS, BICEPS, OMBROS = 2, 3, 10
DE, ATE = date(2026, 9, 5), date(2026, 10, 2)


def _linha(plan_id, group_id, planned_sets, *, weight=None, tecnica=None, aplicacoes=None, nome=None):
    return {
        "plan_id": plan_id,
        "muscle_group_id": group_id,
        "muscle_group_name": nome or f"Grupo {group_id}",
        "advanced_technique": tecnica,
        "advanced_technique_applications": aplicacoes,
        "planned_sets": planned_sets,
        "planned_weight": weight if weight is not None else 1.0,
    }


# ---------------------------------------------------------------------------
# 1) Funcao pura
# ---------------------------------------------------------------------------


def test_soma_a_prescricao_por_grupo_e_conta_fichas_distintas():
    linhas = [
        _linha(124, COSTAS, 12.0),
        _linha(124, BICEPS, 4.5),
        _linha(126, COSTAS, 9.0),
    ]
    por_grupo = {r["muscle_group_id"]: r for r in aggregate_planned_effective_sets(linhas)}
    assert por_grupo[COSTAS]["planned_effective_sets"] == 21.0
    assert por_grupo[COSTAS]["plans_count"] == 2
    assert por_grupo[BICEPS]["planned_effective_sets"] == 4.5
    assert por_grupo[BICEPS]["plans_count"] == 1


def test_tecnica_avancada_soma_o_equivalente_planejado_pelo_peso():
    """Mesma conta do Modelo (`resolve_template_plan`): `planned_sets +
    planned_equivalent(tecnica, aplicacoes) x planned_weight`."""
    catalogo = {"DROP_SET": {"activation_set_equivalent": 0.5, "max_equivalent_per_anchor": 2.0}}
    sem = aggregate_planned_effective_sets([_linha(1, OMBROS, 6.0, weight=2.0)])
    com = aggregate_planned_effective_sets(
        [_linha(1, OMBROS, 6.0, weight=2.0, tecnica="DROP_SET", aplicacoes=2)], catalog=catalogo
    )
    assert vs.planned_equivalent("DROP_SET", 2, catalogo) == 1.0
    assert sem[0]["planned_effective_sets"] == 6.0
    # 6 + 1,0 (2 aplicacoes x 0,5) x 2,0 de peso.
    assert com[0]["planned_effective_sets"] == pytest.approx(8.0)


def test_template_slot_targets_continua_valendo_como_alternativa():
    payload_antigo = {
        "template_slot_targets": [
            {"muscle_group_id": COSTAS, "muscle_group_name": "Costas", "sets_count": 10},
            {"muscle_group_id": None, "muscle_group_name": None, "sets_count": 2},
            {"muscle_group_id": BICEPS, "sets_count": None},
        ]
    }
    resultado = aggregate_planned_effective_sets([_linha(126, COSTAS, 9.0)], [payload_antigo, {}, None])
    por_grupo = {r["muscle_group_id"]: r for r in resultado}
    assert por_grupo[COSTAS]["planned_effective_sets"] == 19.0
    assert por_grupo[COSTAS]["plans_count"] == 2
    assert por_grupo[None]["muscle_group_name"] == vs.OUTROS_GROUP
    assert BICEPS not in por_grupo


def test_sem_nada_planejado_devolve_vazio():
    assert aggregate_planned_effective_sets([], []) == []
    assert aggregate_planned_effective_sets([_linha(1, COSTAS, 0.0)]) == []


# ---------------------------------------------------------------------------
# 2) VolumeService.get_planned_effective_sets: qual fonte vale por ficha
# ---------------------------------------------------------------------------


class _Fake(VolumeService):
    def __init__(self, *, prescricao, fichas, payloads):
        self.prescricao = prescricao
        self.fichas = fichas
        self.payloads = payloads
        self.sqls: list[tuple[str, tuple]] = []

    def fetch_all(self, sql, params=()):
        self.sqls.append((sql, params))
        if "FROM workout_plan_exercises" in sql or "JOIN workout_plan_exercises" in sql:
            return self.prescricao
        if "raw_payload" in sql:
            return [p for p in self.payloads if p["id"] in params]
        if "SELECT wp.id AS id FROM workout_plans" in sql:
            return self.fichas
        return []


def test_ficha_por_equipamento_sem_template_slot_targets_tem_planejado():
    """Retrato das fichas 124 e 126 do usuario: prescricao salva, nenhuma
    `template_slot_targets`. Antes: lista vazia (coluna Planejado em branco)."""
    servico = _Fake(
        prescricao=[_linha(124, COSTAS, 12.0), _linha(126, OMBROS, 8.0)],
        fichas=[{"id": 124}, {"id": 126}],
        payloads=[{"id": 124, "raw_payload": "{}"}, {"id": 126, "raw_payload": "{}"}],
    )
    resultado = {r["muscle_group_id"]: r["planned_effective_sets"] for r in servico.get_planned_effective_sets(1, DE, ATE)}
    assert resultado == {COSTAS: 12.0, OMBROS: 8.0}
    # Nenhuma ficha sem prescricao: `raw_payload` (coluna pesada) nem e lido.
    assert not any("raw_payload" in sql for sql, _ in servico.sqls)


def test_ficha_antiga_sem_prescricao_cai_para_template_slot_targets():
    servico = _Fake(
        prescricao=[_linha(126, COSTAS, 9.0)],
        fichas=[{"id": 104}, {"id": 126}],
        payloads=[
            {"id": 104, "raw_payload": '{"template_slot_targets": [{"muscle_group_id": 2, "sets_count": 10}]}'},
            {"id": 126, "raw_payload": '{"template_slot_targets": [{"muscle_group_id": 2, "sets_count": 99}]}'},
        ],
    )
    resultado = servico.get_planned_effective_sets(1, DE, ATE)
    # 126 soma pela prescricao (9), nunca pelas duas fontes; 104 pela chave antiga (10).
    assert resultado == [
        {"muscle_group_id": COSTAS, "muscle_group_name": "Grupo 2", "planned_effective_sets": 19.0, "plans_count": 2}
    ]
    leitura_payload = [params for sql, params in servico.sqls if "raw_payload" in sql]
    assert leitura_payload == [(104,)]


def test_consulta_da_prescricao_so_conta_series_de_trabalho_com_a_regra_do_realizado():
    """Aquecimento (`warmup_sets_count`) e ajuste (`feeler_set_sets_count`)
    nao entram; contribuicao ativa e id canonico como em `ROLE_TOTALS_SQL`."""
    sql = vs.PLAN_EXERCISES_ACTIVATION_SQL
    assert "wpe.sets_count * emc.stimulus_weight" in sql
    assert "warmup" not in sql and "feeler" not in sql
    assert "emc.is_active = 1" in sql
    assert "COALESCE(elm.canonical_exercise_id, wpe.mapping_id)" in sql
    assert vs._planned_plans_params(1, DE, ATE) == (DE, ATE, 1)
    assert "wp.user_id = %s" in vs._planned_plans_where("wp", 1)
    assert "user_id" not in vs._planned_plans_where("wp", None)
