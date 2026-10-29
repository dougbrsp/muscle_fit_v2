"""Fase 4, PR E - endpoints da metodologia de volume, SEM banco.

O que este arquivo trava, com as tres leituras do banco substituidas por
dados em memoria (`load_methodology_inputs` e a unica costura):

1. a reducao slot -> entrada do motor (Pydantic do builder e dict de
   `_get_template`), nos dois modos, cardio fora, ordem por `sequence`;
2. `POST /workout-plan-templates/methodology-volume-preview` com numeros
   conferidos a mao (fatia somada no pai, canonico de-duplicando, janela de
   7 dias dando a volta num ciclo de 3);
3. `GET /workout-plan-templates/{id}/methodology-volume`: 404, 422 para
   `intent` fora do dominio, `template_id`/`template_name` no payload;
4. o gancho `_attach_methodology_volume_warnings`: so BELOW/ABOVE com
   `severity: warning` viram aviso, e uma excecao no avaliador NAO sobe -
   o campo vem vazio e o CRUD segue.

Sem relogio, sem rede.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import workout_plan_templates as wpt
from app.intelligence import methodology_volume as mv
from app.intelligence import methodology_volume_service as svc
from app.models import WorkoutPlanTemplateSlotIn
from app.security import Identity, get_current_identity, require_admin_or_pt
from tests.support.methodology_paridade import grupo, seed_volume_classes

# ---------------------------------------------------------------------------
# Cenario em memoria (ids reais da 002/055)
# ---------------------------------------------------------------------------

PEITORAL, PEITORAL_SUP, COSTAS, TRICEPS, BICEPS, CORE = 52, 12, 6, 14, 5, 55
SUPINO, SUPINO_B, INCLINADO, CRUCIFIXO, CORDA, SEM_PRIMARY, REMADA = 100, 107, 101, 110, 106, 108, 102

# Fase 6: grupos com o macro-grupo que o seed da 057 daria ao porte deles, e
# o cadastro = seed da 057 (mesmos numeros da v1).
GROUPS = (
    grupo(BICEPS, "Bíceps", "small"),
    grupo(COSTAS, "Costas — largura dorsal", "large"),
    grupo(PEITORAL_SUP, "Peitoral superior", "large", PEITORAL),
    grupo(TRICEPS, "Tríceps", "small"),
    grupo(PEITORAL, "Peitoral", "large"),
    grupo(CORE, "Core", "unclassified"),
)
VOLUME_CLASSES = seed_volume_classes()
# 107 e o supino reto da academia B: id absorvido, canonico 100.
CANONICAL = {SUPINO: SUPINO, SUPINO_B: SUPINO, INCLINADO: INCLINADO, CRUCIFIXO: CRUCIFIXO, CORDA: CORDA, REMADA: REMADA, SEM_PRIMARY: SEM_PRIMARY}
PRIMARY = {
    SUPINO: frozenset({PEITORAL}),
    SUPINO_B: frozenset({PEITORAL}),
    INCLINADO: frozenset({PEITORAL_SUP}),
    CRUCIFIXO: frozenset({PEITORAL}),
    CORDA: frozenset({TRICEPS}),
    REMADA: frozenset({COSTAS}),
}


def _item(order: int, kind: str, **extra) -> dict:
    return {"item_order": order, "item_kind": kind, **extra}


def _eq(order: int, mapping_id: int, group: int, sets_count: int) -> dict:
    return _item(order, "equipment", mapping_id=mapping_id, muscle_group_id=group, equipment_id=1, sets_count=sets_count)


# Ciclo de 3: Push, Pull, descanso. Janela de 7 dias (v3) a partir do Push:
# Push, Pull, descanso, Push, Pull, descanso, Push -> Push 3x, Pull 2x.
SLOTS_MODO_B = [
    {"sequence": 3, "name": "Descanso", "slot_type": "descanso", "items": []},
    {
        "sequence": 1,
        "name": "Push",
        "slot_type": "normal",
        "items": [
            _eq(1, SUPINO, PEITORAL, 4),
            _eq(2, SUPINO_B, PEITORAL, 4),  # mesmo canonico do 100
            _eq(3, INCLINADO, PEITORAL_SUP, 4),  # fatia -> soma no Peitoral
            _eq(4, CRUCIFIXO, PEITORAL, 3),
            _eq(5, CORDA, TRICEPS, 3),
            _eq(6, SEM_PRIMARY, TRICEPS, 3),  # sem PRIMARY: invisivel + data_quality
            _item(7, "cardio", cardio_duration_minutes=10),
        ],
    },
    {"sequence": 2, "name": "Pull", "slot_type": "normal", "items": [_eq(1, REMADA, COSTAS, 4)]},
]


@pytest.fixture
def leituras(monkeypatch):
    """Troca a unica costura com banco por dados em memoria e registra os
    `mapping_ids` pedidos."""
    pedidos: list[set[int]] = []

    def fake(mapping_ids: set[int], counting_mode: str = "primary_only") -> svc.MethodologyInputs:
        pedidos.append(set(mapping_ids))
        return svc.MethodologyInputs(
            canonical_ids={m: CANONICAL.get(m) for m in mapping_ids},
            contributions={m: PRIMARY[m] for m in mapping_ids if m in PRIMARY},
            groups=GROUPS,
            volume_classes=VOLUME_CLASSES,
        )

    monkeypatch.setattr(svc, "load_methodology_inputs", fake)
    return pedidos


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(wpt.router)
    identidade = Identity(user_id=1, role="admin", source="session")
    app.dependency_overrides[get_current_identity] = lambda: identidade
    app.dependency_overrides[require_admin_or_pt] = lambda: identidade
    return TestClient(app)


def _grupo(corpo: dict, group_id: int) -> dict:
    return next(g for g in corpo["groups"] if g["muscle_group_id"] == group_id)


# ---------------------------------------------------------------------------
# 1. Reducao slot -> entrada do motor
# ---------------------------------------------------------------------------


def test_build_slot_inputs_modo_b_ordena_por_sequence_e_deixa_cardio_fora():
    slots = [WorkoutPlanTemplateSlotIn(**s) for s in SLOTS_MODO_B]
    entradas = svc.build_slot_inputs(slots, "equipment_series", CANONICAL)

    assert [s.sequence for s in entradas] == [1, 2, 3]
    assert [s.is_training_session for s in entradas] == [True, True, False]
    push = entradas[0]
    assert len(push.items) == 6  # 7 no payload, cardio fora
    assert push.items[1] == mv.MethodologyItemInput(
        mapping_id=SUPINO_B, canonical_exercise_id=SUPINO, sets_count=4, muscle_group_id=PEITORAL, item_index=1
    )
    assert svc.mapping_ids_of(slots) == {SUPINO, SUPINO_B, INCLINADO, CRUCIFIXO, CORDA, SEM_PRIMARY, REMADA}


def test_build_slot_inputs_aceita_dict_cru_de_get_template():
    # Linha como `_get_template` devolve: dict com as colunas do banco.
    slots = [
        {
            "sequence": 1,
            "name": "A",
            "slot_type": "normal",
            "items": [
                {"item_kind": "equipment", "mapping_id": SUPINO, "muscle_group_id": PEITORAL, "sets_count": 5},
            ],
        }
    ]
    entradas = svc.build_slot_inputs(slots, "equipment_series", {SUPINO: None})
    assert entradas[0].items[0] == mv.MethodologyItemInput(
        mapping_id=SUPINO, canonical_exercise_id=None, sets_count=5, muscle_group_id=PEITORAL, item_index=0
    )


def test_build_slot_inputs_modo_a_percentual_governa_e_zera_sets_count():
    slots = [
        {
            "sequence": 1,
            "name": "A",
            "slot_type": "normal",
            "items": [
                # so percentual: o `sets_count=3` e o default da coluna, ignorado
                {"item_kind": "muscle_group", "muscle_group_id": PEITORAL, "sets_count": 3, "priority_min_pct": 30, "priority_max_pct": 40},
                # por series: entra com o sets_count gravado
                {"item_kind": "muscle_group", "muscle_group_id": COSTAS, "sets_count": 10, "priority_min_pct": None, "priority_max_pct": None},
                # equipamento em Modo A nao e grupo: sem muscle_group_id
                {"item_kind": "equipment", "muscle_group_id": TRICEPS, "mapping_id": CORDA, "sets_count": 3},
            ],
        }
    ]
    itens = svc.build_slot_inputs(slots, "percent_budget", {})[0].items
    assert itens[0] == mv.MethodologyItemInput(sets_count=None, muscle_group_id=PEITORAL)
    assert itens[1] == mv.MethodologyItemInput(sets_count=10, muscle_group_id=COSTAS)
    assert itens[2] == mv.MethodologyItemInput(sets_count=3, muscle_group_id=None)


# ---------------------------------------------------------------------------
# 2. POST preview - numeros a mao
# ---------------------------------------------------------------------------


def test_preview_modo_b_numeros_a_mao(api, leituras):
    resposta = api.post(
        "/workout-plan-templates/methodology-volume-preview",
        json={"slots": SLOTS_MODO_B, "volume_mode": "equipment_series"},
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    json.dumps(corpo)  # serializavel de ponta a ponta

    assert leituras == [{SUPINO, SUPINO_B, INCLINADO, CRUCIFIXO, CORDA, SEM_PRIMARY, REMADA}]
    assert corpo["metric"] == "methodology_volume"
    assert corpo["volume_mode"] == "equipment_series"
    assert corpo["intent"] == {"kind": "standard", "target_group_ids": []}
    assert (corpo["cycle_days"], corpo["training_sessions"]) == (3, 2)
    assert corpo["weekly_factor"] == 1.0
    assert corpo["frequency_is_normalized"] is True
    assert (corpo["window_days"], corpo["window_start_sequence"]) == (7, 1)
    # Fatia nao e bloco proprio: 5 blocos, nao 6.
    assert [g["muscle_group_id"] for g in corpo["groups"]] == [BICEPS, COSTAS, TRICEPS, PEITORAL, CORE]

    # Peitoral = 52 + 12. Push: supino 4 + supino B 4 + inclinado 4 + crucifixo 3
    # = 15 series; exercicios DISTINTOS pelo canonico: {100, 101, 110} = 3
    # (o 107 e o mesmo 100). Push cai 3 vezes na janela -> 3x, coluna 3x_plus
    # grande (2-3 exercicios, 6-12 series): exercicios dentro, series acima.
    peitoral = _grupo(corpo, PEITORAL)
    assert peitoral["member_group_ids"] == [PEITORAL_SUP, PEITORAL]
    assert peitoral["sessions"] == [{"slot_sequence": 1, "slot_name": "Push", "direct_sets": 15, "direct_exercises": 3, "technique_equivalent": 0}]
    assert (peitoral["frequency_raw"], peitoral["weekly_frequency"], peitoral["frequency_bucket"]) == (3, 3.0, "3x_plus")
    assert (peitoral["direct_sets_per_session"], peitoral["direct_exercises_per_session"]) == (15.0, 3.0)
    assert peitoral["direct_exercises_axis"]["status"] == "WITHIN_RANGE"
    assert peitoral["direct_exercises_axis"]["severity"] is None
    assert (peitoral["direct_exercises_axis"]["range_min"], peitoral["direct_exercises_axis"]["range_max"]) == (2, 3)
    assert peitoral["direct_sets_axis"]["status"] == "ABOVE_RANGE"
    assert (peitoral["direct_sets_axis"]["range_min"], peitoral["direct_sets_axis"]["range_max"]) == (6, 12)
    assert peitoral["direct_sets_axis"]["range_source"] == "derived"
    assert peitoral["direct_sets_axis"]["range_evidence"].startswith("derived (proposta D2)")
    assert peitoral["weekly_projection"]["direct_sets_weekly"] == pytest.approx(45.0)
    assert peitoral["weekly_projection"]["direct_exercises_weekly"] == pytest.approx(9.0)
    assert peitoral["weekly_projection"]["range_sets_weekly_min"] == pytest.approx(18.0)
    assert peitoral["weekly_projection"]["range_sets_weekly_max"] == pytest.approx(36.0)
    assert peitoral["weekly_projection"]["source"] == "derived"

    # Triceps: so a corda (o 108 nao tem PRIMARY) = 1 exercicio, 3 series;
    # 3x na janela, pequeno 3x_plus = 1-2 exercicios, 3-8 series: dentro por
    # sessao, mas 9 por semana fica abaixo da meta 12-24.
    triceps = _grupo(corpo, TRICEPS)
    assert triceps["sessions"] == [{"slot_sequence": 1, "slot_name": "Push", "direct_sets": 3, "direct_exercises": 1, "technique_equivalent": 0}]
    assert triceps["direct_exercises_axis"]["status"] == "WITHIN_RANGE"
    assert triceps["direct_exercises_axis"]["severity"] is None
    assert triceps["direct_sets_axis"]["status"] == "WITHIN_RANGE"
    assert triceps["direct_sets_axis"]["range_evidence"].startswith("derived (proposta D2)")
    assert triceps["weekly_sets_axis"]["status"] == "BELOW_RANGE"
    assert triceps["weekly_sets_axis"]["value_weekly"] == pytest.approx(9.0)

    # Costas (Pull): 1 exercicio, 4 series; 2x na janela, grande 2x: abaixo.
    costas = _grupo(corpo, COSTAS)
    assert costas["sessions"][0]["slot_name"] == "Pull"
    assert costas["direct_sets_axis"] == {
        "status": "BELOW_RANGE",
        "reason": None,
        "severity": "warning",
        "value_per_session": 4.0,
        "range_min": 9,
        "range_max": 16,
        "range_source": "methodology",
        "range_evidence": "EXG-016",
        "value_weekly": None,
    }
    # Fase 6 - eixo semanal: 4 series x 2 sessoes na janela = 8, abaixo de 18-32.
    assert costas["volume_class_name"] == "Grandes"
    assert costas["weekly_sets_axis"]["status"] == "BELOW_RANGE"
    assert costas["weekly_sets_axis"]["value_weekly"] == pytest.approx(8.0)
    assert (costas["weekly_sets_axis"]["range_min"], costas["weekly_sets_axis"]["range_max"]) == (18, 32)
    assert (costas["status"], costas["status_conflict"]) == ("BELOW_RANGE", False)
    # Peitoral: 15 x 3 sessoes na janela = 45 por semana passa de 32.
    assert peitoral["weekly_sets_axis"]["status"] == "ABOVE_RANGE"
    assert peitoral["weekly_sets_axis"]["value_weekly"] == pytest.approx(45.0)
    assert peitoral["status"] == "ABOVE_RANGE"
    # campos deprecated continuam no payload por uma versao
    assert peitoral["size_class"] == "large"

    # Musculo zero e neutro; nao classificado nao e avaliado.
    assert _grupo(corpo, BICEPS)["direct_sets_axis"]["status"] == "NOT_DIRECTLY_TRAINED"
    assert _grupo(corpo, BICEPS)["weekly_projection"] is None
    assert _grupo(corpo, CORE)["direct_sets_axis"] == {
        "status": "NOT_EVALUABLE",
        "reason": "UNCLASSIFIED_GROUP",
        "severity": None,
        "value_per_session": None,
        "range_min": None,
        "range_max": None,
        "range_source": None,
        "range_evidence": None,
        "value_weekly": None,
    }

    assert corpo["data_quality"] == {
        "items_without_primary_contribution": 1,
        "items_without_canonical": 0,
        "items_without_mapping": 0,
        "items_without_sets_count": 0,
        "groups_with_unknown_parent": 0,
    }


def test_preview_intencao_troca_so_a_severidade(api, leituras):
    corpo = api.post(
        "/workout-plan-templates/methodology-volume-preview",
        json={"slots": SLOTS_MODO_B, "volume_mode": "equipment_series", "intent": "maintenance"},
    ).json()
    costas = _grupo(corpo, COSTAS)
    assert costas["direct_sets_axis"]["status"] == "BELOW_RANGE"
    assert costas["direct_sets_axis"]["severity"] == "informational"
    assert corpo["intent"] == {"kind": "maintenance", "target_group_ids": []}


def test_preview_especializacao_com_alvo_na_fatia_marca_o_bloco(api, leituras):
    corpo = api.post(
        "/workout-plan-templates/methodology-volume-preview",
        json={
            "slots": SLOTS_MODO_B,
            "volume_mode": "equipment_series",
            "intent": "specialization",
            "target_group_ids": [PEITORAL_SUP],
        },
    ).json()
    assert corpo["intent"]["target_group_ids"] == [PEITORAL_SUP]
    assert _grupo(corpo, PEITORAL)["is_intent_target"] is True
    assert _grupo(corpo, TRICEPS)["is_intent_target"] is False


def test_preview_modo_a_por_series_e_so_percentual(api, leituras):
    por_series = [
        {"sequence": 1, "name": "A", "slot_type": "normal", "items": [
            _item(1, "muscle_group", muscle_group_id=PEITORAL, sets_count=12),
        ]},
    ]
    corpo = api.post(
        "/workout-plan-templates/methodology-volume-preview", json={"slots": por_series, "volume_mode": "percent_budget"}
    ).json()
    # Modo A nao vai ao banco por exercicio: nenhum mapping_id pedido.
    assert leituras[-1] == set()
    peitoral = _grupo(corpo, PEITORAL)
    assert peitoral["direct_exercises_axis"] == {
        "status": "NOT_EVALUABLE",
        "reason": "MODE_A_NO_EXERCISES",
        "severity": None,
        "value_per_session": None,
        "range_min": None,
        "range_max": None,
        "range_source": None,
        "range_evidence": None,
        "value_weekly": None,
    }
    # 12 series numa sessao; ciclo de 1 -> 7x/semana -> 3x+ grande (derivada).
    assert peitoral["direct_sets_axis"]["value_per_session"] == 12.0
    assert peitoral["direct_sets_axis"]["range_source"] == "derived"

    so_pct = [
        {"sequence": 1, "name": "A", "slot_type": "normal", "items": [
            _item(1, "muscle_group", muscle_group_id=PEITORAL, priority_min_pct=30, priority_max_pct=40),
        ]},
    ]
    corpo = api.post(
        "/workout-plan-templates/methodology-volume-preview", json={"slots": so_pct, "volume_mode": "percent_budget"}
    ).json()
    peitoral = _grupo(corpo, PEITORAL)
    assert peitoral["direct_exercises_axis"]["reason"] == "MODE_A_PERCENT_ONLY"
    assert peitoral["direct_sets_axis"]["reason"] == "MODE_A_PERCENT_ONLY"
    assert corpo["data_quality"]["items_without_sets_count"] == 1


@pytest.mark.parametrize("intent", ["bulking", "STANDARD", ""])
def test_preview_intent_fora_do_dominio_e_422(api, leituras, intent):
    resposta = api.post(
        "/workout-plan-templates/methodology-volume-preview",
        json={"slots": SLOTS_MODO_B, "volume_mode": "equipment_series", "intent": intent},
    )
    assert resposta.status_code == 422
    assert leituras == []  # nem chegou a ler


# ---------------------------------------------------------------------------
# 3. GET do Modelo salvo
# ---------------------------------------------------------------------------


def _modelo_salvo(template_id: int = 7, volume_mode: str = "equipment_series") -> dict:
    """Dict no formato de `_get_template` (slots com dicts, nao Pydantic)."""
    slots = [
        {**s, "id": i + 1, "template_id": template_id, "items": [{"id": j + 1, **it} for j, it in enumerate(s["items"])]}
        for i, s in enumerate(SLOTS_MODO_B)
    ]
    return {"id": template_id, "name": "PPL", "volume_mode": volume_mode, "slots": slots, "assignments": []}


def test_get_modelo_salvo_traz_id_e_nome(api, leituras, monkeypatch):
    monkeypatch.setattr(wpt, "_get_template", lambda template_id: _modelo_salvo(template_id))
    resposta = api.get("/workout-plan-templates/7/methodology-volume", params={"intent": "cutting"})
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert (corpo["template_id"], corpo["template_name"]) == (7, "PPL")
    assert corpo["intent"]["kind"] == "cutting"
    # Mesmos numeros do preview: a leitura do dict cru e a do Pydantic batem.
    assert _grupo(corpo, PEITORAL)["sessions"][0]["direct_sets"] == 15
    assert _grupo(corpo, PEITORAL)["sessions"][0]["direct_exercises"] == 3


def test_get_modelo_inexistente_e_404(api, leituras, monkeypatch):
    monkeypatch.setattr(wpt, "_get_template", lambda template_id: None)
    resposta = api.get("/workout-plan-templates/999/methodology-volume")
    assert resposta.status_code == 404
    assert resposta.json()["detail"] == "Modelo de treino não encontrado."


def test_get_intent_fora_do_dominio_e_422_antes_de_ler(api, leituras, monkeypatch):
    chamadas = []
    monkeypatch.setattr(wpt, "_get_template", lambda template_id: chamadas.append(template_id))
    resposta = api.get("/workout-plan-templates/7/methodology-volume", params={"intent": "bulking"})
    assert resposta.status_code == 422
    assert chamadas == []


# ---------------------------------------------------------------------------
# 4. Gancho aditivo do POST/PUT
# ---------------------------------------------------------------------------


def test_warnings_from_evaluation_so_below_above_com_severity_warning(leituras):
    avaliacao = svc.evaluate_template(_modelo_salvo(), mv.MethodologyIntent())
    avisos = svc.warnings_from_evaluation(avaliacao)
    # Costas (3 eixos) + Triceps (so o semanal, 9 < 12) + Peitoral (series
    # por sessao 15 > 12 e semanal 45 > 32); Biceps neutro, Core nao
    # avaliado: nenhum deles vira aviso.
    assert [(a["muscle_group_id"], a["axis"], a["status"]) for a in avisos] == [
        (COSTAS, "direct_exercises", "BELOW_RANGE"),
        (COSTAS, "direct_sets", "BELOW_RANGE"),
        (COSTAS, "weekly_sets", "BELOW_RANGE"),
        (TRICEPS, "weekly_sets", "BELOW_RANGE"),
        (PEITORAL, "direct_sets", "ABOVE_RANGE"),
        (PEITORAL, "weekly_sets", "ABOVE_RANGE"),
    ]
    assert all(a["severity"] == "warning" for a in avisos)
    assert avisos[1]["volume_class_name"] == "Grandes"
    assert avisos[1]["message"] == (
        "Costas — largura dorsal (macro-grupo Grandes): 4 séries diretas por sessão em 2x/semana; "
        "faixa 9–16; abaixo da faixa."
    )
    assert avisos[2]["value_weekly"] == pytest.approx(8.0) and avisos[2]["value_per_session"] is None
    assert avisos[2]["message"] == (
        "Costas — largura dorsal (macro-grupo Grandes): 8 séries diretas por semana; "
        "meta semanal 18–32; abaixo da meta."
    )

    # Intencao que ESPERA o desvio: vira informational e sai da lista (o
    # ABOVE do Peitoral continua: manutencao nao espera excesso).
    avaliacao = svc.evaluate_template(_modelo_salvo(), mv.MethodologyIntent(kind="maintenance"))
    assert [(a["muscle_group_id"], a["axis"]) for a in svc.warnings_from_evaluation(avaliacao)] == [
        (PEITORAL, "direct_sets"),
        (PEITORAL, "weekly_sets"),
    ]


def test_gancho_anexa_campo_aditivo_sem_mexer_no_resto(leituras):
    modelo = _modelo_salvo()
    antes = dict(modelo)
    saida = wpt._attach_methodology_volume_warnings(modelo)
    assert saida is modelo
    assert {k: v for k, v in saida.items() if k != "methodology_volume_warnings"} == antes
    assert [a["muscle_group_id"] for a in saida["methodology_volume_warnings"]] == [
        COSTAS, COSTAS, COSTAS, TRICEPS, PEITORAL, PEITORAL,
    ]


def test_gancho_engole_excecao_do_avaliador_e_devolve_lista_vazia(monkeypatch, caplog):
    def explode(*_args, **_kwargs):
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr(svc, "evaluate_template", explode)
    modelo = _modelo_salvo()
    saida = wpt._attach_methodology_volume_warnings(modelo)  # nao levanta
    assert saida["methodology_volume_warnings"] == []
    assert saida["id"] == 7
    assert any("methodology_volume_warnings indisponivel" in r.getMessage() for r in caplog.records)


def test_gancho_com_modelo_vazio_e_neutro():
    assert wpt._attach_methodology_volume_warnings(None) is None
    assert wpt._attach_methodology_volume_warnings({}) == {}


def test_evaluation_to_payload_usa_direct_sets_nunca_sets(leituras):
    corpo = svc.evaluation_to_payload(svc.evaluate_template(_modelo_salvo(), mv.MethodologyIntent()))
    chaves = set(corpo["groups"][0]) | set(corpo["groups"][0]["weekly_projection"] or {}) | set(corpo)
    assert "sets" not in chaves and "volume" not in chaves
    assert corpo["intent"]["target_group_ids"] == []  # frozenset -> lista
    assert isinstance(corpo["groups"][0]["member_group_ids"], list)


# ---------------------------------------------------------------------------
# 5. Fase 6 - leitura do cadastro de macro-grupos
# ---------------------------------------------------------------------------


class _CursorFalso:
    """Cursor que responde por prefixo do SQL e registra o que foi pedido."""

    def __init__(self, respostas: dict[str, list[dict]]):
        self.respostas = respostas
        self.sqls: list[str] = []
        self._ultimo: list[dict] = []

    def execute(self, sql, params=None):
        self.sqls.append(" ".join(sql.split()))
        self._ultimo = next(v for k, v in self.respostas.items() if k in sql)

    def fetchall(self):
        return self._ultimo


def test_load_volume_classes_normaliza_decimal_e_string_e_aceita_linhas_opcionais():
    from decimal import Decimal

    cur = _CursorFalso({
        "FROM muscle_volume_classes": [
            {"id": 1, "name": "Grandes", "weekly_sets_min": Decimal("18.0"), "weekly_sets_max": Decimal("32.0"),
             "weekly_source": "derived", "weekly_evidence": "2x × faixa por sessão"},
            # Data API pode entregar DECIMAL como string; meta semanal NULL
            {"id": 4, "name": "Lombar", "weekly_sets_min": None, "weekly_sets_max": None,
             "weekly_source": "admin", "weekly_evidence": None},
        ],
        "FROM muscle_volume_class_ranges": [
            {"class_id": 1, "frequency_bucket": "1x", "sets_min": Decimal("15.0"), "sets_max": Decimal("20.0"),
             "exercises_min": 4, "exercises_max": 5, "sets_source": "methodology", "sets_evidence": "EXG-015",
             "exercises_source": "methodology", "exercises_evidence": "EXG-015"},
            {"class_id": 4, "frequency_bucket": "2x", "sets_min": "4.5", "sets_max": "8.0",
             "exercises_min": None, "exercises_max": None, "sets_source": "admin", "sets_evidence": None,
             "exercises_source": None, "exercises_evidence": None},
        ],
    })
    classes = svc.load_volume_classes(cur)
    assert len(cur.sqls) == 2  # uma leitura para as classes, uma para as faixas
    grandes, lombar = classes[1], classes[4]
    one = grandes.range_for("1x")
    assert (one.sets_min, one.sets_max) == (15, 20) and isinstance(one.sets_min, int)
    assert one.sets_source is mv.RangeSource.METHODOLOGY and one.has_exercises_range
    assert (grandes.weekly_sets_min, grandes.weekly_sets_max) == (18, 32)
    assert grandes.range_for("2x") is None  # linha vazia -> NO_RANGE_FOR_FREQUENCY no motor
    two = lombar.range_for("2x")
    assert (two.sets_min, two.sets_max) == (4.5, 8) and not two.has_exercises_range
    assert two.sets_source is mv.RangeSource.ADMIN
    assert not lombar.has_weekly_target


def test_load_methodology_groups_le_o_macro_grupo():
    cur = _CursorFalso({"FROM muscle_groups": [
        {"id": 52, "name": "Peitoral", "size_class": "large", "methodology_parent_group_id": None, "volume_class_id": 1},
        {"id": 12, "name": "Peitoral superior", "size_class": "large", "methodology_parent_group_id": 52,
         "volume_class_id": None},
    ]})
    grupos = svc.load_methodology_groups(cur)
    assert "volume_class_id" in cur.sqls[0]
    assert grupos[0] == mv.MethodologyGroupInput(52, "Peitoral", volume_class_id=1, size_class="large")
    assert grupos[1].volume_class_id is None and grupos[1].methodology_parent_group_id == 52
