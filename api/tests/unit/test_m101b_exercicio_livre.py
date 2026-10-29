"""M101b - exercicio livre no Modelo (Modo B, `equipment_series`).

Item so com o grupo muscular (sem `mapping_id` nem `equipment_id`): a geracao
escolhe o EXERCICIO entre os pares ativos da academia cujo grupo PRIMARY e o
do item, e o equipamento vem junto. Ordem pelo ranking unico
(`domain/equipment_ranking.py`: estrelas do par DESC, recencia ASC), com a
recencia contando tambem as fichas geradas e nao executadas, para alternar
entre geracoes seguidas sem treino no meio.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app import equipment_recommender
from app.domain.laboral import sql_not_laboral
from app.domain.prescription import WARNING_EXERCISE_AUTO_CHOSEN, WARNING_EQUIPMENT_UNRESOLVED
from app.domain.training_safety import TrainingSafety
from app.generation import exercise_substitution as subst
from app.generation import mode_b
from app.generation.deterministic_prescription import PairRequest
from app.generation.freeze_mode_b import match_rows_to_model_items, substitution_audit_for_row

GYM_ID = 7
USER_ID = 1
GROUP_ID = 3


def _candidate(gym_equipment_id: int, exercise_id: int, equipment_id: int, exercise_name: str, equipment_name: str):
    return subst.SubstituteCandidate(
        gym_equipment_id=gym_equipment_id,
        exercise_id=exercise_id,
        exercise_name=exercise_name,
        equipment_id=equipment_id,
        equipment_name=equipment_name,
        muscle_group_id=GROUP_ID,
    )


# gym_equipment.id, exercise_id, equipment_id
SUPINO_BARRA = _candidate(11, 100, 1, "Supino reto", "Barra")
CRUCIFIXO_HALTER = _candidate(12, 200, 2, "Crucifixo", "Halteres")
SUPINO_MAQUINA = _candidate(13, 100, 3, "Supino reto", "Máquina")
CROSSOVER_CABO = _candidate(14, 300, 4, "Crossover", "Polia")


class FakeDb:
    """Banco de mentira das leituras do recomendador (execucoes, fichas
    planejadas, estrelas) - o SQL e casado por trecho."""

    def __init__(self) -> None:
        self.executed: list[dict[str, Any]] = []
        self.planned: list[dict[str, Any]] = []
        self.stars: list[dict[str, Any]] = []
        self.queries: list[str] = []

    def plan(self, gym_equipment_id: int, created_at: Any) -> None:
        """Ficha gerada e salva (status planned), ainda nao executada."""
        current = next((row for row in self.planned if row["gym_equipment_id"] == gym_equipment_id), None)
        if current is None:
            self.planned.append({"gym_equipment_id": gym_equipment_id, "last_planned_at": created_at})
        else:
            current["last_planned_at"] = max(str(current["last_planned_at"]), str(created_at))

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, db: FakeDb) -> None:
        self.db = db
        self._rows: list[dict[str, Any]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.db.queries.append(sql)
        if "FROM workout_plan_exercises" in sql:
            assert params[-1] == equipment_recommender.PLANNED_PLAN_STATUS
            self._rows = [dict(row) for row in self.db.planned]
        elif "FROM exercises e" in sql:
            self._rows = [dict(row) for row in self.db.executed]
        elif equipment_recommender.PREFERENCES_TABLE in sql:
            self._rows = [dict(row) for row in self.db.stars]
        else:  # pragma: no cover - leitura inesperada e erro do teste
            raise AssertionError(f"SQL inesperado: {sql}")

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


def _safety(exercise_id: int, equipment_id: int, max_safe_rpe: int = 9) -> TrainingSafety:
    return TrainingSafety(
        exercise_id=exercise_id,
        equipment_id=equipment_id,
        equipment_exercise_id=None,
        max_safe_rpe=max_safe_rpe,
        failure_allowed=max_safe_rpe == 10,
        safety_source=None,
    )


@pytest.fixture
def candidates() -> list[subst.SubstituteCandidate]:
    return [SUPINO_BARRA, CRUCIFIXO_HALTER, CROSSOVER_CABO]


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, candidates) -> FakeDb:
    fake = FakeDb()
    calls: list[dict[str, Any]] = []

    def fake_load(conn, *, gym_id, muscle_group_id, user_id):
        calls.append({"gym_id": gym_id, "muscle_group_id": muscle_group_id, "user_id": user_id})
        return list(candidates)

    monkeypatch.setattr(subst, "load_group_candidates", fake_load)
    monkeypatch.setattr(
        mode_b, "resolve_training_safety_bulk", lambda conn, pairs: {pair: _safety(*pair) for pair in pairs}
    )
    monkeypatch.setattr(mode_b, "is_pair_coherent", lambda cur, exercise_id, gym_equipment_id: True)
    fake.load_calls = calls  # type: ignore[attr-defined]
    return fake


def _free_item(**extra: Any) -> dict[str, Any]:
    return {"mapping_id": None, "equipment_id": None, "muscle_group_id": GROUP_ID, "sets_count": 3, **extra}


def _generate(db: FakeDb, item: dict[str, Any] | None = None, exclude=()) -> mode_b.ResolvedEquipment:
    return mode_b.resolve_item_equipment(
        db, item or _free_item(), GYM_ID, user_id=USER_ID, exclude_exercise_ids=exclude
    )


def test_item_livre_escolhe_o_exercicio_e_o_equipamento_vem_junto(db: FakeDb):
    db.stars = [{"exercise_id": 300, "equipment_id": 4, "stars": 5}]
    resolved = _generate(db)
    assert (resolved.mapping_id, resolved.equipment_id, resolved.preferred_equipment_id) == (300, 4, 14)
    assert resolved.equipment_name == "Polia"
    assert resolved.warnings == (WARNING_EXERCISE_AUTO_CHOSEN,)
    assert not resolved.requires_user_choice
    assert resolved.audit["kind"] == mode_b.KIND_FREE_EXERCISE
    assert resolved.audit["reason"] == mode_b.REASON_EXERCISE_FREE_IN_TEMPLATE
    assert resolved.audit["from_exercise_id"] is None and resolved.audit["to_exercise_id"] == 300
    assert db.load_calls == [{"gym_id": GYM_ID, "muscle_group_id": GROUP_ID, "user_id": USER_ID}]


def test_alterna_o_exercicio_em_tres_geracoes_seguidas_sem_treino(db: FakeDb):
    """Mesmas estrelas: cada ficha gerada (e salva, nao executada) empurra o
    par escolhido para o fim da fila; a geracao seguinte pega outro."""
    escolhidos = []
    for minuto, formato in ((0, "texto"), (5, "datetime"), (10, "texto")):
        resolved = _generate(db)
        escolhidos.append(resolved.mapping_id)
        quando = (
            f"2026-10-27 10:{minuto:02d}:00" if formato == "texto" else datetime(2026, 10, 27, 10, minuto)
        )  # Data API devolve texto com espaco; PyMySQL, datetime
        db.plan(resolved.preferred_equipment_id, quando)
    # Nenhum usado: desempate pelo nome do equipamento (Barra, Halteres, Polia).
    assert escolhidos == [100, 200, 300]
    assert len(set(escolhidos)) == 3


def test_recencia_e_o_maximo_entre_executado_e_planejado(db: FakeDb, candidates):
    candidates[:] = [SUPINO_BARRA, CRUCIFIXO_HALTER]
    # Barra executada ontem; Halteres so planejado ha uma semana -> Halteres e o mais antigo.
    db.executed = [{"gym_equipment_id": 11, "last_used_date": None, "last_used_at": datetime(2026, 10, 26, 9)}]
    db.plan(12, "2026-10-20 08:00:00")
    assert _generate(db).mapping_id == 200
    # Halteres planejado depois do treino de Barra -> volta para o Supino.
    db.plan(12, "2026-10-27 08:00:00")
    assert _generate(db).mapping_id == 100


def test_dois_livres_do_mesmo_grupo_nao_repetem_o_exercicio(db: FakeDb, candidates):
    """O orquestrador acumula o exercicio de cada item resolvido em
    `exclude_exercise_ids`; dois pares do MESMO exercicio (Supino barra e
    maquina) saem juntos."""
    candidates[:] = [SUPINO_BARRA, SUPINO_MAQUINA, CRUCIFIXO_HALTER]
    primeiro = _generate(db)
    assert primeiro.mapping_id == 100
    segundo = _generate(db, exclude=[primeiro.mapping_id])
    assert segundo.mapping_id == 200
    motivos = {(r["gym_equipment_id"], r["reason"]) for r in segundo.audit["rejected"]}
    assert motivos == {(11, subst.REJECT_EXERCISE_ALREADY_IN_SLOT), (13, subst.REJECT_EXERCISE_ALREADY_IN_SLOT)}


def test_exercicio_fixo_do_slot_fica_fora_do_livre(db: FakeDb):
    resolved = _generate(db, exclude=[100, 200])
    assert resolved.mapping_id == 300


def test_todos_os_exercicios_ja_no_slot_exige_escolha(db: FakeDb):
    resolved = _generate(db, exclude=[100, 200, 300])
    assert resolved.requires_user_choice
    assert resolved.mapping_id is None and resolved.preferred_equipment_id is None
    assert resolved.warnings == (WARNING_EQUIPMENT_UNRESOLVED,)


def test_sem_academia_exige_escolha_do_usuario(db: FakeDb):
    resolved = mode_b.resolve_item_equipment(db, _free_item(), None, user_id=USER_ID)
    assert resolved.requires_user_choice
    assert resolved.mapping_id is None and resolved.preferred_equipment_id is None
    assert resolved.warnings == (WARNING_EQUIPMENT_UNRESOLVED,)
    assert resolved.audit["kind"] == mode_b.KIND_FREE_EXERCISE
    assert db.queries == [] and db.load_calls == []


def test_sem_candidato_no_grupo_exige_escolha(db: FakeDb, candidates):
    candidates[:] = []
    resolved = _generate(db)
    assert resolved.requires_user_choice
    assert resolved.warnings == (WARNING_EQUIPMENT_UNRESOLVED,)


def test_candidatos_do_grupo_excluem_o_laboral():
    """A leitura dos candidatos (a mesma do M6) filtra o laboral pelo dono
    `domain/laboral.sql_not_laboral` e exige o grupo como PRIMARY."""
    executed: list[str] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def execute(self, sql, params=None):
            executed.append(sql)

        def fetchall(self):
            return []

    class Conn:
        def cursor(self):
            return Cursor()

    assert subst.load_group_candidates(Conn(), gym_id=GYM_ID, muscle_group_id=GROUP_ID, user_id=None) == []
    assert sql_not_laboral("ec") in executed[0]
    assert "PRIMARY" in executed[0]


def test_teto_de_seguranca_contra_o_alvo_do_item(db: FakeDb, monkeypatch):
    """Supino vem 1o no ranking, mas o teto dele (8) nao alcanca o RPE-alvo do
    item (9): vale o Crucifixo (teto 10), com o descarte auditado."""
    tetos = {100: 8, 200: 10, 300: 10}
    monkeypatch.setattr(
        mode_b, "resolve_training_safety_bulk", lambda conn, pairs: {p: _safety(*p, tetos[p[0]]) for p in pairs}
    )
    resolved = _generate(db, _free_item(rpe_target="9"))
    assert resolved.mapping_id == 200
    assert resolved.audit["item_target_rpe"] == 9
    assert resolved.audit["chosen_max_safe_rpe"] == 10
    assert resolved.audit["rejected"] == [
        {"gym_equipment_id": 11, "exercise_id": 100, "equipment_id": 1, "reason": mode_b.REJECT_BELOW_ITEM_TARGET,
         "max_safe_rpe": 8}
    ]


def test_nenhum_alcanca_o_alvo_vale_o_primeiro_do_ranking(db: FakeDb, monkeypatch):
    monkeypatch.setattr(mode_b, "resolve_training_safety_bulk", lambda conn, pairs: {p: _safety(*p, 8) for p in pairs})
    resolved = _generate(db, _free_item(rpe_target="10"))
    assert resolved.mapping_id == 100
    assert resolved.audit["rejected"] == []  # o clamp do RPE e da prescricao (`training_safety.compose`)


def test_tecnica_incompativel_tira_o_candidato(db: FakeDb, monkeypatch):
    monkeypatch.setattr(
        mode_b,
        "validate_technique",
        lambda code, safety: type("V", (), {"valid": safety.exercise_id == 200})(),
    )
    resolved = _generate(db, _free_item(advanced_technique="drop_set"))
    assert resolved.mapping_id == 200
    assert {r["exercise_id"] for r in resolved.audit["rejected"]} == {100, 300}


def test_item_com_par_fixo_nao_usa_o_ramo_livre(db: FakeDb, monkeypatch):
    """Modelos existentes (par fixo e ativo): nada muda e a recencia planejada
    nem e lida."""
    from app import services

    monkeypatch.setattr(services, "is_gym_equipment_pair_active", lambda gym_id, mapping_id, equipment_id: True)
    monkeypatch.setattr(
        mode_b,
        "_gym_equipment_for_exercise",
        lambda conn, gym_id, mapping_id: [{"id": 12, "mapping_id": 200, "equipment_id": 2, "equipment_name": "Halteres"}],
    )
    resolved = _generate(db, _free_item(mapping_id=200, equipment_id=2))
    assert (resolved.mapping_id, resolved.preferred_equipment_id) == (200, 12)
    assert resolved.warnings == ()
    assert resolved.audit == {}
    assert db.load_calls == []
    assert not any("workout_plan_exercises" in sql for sql in db.queries)


def test_ranking_padrao_nao_le_fichas_planejadas():
    db = FakeDb()
    db.plan(11, "2026-10-27 10:00:00")
    rows = [
        {"id": 11, "canonical_exercise_id": 100, "equipment_id": 1, "equipment_name": "Barra"},
        {"id": 12, "canonical_exercise_id": 200, "equipment_id": 2, "equipment_name": "Halteres"},
    ]
    with db.cursor() as cur:
        ranked = equipment_recommender.rank_gym_equipment_rows(cur, rows, gym_id=GYM_ID, user_id=USER_ID)
    assert [row["id"] for row in ranked] == [11, 12]
    assert not any("workout_plan_exercises" in sql for sql in db.queries)


def test_aviso_proprio_no_rascunho():
    texto = mode_b._warning_text(WARNING_EXERCISE_AUTO_CHOSEN, "Crucifixo", equipment_name="Halteres")
    assert "exercício livre" in texto and "Crucifixo" in texto and "Halteres" in texto
    sem = mode_b._warning_text(WARNING_EQUIPMENT_UNRESOLVED, "Exercício livre (Peito)", free_exercise=True)
    assert "Escolha o exercício" in sem


# ---------------------------------------------------------------------------
# Congelamento: a linha da ficha casa com o item livre
# ---------------------------------------------------------------------------

FIXED_ITEM = {"item_order": 1, "muscle_group_id": 5, "mapping_id": 900, "equipment_id": 9, "sets_count": 3}
FREE_ITEM = {"item_order": 2, "muscle_group_id": GROUP_ID, "mapping_id": None, "equipment_id": None, "sets_count": 4}


@pytest.fixture
def primary_groups(monkeypatch):
    from app import progression_state as ps

    monkeypatch.setattr(ps, "load_primary_groups", lambda conn, ids: ({900: 5, 200: GROUP_ID, 300: GROUP_ID}, {}))


def _row(row_id: int, mapping_id: int, sequence: int) -> dict[str, Any]:
    return {"workout_plan_exercise_id": row_id, "mapping_id": mapping_id, "sequence": sequence}


def test_congelamento_casa_a_linha_do_item_livre_pela_posicao(primary_groups):
    rows = [_row(1, 900, 1), _row(2, 200, 2)]
    matched = match_rows_to_model_items(None, rows, [FIXED_ITEM, FREE_ITEM])
    assert matched[1]["mapping_id"] == 900
    assert matched[2]["item_order"] == 2 and matched[2]["sets_count"] == 4


def test_congelamento_casa_o_item_livre_pelo_grupo_quando_o_rascunho_foi_reordenado(primary_groups):
    rows = [_row(1, 200, 1), _row(2, 900, 2)]
    matched = match_rows_to_model_items(None, rows, [FIXED_ITEM, FREE_ITEM])
    assert matched[2]["mapping_id"] == 900
    assert matched[1]["item_order"] == 2


def test_congelamento_nao_casa_exercicio_de_outro_grupo_com_o_livre(monkeypatch):
    from app import progression_state as ps

    monkeypatch.setattr(ps, "load_primary_groups", lambda conn, ids: ({900: 5, 777: 99}, {}))
    rows = [_row(1, 900, 1), _row(2, 777, 2)]
    matched = match_rows_to_model_items(None, rows, [FIXED_ITEM, FREE_ITEM])
    assert 2 not in matched


def test_congelamento_dois_livres_do_mesmo_grupo(primary_groups):
    second_free = {**FREE_ITEM, "item_order": 3, "sets_count": 2}
    rows = [_row(1, 900, 1), _row(2, 300, 2), _row(3, 200, 3)]
    matched = match_rows_to_model_items(None, rows, [FIXED_ITEM, FREE_ITEM, second_free])
    assert matched[2]["item_order"] == 2 and matched[3]["item_order"] == 3


def test_congelamento_item_livre_sem_auditoria_de_substituicao():
    request = PairRequest(exercise_id=200, equipment_id=2, muscle_group_id=GROUP_ID)
    assert substitution_audit_for_row(FREE_ITEM, request, {"preferred_equipment_id": 12}) is None
    from app.domain.free_exercise_item import is_free_exercise_item

    assert is_free_exercise_item(FREE_ITEM, require_kind=False)
    assert not is_free_exercise_item(FIXED_ITEM, require_kind=False)


# ---------------------------------------------------------------------------
# Previa do editor: item livre = series do grupo PRIMARY (peso 1,0)
# ---------------------------------------------------------------------------


def _preview_slot(*items: dict[str, Any]):
    from app.models import WorkoutPlanTemplateSlotIn

    return WorkoutPlanTemplateSlotIn(
        sequence=1,
        name="Dia 1",
        slot_type="normal",
        items=[{"item_order": i, "item_kind": "equipment", **item} for i, item in enumerate(items, start=1)],
    )


def test_previa_conta_o_item_livre_como_primario_do_grupo(monkeypatch):
    from app import workout_plan_templates as wpt
    from app.intelligence.volume_service import VolumeService

    monkeypatch.setattr(
        VolumeService,
        "get_muscle_activation_for_exercises",
        lambda self, exercises: [
            {"muscle_group": "Peito", "primary_equivalent": 3.0, "secondary_equivalent": 0.0,
             "stabilizer_equivalent": 0.0, "total_equivalent": 3.0},
            {"muscle_group": "Tríceps", "primary_equivalent": 0.0, "secondary_equivalent": 1.5,
             "stabilizer_equivalent": 0.0, "total_equivalent": 1.5},
        ],
    )
    slot = _preview_slot(
        {"mapping_id": 100, "equipment_id": 1, "muscle_group_id": GROUP_ID, "sets_count": 3},
        {"mapping_id": None, "equipment_id": None, "muscle_group_id": GROUP_ID, "sets_count": 4},
        {"mapping_id": None, "equipment_id": None, "muscle_group_id": 8, "sets_count": 2},
    )
    groups = wpt._compute_slot_activation_equipment_series(slot, None, {GROUP_ID: "Peito", 8: "Costas"})
    by_name = {g["muscle_group"]: g for g in groups}
    assert by_name["Peito"]["primary_equivalent"] == 7.0 and by_name["Peito"]["total_equivalent"] == 7.0
    assert by_name["Costas"]["primary_equivalent"] == 2.0 and by_name["Costas"]["secondary_equivalent"] == 0.0
    assert by_name["Tríceps"]["total_equivalent"] == 1.5
    assert [g["muscle_group"] for g in groups] == ["Costas", "Peito", "Tríceps"]


def test_previa_fonte_do_item_livre_no_detalhamento():
    from app import workout_plan_templates as wpt

    slot = _preview_slot(
        {"mapping_id": None, "equipment_id": None, "muscle_group_id": GROUP_ID, "sets_count": 4},
        {"mapping_id": 100, "equipment_id": 1, "muscle_group_id": GROUP_ID, "sets_count": 3},
    )
    assert [wpt._item_breakdown_source(item, "equipment_series") for item in slot.items] == ["free_exercise", "mapping"]
