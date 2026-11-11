"""Lote G (nutrição adaptativa): fechar o dia, corrigir atividade e a leitura tolerante à migração 108.

Cenários montados pelo caminho real (publicar plano pela rota) no banco em memória de `tests/support/nutrition_fake.py`.
Cobre: PUT/DELETE de fechamento (idempotente, futuro 422, escopo), PUT/DELETE de correção (excluir, reclassificar, 404/422),
`closure`/`completeness_source`/`excluded` no JSON do dia, o efeito no histórico do motor (dia fechado completa; correção
exclui da soma), e o código novo sobre o schema antigo (sem a 108: nada de fechamento nem correção, nunca 500).

Rodar: cd api && python -m pytest tests/unit/test_nutrition_lote_g_fechamento_correcao.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app import nutrition_energy_service as energy_svc
from app import nutrition_schema as schema
from app import nutrition_targets_service as svc
from app.domain.nutrition import adaptive_energy as adaptive
from app.domain.nutrition import labels as nutrition_labels
from tests.unit.test_nutrition_energy_detail import (  # noqa: F401  (db é fixture)
    DESDE,
    FUSO,
    INICIO_FORCA,
    ONTEM,
    _client,
    _dia,
    db,
    _plano_adaptativo_publicado,
    _plano_legado_publicado,
)
from tests.unit.test_nutrition_modo_adaptativo_servico import ALUNO, HOJE, OUTRO, SYNC_UTC, _historico

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

AMANHA = HOJE + timedelta(days=1)


def _fechar(cliente, dia: date, user_id: int = ALUNO):
    return cliente.put(f"/nutrition/days/{dia.isoformat()}/close?user_id={user_id}")


def _reabrir(cliente, dia: date, user_id: int = ALUNO):
    return cliente.delete(f"/nutrition/days/{dia.isoformat()}/close?user_id={user_id}")


def _corrigir(cliente, atividade, corpo: dict, user_id: int = ALUNO):
    return cliente.put(f"/nutrition/activities/{atividade}/correction?user_id={user_id}", json=corpo)


def _desfazer(cliente, atividade, user_id: int = ALUNO):
    return cliente.delete(f"/nutrition/activities/{atividade}/correction?user_id={user_id}")


def _ingestao_do_dia(banco, dia: date, kcal: float) -> None:
    """Ingestão do dia ABAIXO da regra dos 70% da TMB, com as macros preenchidas (dia registrado, não completo)."""
    for linha in banco.meals.values():
        if linha["local_date"] == dia:
            linha["energy_kcal"] = Decimal(str(kcal))


def _forca(banco, dia: date = ONTEM, kcal: float = 428.0, user_id: int = ALUNO) -> int:
    return banco.add_intervals_row(user_id, dia, "WeightTraining", kcal, start=INICIO_FORCA, name="Musculação",
                                elapsed=3600, synced_at=SYNC_UTC)


# --- fechamento do dia -------------------------------------------------------------------------------------------


def test_fechar_e_reabrir_devolve_o_dia_atualizado_e_e_idempotente(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    admin = _client()
    assert _dia(admin, ONTEM).json()["closure"] == {"closed": False, "closed_at": None}
    fechado = _fechar(admin, ONTEM)
    assert fechado.status_code == 200, fechado.text
    corpo = fechado.json()
    assert corpo["local_date"] == ONTEM.isoformat() and corpo["closure"]["closed"] is True and corpo["closure"]["closed_at"]
    primeiro = corpo["closure"]["closed_at"]
    assert _fechar(admin, ONTEM).json()["closure"]["closed_at"] == primeiro  # refechar mantém o carimbo
    assert len(db.closures) == 1
    aberto = _reabrir(admin, ONTEM)
    assert aberto.status_code == 200 and aberto.json()["closure"] == {"closed": False, "closed_at": None}
    assert _reabrir(admin, ONTEM).status_code == 200 and db.closures == {}  # reabrir de novo é no-op


def test_fechar_hoje_vale_e_dia_futuro_e_422(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    admin = _client()
    assert _fechar(admin, HOJE).status_code == 200
    futuro = _fechar(admin, AMANHA)
    assert futuro.status_code == 422 and futuro.json()["detail"]["error"] == "future_day_cannot_be_closed"
    assert (OUTRO, AMANHA) not in db.closures and all(d <= HOJE for _, d in db.closures)


def test_o_fechamento_e_do_aluno_alvo_e_so_admin_ou_pt_age_por_outro(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    proprio = _client("regular", ALUNO)
    assert _fechar(proprio, ONTEM, ALUNO).status_code == 200
    assert (ALUNO, ONTEM) in db.closures
    assert _fechar(proprio, ONTEM, OUTRO).status_code == 403  # aluno comum não fecha o dia de outro
    assert (OUTRO, ONTEM) not in db.closures
    # o fechamento do aluno não aparece no dia de outro usuário
    assert _dia(_client(), ONTEM, OUTRO).json()["closure"]["closed"] is False


def test_dia_fechado_completa_o_dia_abaixo_dos_70_da_tmb_e_reabrir_volta_a_regra(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    _ingestao_do_dia(db, ONTEM, 600.0)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    admin = _client()
    antes = _dia(admin, ONTEM).json()
    assert antes["completeness_source"] is None and antes["completeness_source_label"] is None
    fechado = _fechar(admin, ONTEM).json()
    assert fechado["completeness_source"] == adaptive.COMPLETENESS_SOURCE_MANUAL_CLOSE
    assert fechado["completeness_source_label"] == nutrition_labels.COMPLETENESS_SOURCE_LABELS[adaptive.COMPLETENESS_SOURCE_MANUAL_CLOSE]
    reaberto = _reabrir(admin, ONTEM).json()
    assert reaberto["completeness_source"] is None


def test_dia_completo_pela_regra_expoe_a_origem_da_regra(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    corpo = _dia(_client(), ONTEM).json()
    assert corpo["completeness_source"] == adaptive.COMPLETENESS_SOURCE_INTAKE_RULE
    assert corpo["completeness_source_label"] == nutrition_labels.COMPLETENESS_SOURCE_LABELS[adaptive.COMPLETENESS_SOURCE_INTAKE_RULE]


def test_plano_legado_nao_expoe_origem_mas_o_fechamento_aparece(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    _forca(db)  # com Intervals o histórico é lido mesmo no legado: a origem não pode vazar
    _plano_legado_publicado(db, DESDE)
    corpo = _fechar(_client(), ONTEM).json()
    assert corpo["completeness_source"] is None and corpo["closure"]["closed"] is True


def test_historico_do_motor_recebe_os_dias_fechados(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    _ingestao_do_dia(db, ONTEM, 600.0)
    db.closures[(ALUNO, ONTEM)] = datetime(2026, 11, 5, 12, 0)
    db.closures[(OUTRO, ONTEM - timedelta(days=1))] = datetime(2026, 11, 5, 12, 0)  # outro usuário: não entra
    linhas = energy_svc.load_shadow_rows(db.conn().cursor(), ALUNO, energy_svc.horizon_days(HOJE)[0], HOJE)
    assert linhas.closed_days == frozenset({ONTEM})
    series = svc.load_user_inputs(db.conn().cursor(), ALUNO, HOJE, HOJE, HOJE).series
    historico = energy_svc.load_history(db.conn().cursor(), ALUNO, HOJE, series, timezone_name=FUSO)
    assert historico.completeness_source_on(ONTEM) == adaptive.COMPLETENESS_SOURCE_MANUAL_CLOSE


# --- correção de atividade ---------------------------------------------------------------------------------------


def test_excluir_atividade_mantem_na_lista_marcada_e_tira_da_soma(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    atividade = _forca(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    admin = _client()
    antes = _dia(admin, ONTEM).json()["energy_detail"]["activities"]
    assert antes["counted_activities"] == 1 and antes["activities"][0]["excluded"] is False
    resposta = _corrigir(admin, atividade, {"action": "exclude"})
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["local_date"] == ONTEM.isoformat()  # devolve o dia DA ATIVIDADE
    dia = corpo["energy_detail"]["activities"]
    assert dia["counted_activities"] == 0 and dia["active_kcal"] == 0.0
    item = dia["activities"][0]
    assert item["excluded"] is True and item["correction"] == {"action": "exclude", "energy_kind": None}
    assert db.corrections[(ALUNO, str(atividade))] == {"action": "exclude", "energy_kind": None}


def test_reclassificar_troca_o_tipo_da_kcal_e_desfazer_volta_ao_do_plano(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    atividade = _forca(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    admin = _client()
    corpo = _corrigir(admin, atividade, {"action": "reclassify", "energy_kind": "active"}).json()
    item = corpo["energy_detail"]["activities"]["activities"][0]
    assert item["calorie_mode"] == "active" and item["excluded"] is False
    assert item["correction"] == {"action": "reclassify", "energy_kind": "active"}
    assert item["active_kcal"] == pytest.approx(428.0)  # kcal ativa: sem desconto do repouso
    refeito = _corrigir(admin, atividade, {"action": "reclassify", "energy_kind": "gross"}).json()
    assert refeito["energy_detail"]["activities"]["activities"][0]["calorie_mode"] == "gross"
    assert len(db.corrections) == 1  # UPSERT: uma correção por atividade
    desfeito = _desfazer(admin, atividade).json()
    item = desfeito["energy_detail"]["activities"]["activities"][0]
    assert item["correction"] is None and item["excluded"] is False and item["calorie_mode"] == "gross"
    assert db.corrections == {}
    assert _desfazer(admin, atividade).status_code == 200  # idempotente


def test_atividade_de_outro_usuario_ou_inexistente_e_404(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    alheia = _forca(db, user_id=OUTRO)
    propria = _forca(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    aluno = _client("regular", ALUNO)
    for identificador in (alheia, 9999, "abc"):
        resposta = _corrigir(aluno, identificador, {"action": "exclude"})
        assert resposta.status_code == 404, (identificador, resposta.text)
        assert _desfazer(aluno, identificador).status_code == 404
    assert db.corrections == {}
    assert _corrigir(aluno, propria, {"action": "exclude"}).status_code == 200


@pytest.mark.parametrize(
    "corpo",
    [
        {"action": "apagar"},
        {"action": "reclassify"},
        {"action": "reclassify", "energy_kind": "unknown"},
        {"action": "exclude", "campo_extra": 1},
        {},
    ],
)
def test_corpo_invalido_da_correcao_e_422(db, corpo):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    atividade = _forca(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    assert _corrigir(_client(), atividade, corpo).status_code == 422
    assert db.corrections == {}


def test_excluir_ignora_o_tipo_enviado(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    atividade = _forca(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    assert _corrigir(_client(), atividade, {"action": "exclude", "energy_kind": "active"}).status_code == 200
    assert db.corrections[(ALUNO, str(atividade))]["energy_kind"] is None


def test_correcao_chega_ao_motor_como_correcao_tipada(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    atividade = _forca(db)
    db.corrections[(ALUNO, str(atividade))] = {"action": "exclude", "energy_kind": None}
    db.corrections[(OUTRO, "7")] = {"action": "exclude", "energy_kind": None}
    linhas = energy_svc.load_shadow_rows(db.conn().cursor(), ALUNO, energy_svc.horizon_days(HOJE)[0], HOJE)
    assert [(c.activity_id, c.action, c.energy_kind) for c in linhas.corrections] == [(str(atividade), "exclude", None)]


# --- código novo sobre o schema antigo (sem a 108) ---------------------------------------------------------------


def test_sem_a_108_o_diario_e_o_historico_seguem_sem_fechamento_nem_correcao(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    _forca(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    db.adjustment_tables = False
    schema.reset_nutrition_tables_cache()  # o cache positivo é global: sem isso a guarda deixaria passar
    resposta = _dia(_client(), ONTEM)
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["closure"] == {"closed": False, "closed_at": None}
    assert corpo["energy_detail"]["activities"]["counted_activities"] == 1
    linhas = energy_svc.load_shadow_rows(db.conn().cursor(), ALUNO, energy_svc.horizon_days(HOJE)[0], HOJE)
    assert linhas.closed_days == frozenset() and linhas.corrections == ()


def test_sem_a_108_as_escritas_respondem_503_citando_a_migracao(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    atividade = _forca(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    db.adjustment_tables = False
    schema.reset_nutrition_tables_cache()  # o cache positivo é global: sem isso a guarda deixaria passar
    admin = _client()
    for resposta in (_fechar(admin, ONTEM), _reabrir(admin, ONTEM), _corrigir(admin, atividade, {"action": "exclude"}),
                     _desfazer(admin, atividade)):
        assert resposta.status_code == 503
        assert resposta.json()["detail"]["migration"] == "108"


def test_falha_na_leitura_dos_fechamentos_degrada_sem_derrubar(db, monkeypatch, caplog):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    db.closures[(ALUNO, ONTEM)] = datetime(2026, 11, 5, 12, 0)

    def quebra(self, sql, params=None):
        raise RuntimeError("segredo-do-banco")

    cursor = db.conn().cursor()
    monkeypatch.setattr(type(cursor), "execute", quebra)
    from app import nutrition_adjustments as adj

    assert adj.load_closures(cursor, ALUNO, DESDE, HOJE) == {} and adj.load_corrections(cursor, ALUNO) == ()
    assert "RuntimeError" in caplog.text and "segredo-do-banco" not in caplog.text


def test_fechar_dia_antes_da_janela_do_motor_e_422(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    primeiro = energy_svc.horizon_days(HOJE)[0]
    assert _fechar(_client(), primeiro).status_code == 200
    antigo = _fechar(_client(), primeiro - timedelta(days=1))
    assert antigo.status_code == 422 and antigo.json()["detail"]["error"] == "day_outside_window"
    assert (ALUNO, primeiro - timedelta(days=1)) not in db.closures


def test_id_da_atividade_e_canonico_e_invalido_ou_enorme_e_404(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    atividade = _forca(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    admin = _client()
    assert _corrigir(admin, f"{atividade:04d}", {"action": "exclude"}).status_code == 200  # zeros à esquerda
    assert list(db.corrections) == [(ALUNO, str(atividade))]
    assert _desfazer(admin, f"00{atividade}").status_code == 200 and db.corrections == {}
    for ruim in ("9" * 40, "-1", "1.5", " ", "%C2%B2"):
        assert _corrigir(admin, ruim, {"action": "exclude"}).status_code == 404, ruim
        assert _desfazer(admin, ruim).status_code == 404, ruim
    assert db.corrections == {}


def test_a_deteccao_das_tabelas_da_108_e_uma_por_carga(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    schema.reset_nutrition_tables_cache()
    consultas: list = []
    cursor = db.conn().cursor()
    original = cursor.execute
    cursor.execute = lambda sql, params=None: (consultas.append((sql, params)), original(sql, params))[1]  # type: ignore[method-assign]
    energy_svc.load_shadow_rows(cursor, ALUNO, energy_svc.horizon_days(HOJE)[0], HOJE)
    assert sum(1 for sql, p in consultas if sql == schema.tables_sql(2) and p == schema.ADJUSTMENT_TABLES) == 1


def test_contagem_de_selects_do_diario_adaptativo_com_e_sem_intervals(db):  # noqa: F811 - fixture do pytest importada
    _historico(db)
    _plano_adaptativo_publicado(db, "gross", DESDE)
    admin = _client()
    _dia(admin, ONTEM)  # aquece os caches de detecção
    contagens = {}
    for nome, com_intervals in (("sem", False), ("com", True)):
        if com_intervals:
            _forca(db)
        consultas: list = []
        original = type(db.conn().cursor()).execute
        import unittest.mock as mock
        with mock.patch.object(type(db.conn().cursor()), "execute",
                               lambda self, sql, params=None: (consultas.append(sql), original(self, sql, params))[1]):
            assert _dia(admin, ONTEM).status_code == 200
        contagens[nome] = len(consultas)
    assert contagens["com"] > contagens["sem"]
