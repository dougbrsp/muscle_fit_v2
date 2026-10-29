"""Modo de contagem do volume por Modelo - Lote 2 (migracao 073), sem banco.

- versao do Modelo: o modo entra no cabecalho canonico (trocar = versao nova),
  no INSERT da versao e da reversao, e `row_to_version` le o modo;
- default unico: `template_versions` usa `DEFAULT_VOLUME_COUNTING_MODE` do
  motor (sem literal proprio);
- `WorkoutPlanTemplateIn` valida o modo pelo enum do motor; `None` = editar
  preserva o gravado (`_resolve_counting_mode`);
- loader: faixas e meta semanal DO MODO pedido, sem fallback para outro modo;
- `evaluate_template` avalia no modo persistido e o payload v5 o expoe.

Integracao (MySQL, dois backends): `test_modo_contagem_volume_versao_073.py`
e `test_muscle_volume_classes_crud.py`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import muscle_volume_classes as mvc
from app import template_versions as tv
from app import workout_plan_templates as wpt
from app.intelligence import methodology_volume as mv
from app.intelligence import methodology_volume_service as svc
from app.models import WorkoutPlanTemplateIn
from tests.unit.test_template_versions import AGORA, FakeCur, _payload, _version_row

PONDERADO = mv.VolumeCountingMode.PRIMARY_SECONDARY.value
PRIMARIO = mv.VolumeCountingMode.PRIMARY_ONLY.value


# ---------------------------------------------------------------------------
# Versao do Modelo
# ---------------------------------------------------------------------------


def test_default_do_versionamento_vem_do_motor():
    assert not hasattr(tv, "DEFAULT_COUNTING_MODE")
    assert tv.counting_mode_of({}) == tv.counting_mode_of({"volume_counting_mode": None}) == PRIMARIO
    assert tv.counting_mode_of({"volume_counting_mode": mv.VolumeCountingMode.PRIMARY_SECONDARY}) == PONDERADO
    assert tv.counting_mode_of({"volume_counting_mode": PONDERADO.encode()}) == PONDERADO


def test_row_to_version_le_o_modo_e_linha_sem_coluna_e_primary_only():
    assert tv.row_to_version(_version_row(volume_counting_mode=PONDERADO)).volume_counting_mode == PONDERADO
    assert tv.row_to_version(_version_row()).volume_counting_mode == PRIMARIO


def test_sql_da_versao_e_da_reversao_carregam_o_modo():
    assert "v.volume_counting_mode" in tv._VERSION_COLUMNS
    assert "volume_counting_mode" in tv.INSERT_VERSION_SQL
    assert tv.INSERT_VERSION_SQL.count("%s") == 15  # + training_goal (093), depois do modo
    # A reversao copia o modo DA VERSAO de origem (coluna do SELECT, nao parametro).
    insert_cols, select_cols = tv.REVERT_INSERT_SQL.split("SELECT", 1)
    assert "volume_counting_mode" in insert_cols and "volume_counting_mode" in select_cols


def test_trocar_o_modo_e_mudanca_estrutural():
    vigente = tv.row_to_version(_version_row())
    assert tv.canonical_header(_payload()) == tv.canonical_header(vigente)
    assert tv.canonical_header(_payload(volume_counting_mode=PONDERADO)) != tv.canonical_header(vigente)


def test_write_structural_version_grava_o_modo_na_versao_nova():
    atual = _payload()
    cur = FakeCur(vigente=_version_row(version_number=3), slots=atual["slots"])
    novo = _payload(volume_counting_mode=PONDERADO, slots=atual["slots"])

    assert tv.write_structural_version(cur, 7, novo, None, actor=42) is not None
    [insert] = [p for sql, p in cur.executed if sql.startswith("INSERT INTO workout_plan_template_versions")]
    # colunas: ..., volume_counting_mode, training_goal (093) -> o modo é o penúltimo
    assert insert[9] == AGORA and insert[-2] == PONDERADO and insert[-1] == "hypertrophy"


def test_mesmo_modo_e_mesma_estrutura_nao_cria_versao():
    atual = _payload(volume_counting_mode=PONDERADO)
    cur = FakeCur(vigente=_version_row(volume_counting_mode=PONDERADO), slots=atual["slots"])
    assert tv.write_structural_version(cur, 7, atual, None, actor=42) is None


# ---------------------------------------------------------------------------
# Contrato da API
# ---------------------------------------------------------------------------


def test_payload_valida_o_modo_pelo_enum_do_motor():
    assert WorkoutPlanTemplateIn(name="x").volume_counting_mode is None
    assert WorkoutPlanTemplateIn(name="x", volume_counting_mode=PONDERADO).volume_counting_mode == PONDERADO
    with pytest.raises(ValidationError):
        WorkoutPlanTemplateIn(name="x", volume_counting_mode="secundario")
    with pytest.raises(ValidationError):
        wpt.MethodologyVolumePreviewIn(volume_counting_mode="secundario")


def test_editar_sem_o_campo_preserva_e_criar_usa_o_default():
    sem_campo = WorkoutPlanTemplateIn(name="x")
    assert wpt._resolve_counting_mode(sem_campo, PONDERADO) == PONDERADO
    assert wpt._resolve_counting_mode(sem_campo, None) == PRIMARIO
    explicito = WorkoutPlanTemplateIn(name="x", volume_counting_mode=PRIMARIO)
    assert wpt._resolve_counting_mode(explicito, PONDERADO) == PRIMARIO
    payload = wpt._structural_payload(explicito, "full_body", [], counting_mode=PONDERADO)
    assert payload["volume_counting_mode"] == PONDERADO
    assert wpt._structural_payload({"volume_counting_mode": PONDERADO}, "abc", [])["volume_counting_mode"] == PONDERADO


def test_admin_valida_o_modo_da_rota():
    assert mvc._counting_mode(None) == PRIMARIO
    assert mvc._counting_mode(PONDERADO) == PONDERADO
    with pytest.raises(HTTPException) as erro:
        mvc._counting_mode("ambos")
    assert erro.value.status_code == 422


# ---------------------------------------------------------------------------
# Loader: faixas e meta semanal do modo
# ---------------------------------------------------------------------------


class _Cur:
    def __init__(self, respostas):
        self.respostas = respostas
        self.chamadas: list[tuple[str, tuple | None]] = []
        self._ultimo: list = []

    def execute(self, sql, params=None):
        self.chamadas.append((" ".join(sql.split()), params))
        self._ultimo = next(v for k, v in self.respostas.items() if k in sql)

    def fetchall(self):
        return self._ultimo


_FAIXA = {"class_id": 1, "frequency_bucket": "2x", "sets_min": Decimal("9.0"), "sets_max": Decimal("16.0"),
          "exercises_min": 3, "exercises_max": 4, "sets_source": "methodology", "sets_evidence": "EXG-016",
          "exercises_source": "methodology", "exercises_evidence": "EXG-016"}


def test_primary_only_le_as_tabelas_da_057_como_antes():
    cur = _Cur({
        "FROM muscle_volume_classes": [{"id": 1, "name": "Grandes", "weekly_sets_min": 18, "weekly_sets_max": 32,
                                        "weekly_source": "derived", "weekly_evidence": None}],
        "FROM muscle_volume_class_ranges": [_FAIXA],
    })
    classes = svc.load_volume_classes(cur)
    (sql_classes, p_classes), (sql_faixas, p_faixas) = cur.chamadas
    assert "weekly_targets" not in sql_classes and p_classes is None
    # A tabela da 057 E o cadastro de primary_only: mesma leitura de antes da 073.
    assert sql_faixas.endswith("FROM muscle_volume_class_ranges") and p_faixas is None
    assert classes[1].has_weekly_target and classes[1].range_for("2x").sets_min == 9


def test_modo_ponderado_le_a_filha_e_nao_herda_de_outro_modo():
    cur = _Cur({
        # LEFT JOIN sem linha do modo: meta NULL (eixo semanal fora), nunca a de primary_only.
        "FROM muscle_volume_classes c": [{"id": 1, "name": "Grandes", "weekly_sets_min": None,
                                          "weekly_sets_max": None, "weekly_source": None, "weekly_evidence": None}],
        "FROM muscle_volume_class_mode_ranges": [],
    })
    classes = svc.load_volume_classes(cur, PONDERADO)
    (sql_classes, p_classes), (sql_faixas, p_faixas) = cur.chamadas
    assert "LEFT JOIN muscle_volume_class_weekly_targets" in sql_classes and p_classes == (PONDERADO,)
    assert "FROM muscle_volume_class_mode_ranges WHERE counting_mode = %s" in sql_faixas
    assert p_faixas == (PONDERADO,)
    assert not classes[1].has_weekly_target and classes[1].range_for("2x") is None


def test_evaluate_template_usa_o_modo_persistido_e_o_payload_v5_o_expoe(monkeypatch):
    pedidos = []

    def fake_inputs(mapping_ids, counting_mode=mv.DEFAULT_VOLUME_COUNTING_MODE):
        pedidos.append(mv.VolumeCountingMode(counting_mode))
        return svc.MethodologyInputs(canonical_ids={}, contributions={}, groups=(), counting_mode=counting_mode)

    monkeypatch.setattr(svc, "load_methodology_inputs", fake_inputs)
    modelo = {"slots": [], "volume_mode": "equipment_series", "volume_counting_mode": PONDERADO}
    payload = svc.evaluation_to_payload(svc.evaluate_template(modelo, mv.MethodologyIntent()))
    assert pedidos == [mv.VolumeCountingMode.PRIMARY_SECONDARY]
    assert (payload["counting_mode"], payload["metric_version"]) == (PONDERADO, "methodology-volume-v5")

    sem_coluna = svc.evaluation_to_payload(svc.evaluate_template({"slots": []}, mv.MethodologyIntent()))
    assert sem_coluna["counting_mode"] == PRIMARIO
