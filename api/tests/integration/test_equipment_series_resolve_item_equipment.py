"""Resolução do par (exercício, equipamento) de um item do modo
`equipment_series` contra a academia (`_resolve_item_equipment`).

Regressão da ficha 110 em produção (2026-09-17): quando o equipamento do
modelo não existia na academia da geração, o fallback escolhia a associação
de OUTRO exercício do mesmo grupo muscular e mantinha o nome/descrição do
exercício original ("Desenvolvimento de ombros na máquina" com Halteres,
"Extensão de tríceps na máquina" com Crossover).
"""

from __future__ import annotations

from app.generation import mode_b_legacy as engine


def _link(cur, gym_id: int, mapping_id: int, equipment_id: int) -> int:
    cur.execute(
        "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s, %s, %s, 1)",
        (gym_id, mapping_id, equipment_id),
    )
    return cur.lastrowid


def test_fallback_usa_outro_equipamento_do_mesmo_exercicio(db_conn, catalog, monkeypatch):
    ex, eq = catalog.exercises, catalog.equipment
    with db_conn.cursor() as cur:
        # "Banco reto" < "Polia" na ordem alfabética: o fallback antigo (por
        # grupo muscular) escolhia a associação do Supino reto.
        _link(cur, catalog.gym_id, ex["Supino reto"], eq["Banco reto"])
        crucifixo_polia = _link(cur, catalog.gym_id, ex["Crucifixo"], eq["Polia"])
    db_conn.commit()
    monkeypatch.setattr(engine, "_recommend_for_group", lambda *_: None)

    warnings: list[str] = []
    resolved = engine._resolve_item_equipment(
        # Equipamento do modelo ("Rack") não existe na academia.
        {"mapping_id": ex["Crucifixo"], "equipment_id": eq["Rack"], "muscle_group_id": catalog.muscle_groups["Peitoral"]},
        catalog.gym_id,
        {catalog.muscle_groups["Peitoral"]: "Peitoral"},
        warnings,
    )

    assert resolved["mapping_id"] == ex["Crucifixo"]
    assert resolved["preferred_equipment_id"] == crucifixo_polia
    assert resolved["equipment_id"] == eq["Polia"]
    assert resolved["equipment_name"] == "Polia"


def test_sem_equipamento_do_exercicio_nao_empresta_associacao_de_outro(db_conn, catalog, monkeypatch):
    ex, eq, mg = catalog.exercises, catalog.equipment, catalog.muscle_groups
    with db_conn.cursor() as cur:
        supino_banco = _link(cur, catalog.gym_id, ex["Supino reto"], eq["Banco reto"])
    db_conn.commit()

    recommended = {
        "id": supino_banco,
        "mapping_id": ex["Supino reto"],
        "equipment_id": eq["Banco reto"],
        "equipment_name": "Banco reto",
        "mapping_exercise_name": "Supino reto",
        "mapping_amazfit_name": "Supino reto",
    }
    calls: list[str] = []

    def fake_recommend(gym_id, group_name, user_id=None):
        calls.append(group_name)
        return recommended

    monkeypatch.setattr(engine, "_recommend_for_group", fake_recommend)

    warnings: list[str] = []
    resolved = engine._resolve_item_equipment(
        {"mapping_id": ex["Crucifixo"], "equipment_id": eq["Polia"], "muscle_group_id": mg["Peitoral"]},
        catalog.gym_id,
        {mg["Peitoral"]: "Peitoral"},
        warnings,
    )

    # A academia não tem nenhum equipamento para o Crucifixo: troca o
    # EXERCÍCIO inteiro pela recomendação do grupo (par coerente), em vez de
    # manter "Crucifixo" com a associação do Supino reto.
    assert calls == ["Peitoral"]
    assert resolved["mapping_id"] == ex["Supino reto"]
    assert resolved["preferred_equipment_id"] == supino_banco
    assert resolved["exercise_name"] == "Supino reto"


def test_lista_de_equipamentos_da_academia_traz_descricao_do_exercicio(db_conn, catalog, monkeypatch):
    """O Modo Treino usa `mapping_exercise_description` para exibir a
    descrição do exercício escolhido numa troca de equipamento."""
    from app import gyms

    ex, eq = catalog.exercises, catalog.equipment
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE exercise_catalog SET description = %s WHERE id = %s",
            ("Feche os braços à frente do peito.", ex["Crucifixo"]),
        )
        link_id = _link(cur, catalog.gym_id, ex["Crucifixo"], eq["Polia"])
    db_conn.commit()
    monkeypatch.setattr(gyms, "_require_identity", lambda *_args, **_kwargs: None)

    result = gyms.list_gym_equipment(
        gym_id=catalog.gym_id, mapping_id=None, primary_group=None, active_only=True, x_api_token=None, musclefit_session=None
    )

    row = next(item for item in result["items"] if item["id"] == link_id)
    assert row["mapping_exercise_description"] == "Feche os braços à frente do peito."
