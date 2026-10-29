"""Chave do PAR exercicio+equipamento usada para achar a nota do usuario
(PR-08b): `(exercise_catalog.id, equipment.id)`, a mesma do resolvedor de
seguranca. Puro, sem banco."""

from __future__ import annotations

from app import equipment_recommender as rec


def test_usa_o_mapping_id_do_item_do_recomendador():
    assert rec.pair_key({"id": 1, "mapping_id": 77, "equipment_id": 5}) == (77, 5)


def test_id_canonico_resolvido_pelo_chamador_vence_o_mapping_id_legado():
    row = {"id": 1, "mapping_id": 12, "canonical_exercise_id": 77, "equipment_id": 5}
    assert rec.pair_key(row) == (77, 5)


def test_sem_exercicio_ou_sem_equipamento_nao_ha_par():
    assert rec.pair_key({"id": 1, "equipment_id": 5}) is None
    assert rec.pair_key({"id": 1, "mapping_id": None, "equipment_id": 5}) is None
    assert rec.pair_key({"id": 1, "mapping_id": 77}) is None


def test_nota_e_do_par_nao_do_equipamento():
    stars = {(77, 5): 5, (78, 5): 1}
    assert rec._pair_stars(stars, {"mapping_id": 77, "equipment_id": 5}) == 5
    assert rec._pair_stars(stars, {"mapping_id": 78, "equipment_id": 5}) == 1
    assert rec._pair_stars(stars, {"mapping_id": 79, "equipment_id": 5}) is None  # sem nota -> 3 so no ranking
    assert rec._pair_stars(stars, {"equipment_id": 5}) is None
