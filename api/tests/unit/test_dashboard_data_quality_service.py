"""`app/intelligence/dashboard/data_quality_service.py` sem banco: cursor fake
que registra as queries e devolve linhas preparadas por regra.

O que estes testes protegem (plano §8.3, §15):

- as 10 regras do §8.3 existem, cada uma com caso POSITIVO e NEGATIVO;
- UMA query por regra (sem N+1) e `user_id` SEMPRE nos parametros;
- regra de CADASTRO (`catalog`) nao recebe a janela; regra de janela recebe;
- ausencia de dado nao vira 0 indevidamente: regra que nao pode ser avaliada
  (coluna `load_convention` ausente, janela de deploy) sai como limitacao
  declarada, nunca como item "0 problemas";
- `INFO` nunca escala para `ACTION`: series sem RPE nao derrubam o veredito;
- `overall` derivado corretamente (ACTION -> LOW, ATTENTION -> MEDIUM, nada
  -> HIGH);
- `require_user_id`: nenhum caminho aceita `None`.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app import load_conventions
from app.intelligence.dashboard import data_quality_service as svc
from app.intelligence.dashboard.contracts import DataQuality, DataQualityScope, Severity, WindowInfo, WindowKind
from app.intelligence.dashboard.metrics_registry import DATA_QUALITY_RULES, DataQualityRule

JANELA = WindowInfo(
    kind=WindowKind.ROLLING_90D,
    date_from=date(2026, 6, 24),
    date_to=date(2026, 9, 22),
    timezone="America/Sao_Paulo",
    days=90,
)
AGORA = datetime(2026, 9, 22, 20, 0, 0)
USUARIO = 7
OUTRO_USUARIO = 8

# Trecho que identifica a query de cada regra (o mesmo papel de um nome de
# rota num roteador fake): o teste nao reescreve o SQL, so o reconhece.
MARCADOR = {
    DataQualityRule.EXERCISES_WITHOUT_CANONICAL_MAPPING: "e.original_name AS example_label",
    DataQualityRule.MAPPINGS_WITHOUT_MUSCLE_CONTRIBUTION: "LEFT JOIN exercise_muscle_contributions emc",
    DataQualityRule.SETS_WITH_UNKNOWN_SET_TYPE: "set_type_value",
    DataQualityRule.EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION: "JOIN equipment_exercises eqex",
    DataQualityRule.SETS_WITHOUT_RPE: "es.rpe IS NULL",
    DataQualityRule.SESSIONS_WITHOUT_FINAL_STATUS: "a.session_status IS NULL",
    DataQualityRule.SESSIONS_NOT_LINKED_TO_PLAN: "a.workout_plan_id IS NULL",
    DataQualityRule.DUPLICATE_WEIGHT_SAME_DAY: "FROM intervals_wellness iw",
    DataQualityRule.ACTIVITIES_WITHOUT_STARTED_AT: "a.started_at IS NULL",
    DataQualityRule.MUSCLE_GROUPS_WITHOUT_TARGET: "training_type_muscle_group_targets tgt",
}

# Uma linha de exemplo plausivel por regra (`total_count` = a contagem que a
# funcao de janela devolveria).
LINHA = {
    DataQualityRule.EXERCISES_WITHOUT_CANONICAL_MAPPING: {
        "total_count": 3,
        "example_id": 41,
        "example_label": "Remada estranha",
        "example_date": date(2026, 9, 10),
    },
    DataQualityRule.MAPPINGS_WITHOUT_MUSCLE_CONTRIBUTION: {
        "total_count": 2,
        "example_id": 1001,
        "example_label": "Supino reto",
    },
    DataQualityRule.SETS_WITH_UNKNOWN_SET_TYPE: {
        "total_count": 12,
        "example_label": "myo_reps",
        "example_count": 12,
    },
    DataQualityRule.EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION: {
        "total_count": 4,
        "example_id": 55,
        "example_equipment": "Graviton",
        "example_exercise": "Barra fixa assistida",
    },
    DataQualityRule.SETS_WITHOUT_RPE: {
        "total_count": 30,
        "example_id": 900,
        "example_label": "Treino A",
        "example_date": date(2026, 9, 12),
        "example_count": 6,
    },
    DataQualityRule.SESSIONS_WITHOUT_FINAL_STATUS: {
        "total_count": 1,
        "example_id": 901,
        "example_label": "Treino B",
        "example_date": date(2026, 9, 11),
        "example_status": "in_progress",
    },
    DataQualityRule.SESSIONS_NOT_LINKED_TO_PLAN: {
        "total_count": 5,
        "example_id": 902,
        "example_label": "Treino C",
        "example_date": date(2026, 9, 9),
    },
    DataQualityRule.DUPLICATE_WEIGHT_SAME_DAY: {
        "total_count": 2,
        "example_date": date(2026, 9, 8),
        "example_min": 80.0,
        "example_max": 82.4,
        "example_sources": 2,
    },
    DataQualityRule.ACTIVITIES_WITHOUT_STARTED_AT: {
        "total_count": 7,
        "example_id": 903,
        "example_label": "Treino D",
        "example_date": date(2026, 9, 7),
    },
    DataQualityRule.MUSCLE_GROUPS_WITHOUT_TARGET: {
        "total_count": 2,
        "example_id": 6,
        "example_label": "Costas (superior)",
    },
}


@pytest.fixture(autouse=True)
def _sem_cache_de_coluna():
    load_conventions.reset_load_convention_column_cache()
    yield
    load_conventions.reset_load_convention_column_cache()


class FakeCursor:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.db.executed.append((sql, tuple(params or ())))
        self.db.resultado = self.db.resolver(sql)

    def fetchall(self):
        return list(self.db.resultado)

    def fetchone(self):
        return self.db.resultado[0] if self.db.resultado else None

    def close(self):
        return None


class FakeDb:
    """Responde `SELECT DATABASE()`, a deteccao da coluna 045 e as queries das
    regras (por marcador). Regra sem linha preparada devolve vazio."""

    def __init__(self, *, rows_por_regra=None, tem_load_convention=True):
        self.rows_por_regra = rows_por_regra or {}
        self.tem_load_convention = tem_load_convention
        self.executed: list[tuple[str, tuple]] = []
        self.resultado: list[dict] = []

    def cursor(self):
        return FakeCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def resolver(self, sql: str):
        if "SELECT DATABASE()" in sql:
            return [{"db": "musclefit_test"}]
        if "information_schema.COLUMNS" in sql:
            return [{"n": 1 if self.tem_load_convention else 0}]
        for regra, marcador in MARCADOR.items():
            if marcador in sql:
                return list(self.rows_por_regra.get(regra, []))
        raise AssertionError(f"query nao reconhecida pelo fake: {sql[:120]}")

    def queries_de_regra(self) -> list[tuple[str, tuple]]:
        return [
            (sql, params)
            for sql, params in self.executed
            if "SELECT DATABASE()" not in sql and "information_schema.COLUMNS" not in sql
        ]


def contexto(**kwargs) -> svc.QualityContext:
    base = dict(user_id=USUARIO, window=JANELA, now_local=AGORA, training_type_id=3)
    base.update(kwargs)
    return svc.QualityContext(**base)


# ---------------------------------------------------------------------------
# Catalogo de regras
# ---------------------------------------------------------------------------


def test_as_dez_regras_do_plano_estao_implementadas():
    implementadas = {regra for regra, _ in svc.RULE_FUNCTIONS}
    assert implementadas == set(DataQualityRule)
    assert len(svc.RULE_FUNCTIONS) == 10
    # toda regra implementada tem metadados (rotulo/severidade/fix_link)
    for regra in implementadas:
        meta = DATA_QUALITY_RULES[regra]
        assert meta.label and meta.description and meta.fix_link.startswith("/")


# ---------------------------------------------------------------------------
# Caso positivo e negativo de CADA regra
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("regra", list(DataQualityRule))
def test_caso_positivo_de_cada_regra(regra):
    db = FakeDb(rows_por_regra={regra: [LINHA[regra]]})
    funcao = dict(svc.RULE_FUNCTIONS)[regra]

    with db.cursor() as cursor:
        resultado = funcao(cursor, contexto())

    assert resultado.rule is regra
    assert resultado.count == LINHA[regra]["total_count"]
    assert resultado.evaluated is True
    assert len(resultado.examples) == 1
    exemplo = resultado.examples[0]
    assert exemplo["label"] and isinstance(exemplo["label"], str)
    # exemplo e id + rotulo legivel, nunca a linha crua do banco
    assert set(exemplo) == {"id", "label", "detail"}


@pytest.mark.parametrize("regra", list(DataQualityRule))
def test_caso_negativo_de_cada_regra(regra):
    """Sem ocorrencia: contagem zero, sem exemplo, mas a regra FOI avaliada."""
    db = FakeDb(rows_por_regra={})
    funcao = dict(svc.RULE_FUNCTIONS)[regra]

    with db.cursor() as cursor:
        resultado = funcao(cursor, contexto())

    assert resultado.count == 0
    assert resultado.examples == []
    assert resultado.evaluated is True


@pytest.mark.parametrize("regra", list(DataQualityRule))
def test_uma_query_por_regra_com_user_id(regra):
    db = FakeDb(rows_por_regra={regra: [LINHA[regra]]})
    funcao = dict(svc.RULE_FUNCTIONS)[regra]

    with db.cursor() as cursor:
        funcao(cursor, contexto())

    queries = db.queries_de_regra()
    assert len(queries) == 1, "cada regra le em UMA query (sem N+1)"
    sql, params = queries[0]
    assert "user_id = %s" in sql
    assert USUARIO in params
    assert OUTRO_USUARIO not in params


@pytest.mark.parametrize("regra", list(DataQualityRule))
def test_escopo_define_se_a_janela_entra_na_query(regra):
    """Regra de cadastro ignora a janela de proposito (§8.3); regra de janela
    filtra por `local_date BETWEEN`."""
    db = FakeDb(rows_por_regra={regra: [LINHA[regra]]})
    funcao = dict(svc.RULE_FUNCTIONS)[regra]

    with db.cursor() as cursor:
        funcao(cursor, contexto())

    sql, params = db.queries_de_regra()[0]
    escopo = DATA_QUALITY_RULES[regra].scope
    if escopo is DataQualityScope.WINDOW:
        assert "BETWEEN %s AND %s" in sql
        assert JANELA.date_from in params and JANELA.date_to in params
    else:
        assert JANELA.date_from not in params and JANELA.date_to not in params


# ---------------------------------------------------------------------------
# Regras com regra de negocio propria
# ---------------------------------------------------------------------------


def test_sessao_em_andamento_usa_o_relogio_do_usuario_e_24h():
    db = FakeDb(rows_por_regra={DataQualityRule.SESSIONS_WITHOUT_FINAL_STATUS: []})
    with db.cursor() as cursor:
        svc.rule_sessions_without_final_status(cursor, contexto())

    _, params = db.queries_de_regra()[0]
    assert datetime(2026, 9, 21, 20, 0, 0) in params, "corte = agora do USUARIO - 24h"


def test_series_sem_rpe_conta_so_series_de_trabalho():
    """Aquecimento sem RPE e o esperado: nao pode inflar a contagem."""
    assert "es.rpe IS NULL" in svc._SQL_SETS_WITHOUT_RPE
    assert "IN ('', 'working')" in svc._SQL_SETS_WITHOUT_RPE


def test_meta_sem_training_type_resolvido_conta_todos_os_grupos_treinados():
    """Sem Modelo com tipo de treino, o LEFT JOIN nao casa e TODO grupo cai no
    fallback generico - o caso em que "abaixo da meta" nao quer dizer nada."""
    db = FakeDb(
        rows_por_regra={
            DataQualityRule.MUSCLE_GROUPS_WITHOUT_TARGET: [LINHA[DataQualityRule.MUSCLE_GROUPS_WITHOUT_TARGET]]
        }
    )
    with db.cursor() as cursor:
        resultado = svc.rule_muscle_groups_without_target(cursor, contexto(training_type_id=None))

    _, params = db.queries_de_regra()[0]
    assert None in params
    assert resultado.count == 2


def test_pesagens_divergentes_cruzam_as_tres_fontes_numa_query():
    db = FakeDb(
        rows_por_regra={DataQualityRule.DUPLICATE_WEIGHT_SAME_DAY: [LINHA[DataQualityRule.DUPLICATE_WEIGHT_SAME_DAY]]}
    )
    with db.cursor() as cursor:
        resultado = svc.rule_duplicate_weight_same_day(cursor, contexto())

    sql, params = db.queries_de_regra()[0]
    for tabela in ("body_composition", "daily_biometrics", "intervals_wellness"):
        assert tabela in sql
    assert svc.WEIGHT_DIVERGENCE_KG in params
    assert "80.0 kg vs 82.4 kg" in resultado.examples[0]["detail"]


# ---------------------------------------------------------------------------
# Coluna `load_convention` ausente (janela de deploy da migracao 045)
# ---------------------------------------------------------------------------


def test_sem_a_coluna_045_a_regra_nao_e_avaliada_e_nao_consulta_o_par():
    db = FakeDb(tem_load_convention=False)
    with db.cursor() as cursor:
        resultado = svc.rule_equipment_pairs_without_load_convention(cursor, contexto())

    assert resultado.evaluated is False
    assert resultado.count == 0
    assert resultado.limitations, "a limitacao precisa ser declarada, nao silenciada"
    assert db.queries_de_regra() == [], "sem a coluna, nenhuma query e emitida (nada de 500)"


def test_regra_nao_avaliada_nao_vira_item_de_zero_problemas():
    resultados = [
        svc.RuleResult(DataQualityRule.EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION, 0, evaluated=False, limitations=["x"])
    ]
    assert svc.build_items(resultados) == []


def test_com_a_coluna_045_a_regra_roda_normalmente():
    db = FakeDb(
        rows_por_regra={
            DataQualityRule.EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION: [
                LINHA[DataQualityRule.EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION]
            ]
        },
        tem_load_convention=True,
    )
    with db.cursor() as cursor:
        resultado = svc.rule_equipment_pairs_without_load_convention(cursor, contexto())

    assert resultado.evaluated is True
    assert resultado.count == 4
    assert resultado.examples[0]["label"] == "Graviton - Barra fixa assistida"


# ---------------------------------------------------------------------------
# Severidade e veredito
# ---------------------------------------------------------------------------


def test_overall_derivado_das_severidades():
    assert svc.derive_overall([]) is DataQuality.HIGH
    assert svc.derive_overall([Severity.INFO]) is DataQuality.HIGH
    assert svc.derive_overall([Severity.INFO, Severity.ATTENTION]) is DataQuality.MEDIUM
    assert svc.derive_overall([Severity.ATTENTION, Severity.ACTION]) is DataQuality.LOW


def test_info_nunca_escala_por_contagem_alta():
    """10.000 series sem RPE continuam INFO e continuam HIGH: a contagem mede
    o tamanho do problema, nao muda a natureza dele (§8.3)."""
    resultados = [svc.RuleResult(DataQualityRule.SETS_WITHOUT_RPE, count=10_000)]
    itens = svc.build_items(resultados)
    assert itens[0]["severity"] is Severity.INFO
    assert svc.derive_overall([item["severity"] for item in itens]) is DataQuality.HIGH


def test_build_items_ignora_contagem_zero_e_traz_o_texto_do_registry():
    resultados = [
        svc.RuleResult(DataQualityRule.SETS_WITHOUT_RPE, count=0),
        svc.RuleResult(DataQualityRule.EXERCISES_WITHOUT_CANONICAL_MAPPING, count=3),
    ]
    itens = svc.build_items(resultados)
    assert [item["code"] for item in itens] == ["EXERCISES_WITHOUT_CANONICAL_MAPPING"]
    assert itens[0]["fix_link"] == "/dominio-exercicios"
    assert itens[0]["severity"] is Severity.ACTION
    assert itens[0]["scope"] is DataQualityScope.WINDOW


# ---------------------------------------------------------------------------
# Resposta completa
# ---------------------------------------------------------------------------


def test_resposta_completa_base_limpa():
    db = FakeDb()
    payload = svc.build_data_quality(db, user_id=USUARIO, window=JANELA, now_local=AGORA, training_type_id=3)

    assert payload["items"] == []
    assert payload["overall"] is DataQuality.HIGH
    assert payload["checked_rules"] == 10
    assert payload["data_quality"] is DataQuality.HIGH
    assert payload["metric_versions"] == {"data_quality": "dq-v1"}
    assert len(db.queries_de_regra()) == 10, "uma query por regra, nem mais"


def test_resposta_completa_com_problemas_e_limitacao_de_cadastro():
    db = FakeDb(
        rows_por_regra={
            DataQualityRule.EXERCISES_WITHOUT_CANONICAL_MAPPING: [
                LINHA[DataQualityRule.EXERCISES_WITHOUT_CANONICAL_MAPPING]
            ],
            DataQualityRule.MUSCLE_GROUPS_WITHOUT_TARGET: [LINHA[DataQualityRule.MUSCLE_GROUPS_WITHOUT_TARGET]],
            DataQualityRule.SETS_WITHOUT_RPE: [LINHA[DataQualityRule.SETS_WITHOUT_RPE]],
        }
    )
    payload = svc.build_data_quality(db, user_id=USUARIO, window=JANELA, now_local=AGORA, training_type_id=3)

    codigos = [item["code"] for item in payload["items"]]
    assert codigos == [
        "EXERCISES_WITHOUT_CANONICAL_MAPPING",
        "SETS_WITHOUT_RPE",
        "MUSCLE_GROUPS_WITHOUT_TARGET",
    ]
    assert payload["overall"] is DataQuality.LOW  # ha um ACTION
    assert any("cadastro" in limite for limite in payload["limitations"])


def test_resposta_sem_a_coluna_045_declara_a_limitacao_e_baixa_a_confianca():
    db = FakeDb(tem_load_convention=False)
    payload = svc.build_data_quality(db, user_id=USUARIO, window=JANELA, now_local=AGORA)

    assert payload["checked_rules"] == 9
    assert payload["data_quality"] is DataQuality.MEDIUM, "confianca NA RESPOSTA cai"
    assert payload["overall"] is DataQuality.HIGH, "o veredito dos DADOS nao inventa problema"
    assert any("045" in limite for limite in payload["limitations"])


def test_snapshot_logado_so_com_codigos_e_numeros(caplog):
    import logging

    db = FakeDb(
        rows_por_regra={
            DataQualityRule.EXERCISES_WITHOUT_CANONICAL_MAPPING: [
                LINHA[DataQualityRule.EXERCISES_WITHOUT_CANONICAL_MAPPING]
            ]
        }
    )
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        svc.build_data_quality(db, user_id=USUARIO, window=JANELA, now_local=AGORA)

    linhas = [registro.getMessage() for registro in caplog.records if "dq.snapshot" in registro.getMessage()]
    assert len(linhas) == 1
    assert "EXERCISES_WITHOUT_CANONICAL_MAPPING" in linhas[0]
    assert "Remada estranha" not in linhas[0], "nenhum nome de exercicio/sessao vai para o log"


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("invalido", [None, 0, -1, "abc", True])
def test_user_id_obrigatorio(invalido):
    with pytest.raises(ValueError):
        svc.build_data_quality(FakeDb(), user_id=invalido, window=JANELA, now_local=AGORA)


def test_nenhuma_query_de_escrita():
    """Servico de LEITURA: nenhum SQL do modulo escreve ou faz DDL."""
    import re

    proibidos = ("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP", "TRUNCATE")
    for nome, valor in vars(svc).items():
        if not (nome.startswith("_SQL_") and isinstance(valor, str)):
            continue
        # literais entre aspas saem antes (o vocabulario de set_type tem
        # 'drop set', que casaria com DROP por acidente)
        sem_literais = re.sub(r"'[^']*'", "''", valor).upper()
        assert sem_literais.lstrip().startswith("SELECT"), nome
        assert not any(re.search(rf"\b{verbo}\b", sem_literais) for verbo in proibidos), nome
