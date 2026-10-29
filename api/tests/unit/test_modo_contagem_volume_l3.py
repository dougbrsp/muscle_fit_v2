"""Modo de contagem do volume por Modelo - Lote 3 (telas), sem banco.

- motor: fatia de cada item por bloco (`item_shares`, o card do builder só
  exibe), unidade e rótulos do modo exportados para o FE, flag de faixa não
  calibrada e a comparação lado a lado dos modos (`compare_counting_modes`);
- serviço: o loader marca a cópia não calibrada; `evaluate_template_all_modes`
  lê tudo numa transação e avalia o Modelo em cada modo;
- rotas: `GET /{id}/methodology-volume/counting-modes` e a cópia explícita
  `POST /muscle-volume-classes/{id}/copy-from-default`.
"""

from __future__ import annotations

import contextlib
from decimal import Decimal

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import muscle_volume_classes as mvc
from app import workout_plan_templates as wpt

from app.intelligence import methodology_volume as mv
from app.intelligence import methodology_volume_service as svc
from app.security import Identity, get_current_identity, require_admin_or_pt
from tests.support import methodology_paridade as paridade
from tests.support.methodology_paridade import grupo, seed_volume_classes

SEED = seed_volume_classes()
PONDERADO = mv.VolumeCountingMode.PRIMARY_SECONDARY
PRIMARIO = mv.VolumeCountingMode.PRIMARY_ONLY

DORSAL, BICEPS, PEITORAL, PEITORAL_SUP = 6, 5, 52, 12
GROUPS = (
    grupo(DORSAL, "Costas - largura dorsal", "large"),
    grupo(BICEPS, "Biceps", "small"),
    grupo(PEITORAL, "Peitoral", "large"),
    grupo(PEITORAL_SUP, "Peitoral superior", "large", PEITORAL),
)
PUXADA, ROSCA, INCLINADO = 109, 104, 101


def _row(group: int, role: str, weight: float) -> mv.ContributionRow:
    return mv.ContributionRow(group, role, weight)


CONTRIBUICOES = {
    PUXADA: (_row(DORSAL, "PRIMARY", 1.0), _row(BICEPS, "SECONDARY", 0.5), _row(PEITORAL, "STABILIZER", 0.25)),
    ROSCA: (_row(BICEPS, "PRIMARY", 1.0),),
    INCLINADO: (_row(PEITORAL_SUP, "PRIMARY", 1.0),),
}


def _item(mapping_id: int, sets: int, index: int | None, technique: float = 0.0) -> mv.MethodologyItemInput:
    return mv.MethodologyItemInput(
        mapping_id=mapping_id,
        canonical_exercise_id=mapping_id * 10,
        sets_count=sets,
        technique_equivalent=technique,
        item_index=index,
    )


def _semana(*items: mv.MethodologyItemInput) -> list[mv.MethodologySlotInput]:
    treino = mv.MethodologySlotInput(1, "normal", "A", tuple(items))
    return [treino] + [mv.MethodologySlotInput(seq, "descanso", "Descanso") for seq in range(2, 8)]


def _avaliar(slots, modo=PONDERADO, classes=SEED):
    return mv.evaluate_methodology_volume(slots, CONTRIBUICOES, GROUPS, "equipment_series", None, classes, modo)


def _share(resultado, index: int) -> dict[int, mv.ItemGroupShare]:
    item = next(s for s in resultado.item_shares if s.item_index == index)
    return {g.muscle_group_id: g for g in item.groups}


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------


def test_export_do_fe_traz_todos_os_modos_do_enum_com_rotulo_e_regua():
    exportado = mv.export_for_frontend()
    assert exportado["default_counting_mode"] == mv.DEFAULT_VOLUME_COUNTING_MODE.value
    assert [m["value"] for m in exportado["counting_modes"]] == [m.value for m in mv.VolumeCountingMode]
    ponderado = next(m for m in exportado["counting_modes"] if m["value"] == PONDERADO.value)
    # O limiar entra interpolado da constante, nunca escrito no FE.
    assert f"{mv.SECONDARY_SESSION_MIN_SETS:g} série" in ponderado["description"]
    assert ponderado["sets_unit_label"] == mv.COUNTED_SETS_UNIT_LABELS[PONDERADO]


def test_fatia_do_item_ponderado_puxada_2_series_vale_1_de_biceps_e_estabilizador_fora():
    resultado = _avaliar(_semana(_item(PUXADA, 2, 0), _item(ROSCA, 3, 1, technique=0.5)))
    puxada = _share(resultado, 0)
    assert set(puxada) == {DORSAL, BICEPS}  # estabilizador (peitoral) fora
    assert (puxada[DORSAL].direct_sets, puxada[DORSAL].is_primary) == (2, True)
    assert (puxada[BICEPS].direct_sets, puxada[BICEPS].weight, puxada[BICEPS].is_primary) == (1, 0.5, False)
    rosca = _share(resultado, 1)
    assert (rosca[BICEPS].direct_sets, rosca[BICEPS].technique_equivalent) == (3.5, 0.5)
    # A soma das fatias do dia é a sessão do bloco (mesma conta do motor).
    biceps = next(g for g in resultado.groups if g.muscle_group_id == BICEPS)
    assert biceps.sessions[0].direct_sets == puxada[BICEPS].direct_sets + rosca[BICEPS].direct_sets
    assert resultado.sets_unit_label == mv.COUNTED_SETS_UNIT_LABELS[PONDERADO]


def test_fatia_em_primary_only_so_o_primario_com_peso_1():
    resultado = _avaliar(_semana(_item(PUXADA, 2, 0)), modo=PRIMARIO)
    assert {g: s.direct_sets for g, s in _share(resultado, 0).items()} == {DORSAL: 2}
    assert resultado.sets_unit_label == mv.COUNTED_SETS_UNIT_LABELS[PRIMARIO]


def test_fatia_de_grupo_fatia_cai_no_bloco_pai_com_o_nome_da_fatia():
    share = _share(_avaliar(_semana(_item(INCLINADO, 4, 2)), modo=PRIMARIO), 2)
    assert (share[PEITORAL].slice_group_id, share[PEITORAL].slice_group_name) == (PEITORAL_SUP, "Peitoral superior")
    assert share[PEITORAL].muscle_group_name == "Peitoral"


def test_sem_item_index_nao_ha_fatia_e_o_resto_e_identico():
    sem = _avaliar(_semana(_item(PUXADA, 2, None)))
    com = _avaliar(_semana(_item(PUXADA, 2, 0)))
    assert sem.item_shares == ()
    assert sem.groups == com.groups


def _classes_copiadas(semanal: bool) -> dict[int, mv.VolumeClassSpec]:
    import dataclasses

    return {
        cid: dataclasses.replace(
            spec,
            ranges={b: dataclasses.replace(r, uncalibrated=True) for b, r in spec.ranges.items()},
            weekly_uncalibrated=semanal,
        )
        for cid, spec in SEED.items()
    }


def test_faixa_copiada_usada_na_avaliacao_marca_o_bloco_nao_calibrado():
    resultado = _avaliar(_semana(_item(PUXADA, 4, 0)), classes=_classes_copiadas(semanal=False))
    dorsal = next(g for g in resultado.groups if g.muscle_group_id == DORSAL)
    assert dorsal.uncalibrated_ranges is True
    # Grupo sem trabalho não é avaliado contra faixa nenhuma: sem selo.
    peitoral = next(g for g in resultado.groups if g.muscle_group_id == PEITORAL)
    assert peitoral.uncalibrated_ranges is False
    assert all(not g.uncalibrated_ranges for g in _avaliar(_semana(_item(PUXADA, 4, 0))).groups)


def test_comparacao_lado_a_lado_marca_as_transicoes_e_os_modos_nao_calibrados():
    slots = _semana(_item(PUXADA, 2, 0))
    comparacao = mv.compare_counting_modes(
        [_avaliar(slots, PONDERADO, _classes_copiadas(semanal=True)), _avaliar(slots, PRIMARIO)], PRIMARIO
    )
    assert comparacao.counting_modes == (PRIMARIO.value, PONDERADO.value)  # ordem do enum
    assert comparacao.current_counting_mode == PRIMARIO.value
    assert comparacao.uncalibrated_modes == (PONDERADO.value,)
    por_grupo = {g.muscle_group_id: g for g in comparacao.groups}
    # Peitoral (só estabilizador) não aparece: sem trabalho em nenhum modo.
    assert set(por_grupo) == {DORSAL, BICEPS}
    biceps = por_grupo[BICEPS]
    so_primario, ponderado = biceps.modes
    assert (so_primario.status, so_primario.frequency_bucket) == (mv.MethodologyVolumeStatus.NOT_DIRECTLY_TRAINED, None)
    assert (ponderado.direct_sets_weekly, ponderado.frequency_bucket) == (1.0, "1x")
    assert biceps.status_changed and biceps.bucket_changed
    assert not por_grupo[DORSAL].bucket_changed


# ---------------------------------------------------------------------------
# Serviço
# ---------------------------------------------------------------------------


class _Cur:
    def __init__(self, respostas):
        self.respostas = respostas
        self.chamadas: list[tuple[str, tuple | None]] = []
        self._ultimo: list = []

    def execute(self, sql, params=None):
        self.chamadas.append((" ".join(sql.split()), params))
        self._ultimo = next((v for k, v in self.respostas.items() if k in sql), [])

    def fetchall(self):
        return self._ultimo

    def fetchone(self):
        return self._ultimo[0] if self._ultimo else None


_FAIXA = {"class_id": 1, "frequency_bucket": "2x", "sets_min": Decimal("9.0"), "sets_max": Decimal("16.0"),
          "exercises_min": 3, "exercises_max": 4, "sets_source": "methodology", "sets_evidence": "EXG-016",
          "exercises_source": "methodology", "exercises_evidence": "EXG-016"}


def test_loader_marca_copia_nao_calibrada_so_no_modo_ponderado():
    cur = _Cur({
        "FROM muscle_volume_classes c": [{"id": 1, "name": "Grandes", "weekly_sets_min": 18, "weekly_sets_max": 32,
                                          "weekly_source": "derived", "weekly_evidence": None,
                                          "weekly_copied_from_counting_mode": PRIMARIO.value}],
        "FROM muscle_volume_class_mode_ranges": [{**_FAIXA, "copied_from_counting_mode": PRIMARIO.value}],
    })
    classes = svc.load_volume_classes(cur, PONDERADO)
    assert classes[1].weekly_uncalibrated and classes[1].range_for("2x").uncalibrated

    padrao = _Cur({
        "FROM muscle_volume_classes": [{"id": 1, "name": "Grandes", "weekly_sets_min": 18, "weekly_sets_max": 32,
                                        "weekly_source": "derived", "weekly_evidence": None}],
        "FROM muscle_volume_class_ranges": [_FAIXA],
    })
    classes = svc.load_volume_classes(padrao)
    assert not classes[1].weekly_uncalibrated and not classes[1].range_for("2x").uncalibrated


def test_build_slot_inputs_ecoa_o_indice_do_corpo_recebido_mesmo_com_cardio_antes():
    slots = [{"sequence": 1, "slot_type": "normal", "items": [
        {"item_kind": "cardio"},
        {"item_kind": "equipment", "mapping_id": PUXADA, "sets_count": 2},
    ]}]
    (slot,) = svc.build_slot_inputs(slots, "equipment_series", {PUXADA: None})
    assert [i.item_index for i in slot.items] == [1]


def test_evaluate_template_all_modes_uma_transacao_e_cada_modo_com_suas_faixas(monkeypatch):
    lidos: dict[str, object] = {}

    @contextlib.contextmanager
    def fake_transaction():
        class _Conn:
            @contextlib.contextmanager
            def cursor(self):
                yield object()

        lidos["transacoes"] = int(lidos.get("transacoes", 0)) + 1
        yield _Conn()

    def fake_contribs(cur, ids, mode=mv.DEFAULT_VOLUME_COUNTING_MODE):
        lidos["papeis"] = mv.counted_roles(mode)
        return {PUXADA: CONTRIBUICOES[PUXADA]}

    def fake_classes(cur, mode=mv.DEFAULT_VOLUME_COUNTING_MODE):
        lidos.setdefault("modos", []).append(mv.VolumeCountingMode(mode))
        return _classes_copiadas(semanal=False) if mv.VolumeCountingMode(mode) is PONDERADO else SEED

    monkeypatch.setattr(svc, "transaction", fake_transaction)
    monkeypatch.setattr(svc, "load_canonical_exercise_ids", lambda cur, ids: {i: None for i in ids})
    monkeypatch.setattr(svc, "load_volume_contributions", fake_contribs)
    monkeypatch.setattr(svc, "load_methodology_groups", lambda cur: GROUPS)
    monkeypatch.setattr(svc, "load_volume_classes", fake_classes)
    modelo = {
        "volume_mode": "equipment_series",
        "volume_counting_mode": PONDERADO.value,
        "slots": [{"sequence": 1, "slot_type": "normal", "items": [
            {"item_kind": "equipment", "mapping_id": PUXADA, "sets_count": 2}]}],
    }
    payload = svc.comparison_to_payload(svc.evaluate_template_all_modes(modelo, mv.MethodologyIntent()))
    assert lidos["transacoes"] == 1
    assert lidos["papeis"] == mv.counted_roles(PONDERADO)  # uma leitura com todos os papéis
    assert lidos["modos"] == list(mv.VolumeCountingMode)
    assert payload["current_counting_mode"] == PONDERADO.value
    assert payload["uncalibrated_modes"] == [PONDERADO.value]
    biceps = next(g for g in payload["groups"] if g["muscle_group_id"] == BICEPS)
    assert [m["counting_mode"] for m in biceps["modes"]] == [m.value for m in mv.VolumeCountingMode]
    assert biceps["status_changed"] is True


def test_aviso_no_modo_ponderado_fala_da_unidade_do_modo():
    resultado = _avaliar(_semana(_item(PUXADA, 30, 0)))
    avisos = svc.warnings_from_evaluation(resultado)
    assert avisos and all(mv.COUNTED_SETS_UNIT_LABELS[PONDERADO] in a["message"] for a in avisos
                          if a["axis"] != "direct_exercises")
    primario = svc.warnings_from_evaluation(_avaliar(_semana(_item(PUXADA, 30, 0)), modo=PRIMARIO))
    assert all("séries diretas" in a["message"] for a in primario if a["axis"] != "direct_exercises")


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


def _api_modelos() -> TestClient:
    app = FastAPI()
    app.include_router(wpt.router)
    identidade = Identity(user_id=1, role="admin", source="session")
    app.dependency_overrides[get_current_identity] = lambda: identidade
    app.dependency_overrides[require_admin_or_pt] = lambda: identidade
    return TestClient(app)


def test_rota_de_comparacao_404_e_payload_do_motor(monkeypatch):
    monkeypatch.setattr(wpt, "_get_template", lambda template_id: None)
    assert _api_modelos().get("/workout-plan-templates/9/methodology-volume/counting-modes").status_code == 404

    modelo = {"id": 9, "name": "ABC", "slots": [], "volume_mode": "equipment_series"}
    monkeypatch.setattr(wpt, "_get_template", lambda template_id: modelo)
    vazia = mv.compare_counting_modes([_avaliar([], m) for m in mv.VolumeCountingMode])
    monkeypatch.setattr(svc, "evaluate_template_all_modes", lambda template, intent: vazia)
    corpo = _api_modelos().get("/workout-plan-templates/9/methodology-volume/counting-modes").json()
    assert (corpo["template_id"], corpo["template_name"]) == (9, "ABC")
    assert corpo["counting_modes"] == [m.value for m in mv.VolumeCountingMode]


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @contextlib.contextmanager
    def cursor(self):
        yield self.cur


def test_copia_explicita_recusa_o_modo_default(monkeypatch):
    monkeypatch.setattr(mvc, "_require_admin_identity", lambda *a: None)
    for modo in (None, PRIMARIO.value):
        with pytest.raises(HTTPException) as erro:
            mvc.copy_volume_class_from_default_mode(1, counting_mode=modo, x_api_token=None, musclefit_session=None)
        assert erro.value.status_code == 422


def test_copia_explicita_so_preenche_o_que_falta_e_marca_a_copia(monkeypatch):
    cur = _Cur({"FROM muscle_volume_classes WHERE id": [{"id": 1, "name": "Novo"}]})
    monkeypatch.setattr(mvc, "_require_admin_identity", lambda *a: None)
    monkeypatch.setattr(mvc, "_connect", lambda: _Conn(cur))
    mvc.copy_volume_class_from_default_mode(1, counting_mode=PONDERADO.value, x_api_token=None, musclefit_session=None)
    inserts = [(sql, p) for sql, p in cur.chamadas if sql.startswith("INSERT")]
    assert len(inserts) == 2
    (faixas_sql, faixas_p), (meta_sql, meta_p) = inserts
    assert "INSERT INTO muscle_volume_class_mode_ranges" in faixas_sql and "NOT EXISTS" in faixas_sql
    assert faixas_p == (PONDERADO.value, PRIMARIO.value, 1, PONDERADO.value)
    assert "INSERT INTO muscle_volume_class_weekly_targets" in meta_sql and "NOT EXISTS" in meta_sql
    assert meta_p == (PONDERADO.value, mvc.ADMIN_SOURCE, PRIMARIO.value, 1, PONDERADO.value)
    # nunca apaga nem sobrescreve o que o modo já tem
    assert not any(sql.startswith(("DELETE", "UPDATE")) or "ON DUPLICATE" in sql for sql, _p in cur.chamadas)


def test_paridade_de_grupos_do_suporte_continua_valida():
    # Sanidade: os ids de fatia usados aqui batem com os do suporte de paridade.
    assert (paridade.PEITORAL, paridade.PEITORAL_SUP) == (PEITORAL, PEITORAL_SUP)
