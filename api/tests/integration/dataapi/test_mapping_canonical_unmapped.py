"""GET /mapping-canonical/unmapped (formato agrupado por nome) e escopo da
reclassificacao de POST /mapping-canonical/associate, nos dois backends
(PyMySQL e shim RDS Data API + dublê).

- `group_by=name`: um item por nome sem vinculo valido, SEM janela de dias,
  com contagens/datas; paginado; formato original (sem `group_by`) intacto;
  pagina maxima cabe no limite de 1 MiB da Data API.
- associate: so reprocessa exercicios sem vinculo valido + os do nome do
  alias. Antes reclassificava TUDO (`only_missing=False`) e sobrescrevia
  vinculos corretos (ex.: gravados na sessao por `resolve_mapping_id`).
"""

from __future__ import annotations

import datetime as _dt

import pytest

from app import exercise_mapping_canonical as emc
from tests.support.muscle_groups_mapped import PULLDOWN_ID, SUPINO_ID, UNMAPPED_NAME, seed_canonical_history

INACTIVE_ID = 3004
INACTIVE_NAME = "Maquina inativa"


def _add_exercise(cur, user_id, local_date, name, mapping_id, set_types):
    cur.execute(
        "INSERT INTO activities (user_id, local_date, name, activity_type) VALUES (%s, %s, 'Extra', 'Musculacao')",
        (user_id, local_date),
    )
    activity_id = cur.lastrowid
    cur.execute(
        "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) VALUES (%s, 1, %s, %s, %s)",
        (activity_id, name, mapping_id, len(set_types)),
    )
    exercise_id = cur.lastrowid
    for number, set_type in enumerate(set_types, 1):
        cur.execute(
            "INSERT INTO exercise_sets (exercise_id, set_number, reps, weight_kg, set_type) VALUES (%s, %s, 10, '10.00', %s)",
            (exercise_id, number, set_type),
        )
    return exercise_id


@pytest.fixture
def history(raw):
    ids = seed_canonical_history(raw)
    today = _dt.date.today()
    ids["old_date"] = today - _dt.timedelta(days=400)
    ids["inactive_date"] = today - _dt.timedelta(days=10)
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        try:
            # Mesmo nome, fora de qualquer janela de dias (400 dias atras).
            ids["old_unmapped"] = _add_exercise(
                cur, ids["user"], ids["old_date"], UNMAPPED_NAME, None, ["warmup", "working", "working"]
            )
            # Vinculo que nao resolve: exercicio do catalogo inativo.
            cur.execute("INSERT INTO exercise_catalog (id, name, is_active) VALUES (%s, %s, 0)", (INACTIVE_ID, INACTIVE_NAME))
            ids["inactive"] = _add_exercise(cur, ids["other_user"], ids["inactive_date"], INACTIVE_NAME, INACTIVE_ID, ["working"])
            # Nome vazio: nao aparece no agrupado (nao ha como associar).
            _add_exercise(cur, ids["user"], ids["inactive_date"], "", None, ["working"])
            # Ja vinculado a Puxada alta, mas o matcher resolveria para Supino reto.
            ids["linked_supino"] = _add_exercise(cur, ids["user"], ids["inactive_date"], "Supino reto", PULLDOWN_ID, ["working"])
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    return ids


def _mapping_ids(raw) -> dict[int, tuple[str, int | None]]:
    with raw.cursor() as cur:
        cur.execute("SELECT id, original_name, mapping_id FROM exercises ORDER BY id")
        return {row["id"]: (row["original_name"], row["mapping_id"]) for row in cur.fetchall()}


def _associate(name: str, target: int) -> dict:
    return emc.canonical_associate(emc.CanonicalAssociationRequest(alias_name=name, target_mapping_id=target))


# -- GET /mapping-canonical/unmapped?group_by=name -----------------------------


def test_unmapped_agrupado_por_nome_sem_janela_de_dias(history, db_backend):
    payload = emc.unmapped(limit=100, group_by="name", offset=0)
    assert payload == {
        "ok": True,
        "group_by": "name",
        "rows": [
            {
                "original_name": UNMAPPED_NAME,
                "exercises_count": 2,
                "sets_count": 4,  # 2 + 2 (aquecimento fora)
                "first_seen": history["old_date"].isoformat(),
                "last_seen": history["date_a"].isoformat(),
            },
            {
                "original_name": INACTIVE_NAME,
                "exercises_count": 1,
                "sets_count": 1,
                "first_seen": history["inactive_date"].isoformat(),
                "last_seen": history["inactive_date"].isoformat(),
            },
        ],
        "total": 2,
        "limit": 100,
        "offset": 0,
        "has_more": False,
    }


def test_unmapped_agrupado_paginado(history, db_backend):
    first = emc.unmapped(limit=1, group_by="name", offset=0)
    second = emc.unmapped(limit=1, group_by="name", offset=1)
    assert [row["original_name"] for row in first["rows"]] == [UNMAPPED_NAME]
    assert (first["total"], first["has_more"]) == (2, True)
    assert [row["original_name"] for row in second["rows"]] == [INACTIVE_NAME]
    assert second["has_more"] is False


def test_unmapped_formato_original_inalterado(history, db_backend):
    payload = emc.unmapped(limit=100, group_by=None, offset=0)
    assert set(payload) == {"ok", "rows"}
    assert all(set(row) == {"id", "original_name"} for row in payload["rows"])
    ids = [row["id"] for row in payload["rows"]]
    assert ids == sorted(ids, reverse=True)
    assert {history["old_unmapped"], history["inactive"]} <= set(ids)
    assert history["linked_supino"] not in ids


def test_unmapped_agrupado_pagina_maxima_cabe_em_1mib(raw, db_backend):
    """600 nomes distintos de 255 caracteres multibyte: a pagina e limitada a
    UNMAPPED_GROUPED_MAX_LIMIT e o resultado cabe no limite da Data API."""
    seed_canonical_history(raw)
    with raw.cursor() as cur:
        cur.execute("INSERT INTO activities (user_id, local_date, name, activity_type) VALUES (1, CURDATE(), 'Volume', 'Musculacao')")
        activity_id = cur.lastrowid
        cur.executemany(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) VALUES (%s, %s, %s, NULL, 0)",
            [(activity_id, index, (f"{index:04d} " + "é" * 300)[:255]) for index in range(600)],
        )
    payload = emc.unmapped(limit=1000, group_by="name", offset=0)
    assert payload["limit"] == emc.UNMAPPED_GROUPED_MAX_LIMIT
    assert len(payload["rows"]) == emc.UNMAPPED_GROUPED_MAX_LIMIT
    assert payload["total"] == 601  # + UNMAPPED_NAME do seed
    assert payload["has_more"] is True


# -- POST /mapping-canonical/associate: escopo da reclassificacao ---------------


def test_associate_grava_so_as_linhas_do_nome(history, raw, db_backend):
    before = _mapping_ids(raw)
    result = _associate(UNMAPPED_NAME, SUPINO_ID)
    after = _mapping_ids(raw)

    assert result["ok"] is True and result["operation"] == "created"
    changed = {exercise_id for exercise_id in before if before[exercise_id] != after[exercise_id]}
    unmapped_ids = {exercise_id for exercise_id, (name, _) in before.items() if name == UNMAPPED_NAME}
    assert len(unmapped_ids) == 2
    assert changed == unmapped_ids
    assert all(after[exercise_id][1] == SUPINO_ID for exercise_id in unmapped_ids)
    assert result["reclassification"]["written"] == 2
    # Vinculado a X (Puxada alta) cujo nome o matcher resolve para Y (Supino reto): intocado.
    assert after[history["linked_supino"]] == ("Supino reto", PULLDOWN_ID)
    # Vinculo que nao resolve (catalogo inativo) e nao casa: mantido, nao vira NULL.
    assert after[history["inactive"]] == (INACTIVE_NAME, INACTIVE_ID)


def test_reclassificacao_completa_sobrescreveria_vinculos(history, raw, db_backend):
    """Sensibilidade dos testes acima: o comportamento ANTERIOR do associate
    (reclassificar tudo) altera vinculos que nao sao do nome associado."""
    before = _mapping_ids(raw)
    emc.reclassify_exercises(limit=emc.ASSOCIATE_RECLASSIFY_LIMIT, only_missing=False)
    after = _mapping_ids(raw)
    assert after[history["linked_supino"]] == ("Supino reto", SUPINO_ID)
    assert any(before[i] != after[i] for i in before if before[i][0] not in (UNMAPPED_NAME, INACTIVE_NAME, "Supino reto"))


def test_associar_o_nome_a_outro_exercicio_acrescenta_o_apelido_sem_mexer_em_vinculos(history, raw, db_backend):
    """Decisao 2026-09-14: apelidos duplicados aceitos. Associar o mesmo nome a
    outro exercicio acrescenta o apelido no novo alvo, mantem o anterior e nao
    troca vinculos ja validos (antes: movia a linha do apelido e re-vinculava
    os exercicios do nome)."""
    _associate(UNMAPPED_NAME, SUPINO_ID)
    before = _mapping_ids(raw)
    result = _associate(UNMAPPED_NAME, PULLDOWN_ID)

    assert result["operation"] == "created"
    assert result["reclassification"]["written"] == 0
    assert _mapping_ids(raw) == before
    with raw.cursor() as cur:
        cur.execute("SELECT exercise_id FROM exercise_aliases WHERE alias = %s ORDER BY exercise_id", (UNMAPPED_NAME,))
        assert sorted(row["exercise_id"] for row in cur.fetchall()) == sorted([SUPINO_ID, PULLDOWN_ID])


def test_associate_sem_mudanca_nao_grava_vinculo(history, raw, db_backend, monkeypatch):
    _associate(UNMAPPED_NAME, SUPINO_ID)
    before = _mapping_ids(raw)

    writes: list[str] = []
    original = emc.execute

    def spy(sql, params=()):
        writes.append(" ".join(sql.split()))
        return original(sql, params)

    monkeypatch.setattr(emc, "execute", spy)
    result = _associate(UNMAPPED_NAME, SUPINO_ID)

    assert result["reclassification"]["written"] == 0
    assert result["operation"] == "updated"
    assert writes == []  # apelido ja existe no alvo: nada a gravar
    assert _mapping_ids(raw) == before
