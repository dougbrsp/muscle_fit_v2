"""Leitura agregada dos insumos da recuperacao (M20) - a parte que nao
precisa de banco.

O que cada bloco protege:

1. **Desempacotamento**: o texto que o banco devolve vira exatamente o mesmo
   dicionario de serie que a leitura antiga entregava ao laco de dose - e e
   por isso que a dose sai identica ate o ultimo bit.
2. **Truncagem detectada**: `GROUP_CONCAT` tem teto de 1024 bytes por padrao.
   Se ele cortar series, a contagem lida diverge do `COUNT(*)` do banco e o
   leitor LEVANTA erro - quem chama volta para a leitura antiga em vez de
   calcular uma dose menor em silencio.
3. **Dados de referencia numa consulta so**: contribuicoes, nomes de grupo,
   atributos primario/secundario e coeficiente pessoal saem todos da mesma
   resposta, com o mesmo criterio da leitura antiga (primeiro por `id`).
4. **Ownership**: sem `user_id` nao ha leitura.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.intelligence import recovery_read_model as rrm


class _ServicoFalso:
    """Devolve as linhas preparadas para cada consulta, reconhecida pelo
    trecho de SQL."""

    def __init__(self, referencia=None, historico=None, janela=None):
        self.referencia = referencia or []
        self.historico = historico or []
        self.janela = janela or []
        self.consultas: list[str] = []

    # M76 Lote 2b: janela e `trained_at` no fuso do usuario (Sao Paulo).
    def _days_ago(self, user_id, days):
        return date(2026, 9, 30) - timedelta(days=int(days))

    def _trained_at_sql(self, user_id, alias="a"):
        from app.intelligence.dashboard.time_windows import trained_at_sql

        return trained_at_sql(alias, offset_minutes=-180)

    def fetch_all(self, sql, params=()):
        self.consultas.append(sql)
        if "exercise_muscle_contributions" in sql:
            return list(self.referencia)
        if "JSON_ARRAYAGG" in sql:
            return list(self.historico)
        return list(self.janela)


def _referencia():
    return [
        {
            "kind": "contribution",
            "id": 1,
            "exercise_id": 900,
            "muscle_group_id": 5,
            "role": "PRIMARY",
            "stimulus_weight": "1.00",
            "fatigue_weight": "1.00",
            "muscle_group_name": "Peito",
        },
        {
            "kind": "contribution",
            "id": 2,
            "exercise_id": 900,
            "muscle_group_id": 6,
            "role": "SECONDARY",
            "stimulus_weight": "0.50",
            "fatigue_weight": "0.40",
            "muscle_group_name": "Triceps",
        },
        {
            "kind": "contribution",
            "id": 3,
            "exercise_id": 900,
            "muscle_group_id": 7,
            "role": "SECONDARY",
            "stimulus_weight": "0.20",
            "fatigue_weight": "0.20",
            "muscle_group_name": "Ombros",
        },
    ]


def _sessao(packed: list[str]):
    return [
        {
            "activity_id": 77,
            "local_date": "2026-09-20",
            "trained_at": "2026-09-20T19:00:00",
            "exercises_packed": "[" + ", ".join(f'"{item}"' for item in packed) + "]",
        }
    ]


# ---------------------------------------------------------------------------
# 1) Desempacotamento
# ---------------------------------------------------------------------------


def test_serie_desempacotada_tem_as_mesmas_chaves_do_caminho_antigo():
    item = rrm._parse_exercise(
        f"11|900|3|1|2|1:10:8.50:60.00:0::,2:8:9.00:60.00:{rrm.SET_TYPE_CODES.index('rest_pause')}:rest_pause:"
        "|42|per_side|Supino reto"
    )

    assert item["exercise_id"] == 11
    assert item["mapping_id"] == 900
    assert item["muscle_pain"] == 3
    assert item["joint_pain"] == 1
    assert item["original_name"] == "Supino reto"
    # Chave de comparabilidade do "Desempenho recente" - mesmas colunas do
    # caminho serie a serie.
    assert item["equipment_id"] == 42
    assert item["load_convention"] == "per_side"
    assert item["set_rows"] == [
        {"set_number": 1, "reps": 10, "rpe": 8.5, "weight_kg": 60.0, "set_type": "working", "technique_code": None, "measure": "reps"},
        {"set_number": 2, "reps": 8, "rpe": 9.0, "weight_kg": 60.0, "set_type": "rest_pause", "technique_code": "rest_pause", "measure": "reps"},
    ]


def test_campos_ausentes_viram_none_e_nunca_zero():
    item = rrm._parse_exercise("12||||1|::::::|||")

    assert item["mapping_id"] is None
    assert item["equipment_id"] is None
    assert item["load_convention"] is None
    assert item["muscle_pain"] is None
    assert item["joint_pain"] is None
    assert item["original_name"] is None
    assert item["set_rows"] == [
        {"set_number": None, "reps": None, "rpe": None, "weight_kg": None, "set_type": "working", "technique_code": None, "measure": "reps"}
    ]


def test_set_type_fora_do_vocabulario_cai_em_working():
    """Codigo desconhecido vale 1.00 no modelo - o mesmo que `working` -,
    entao o banco ja o codifica assim."""
    (serie,) = rrm._parse_exercise("13|900|||1|1:10:8.00:60.00:9::|||")["set_rows"]
    assert serie["set_type"] == "working"


# ---------------------------------------------------------------------------
# 2) Truncagem
# ---------------------------------------------------------------------------


def test_group_concat_truncado_levanta_em_vez_de_calcular_dose_menor():
    # o banco contou 4 series, mas so 2 vieram no texto
    with pytest.raises(rrm.PackedHistoryError):
        rrm._parse_exercise("14|900|||4|1:10:8.00:60.00:0::,2:10:8.00:60.00:0::|||")


def test_empacotamento_malformado_levanta():
    with pytest.raises(rrm.PackedHistoryError):
        rrm._parse_exercise("14|900|falta campo")


# ---------------------------------------------------------------------------
# 3) Dados de referencia numa consulta so
# ---------------------------------------------------------------------------


def test_referencia_resolve_contribuicoes_atributos_e_nomes():
    servico = _ServicoFalso(
        referencia=_referencia(),
        historico=_sessao(["11|900|||1|1:10:8.00:60.00:0::|||"]),
    )
    datasets = rrm.fetch_overview_datasets(servico, 14, user_id=3)

    assert [c["muscle_group_id"] for c in datasets["contributions_by_mapping"][900]] == [5, 6, 7]
    assert datasets["muscle_group_names_by_id"] == {5: "Peito", 6: "Triceps", 7: "Ombros"}
    # B12 (M76 Lote 2c): `muscle_recovery_coefficients` nao e mais lida.
    assert "personal_coefficients" not in datasets
    assert "muscle_recovery_coefficients" not in rrm.REFERENCE_DATA_SQL

    # atributos de grupo: PRIMARY e o primeiro por `id`; SECONDARY tambem -
    # o terceiro (id 3) e ignorado, como na leitura antiga.
    (item,) = datasets["history_items"]
    assert item["primary_group"] == "Peito"
    assert item["primary_group_id"] == 5
    assert item["secondary_group"] == "Triceps"
    assert item["secondary_group_id"] == 6
    assert item["secondary_participation"] == "0.50"


def test_historico_vem_ordenado_por_exercise_id():
    """A ordem importa: a dose de um grupo numa sessao e uma soma de floats, e
    somar na mesma ordem do caminho antigo e o que torna os dois identicos."""
    servico = _ServicoFalso(
        referencia=_referencia(),
        historico=_sessao(
            ["30|900|||1|1:10:8.00:60.00:0::|||", "11|900|||1|1:10:8.00:60.00:0::|||"]
        ),
    )
    datasets = rrm.fetch_overview_datasets(servico, 14, user_id=3)
    assert [item["exercise_id"] for item in datasets["history_items"]] == [11, 30]


def test_sao_tres_consultas():
    servico = _ServicoFalso(referencia=_referencia(), historico=_sessao([]))
    rrm.fetch_overview_datasets(servico, 14, user_id=3)
    assert len(servico.consultas) == 3


# ---------------------------------------------------------------------------
# 4) Ownership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("user_id", [None, 0, -1, "abc"])
def test_leitura_recusa_user_id_invalido_antes_de_qualquer_sql(user_id):
    class _ServicoProibido:
        def fetch_all(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("nenhuma consulta pode sair com user_id invalido")

    with pytest.raises(ValueError):
        rrm.fetch_overview_datasets(_ServicoProibido(), 14, user_id)
