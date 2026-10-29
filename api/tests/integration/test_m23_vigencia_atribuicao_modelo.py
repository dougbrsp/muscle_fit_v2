"""M23 (migracao 049) com banco real: UMA atribuicao de Modelo vigente por
usuario, e o historico das anteriores preservado.

O que so o banco de verdade prova, e por isso esta aqui e nao no unitario:

1. o `UNIQUE (active_user_id)` RECUSA uma segunda vigente - a invariante e do
   banco, nao da aplicacao, entao um INSERT cru tem de bater na trave;
2. `POST /assignments` faz a troca INTEIRA (encerra a anterior e abre a nova)
   num instante so, sem janela em que o usuario fica sem Modelo;
3. `DELETE /assignments/{user}` deixou de apagar a linha - o passado fica;
4. reatribuir um Modelo JA largado funciona (era isso que o
   `UNIQUE (template_id, user_id)` antigo proibia) e gera duas linhas de
   historico para o mesmo par.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import workout_plan_templates
from app.security import Identity, get_current_identity, require_admin_or_pt


@pytest.fixture
def api(db_conn, catalog):
    """App so com o router de modelos, autenticado como o proprio catalogo."""
    app = FastAPI()
    app.include_router(workout_plan_templates.router)
    identidade = Identity(user_id=catalog.user_id, role="admin", source="session")
    app.dependency_overrides[get_current_identity] = lambda: identidade
    app.dependency_overrides[require_admin_or_pt] = lambda: identidade
    return TestClient(app)


@pytest.fixture
def modelos(db_conn, catalog):
    """Dois modelos ativos e um usuario-aluno, sem atribuicao nenhuma."""
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM workout_plan_template_assignments")
        cur.execute("DELETE FROM workout_plan_templates")
        ids = []
        for nome in ("Modelo A", "Modelo B"):
            cur.execute(
                "INSERT INTO workout_plan_templates (name, volume_mode, is_active, created_by_user_id) "
                "VALUES (%s, 'percent_budget', 1, %s)",
                (nome, catalog.user_id),
            )
            ids.append(cur.lastrowid)
        cur.execute(
            "INSERT INTO users (username, password_hash, role, is_active) VALUES ('aluno_m23', 'x', 'regular', 1)"
        )
        aluno_id = cur.lastrowid
    db_conn.commit()
    return ids[0], ids[1], aluno_id


def _linhas(conn, user_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT template_id, assigned_at, unassigned_at, unassigned_by_user_id "
            "FROM workout_plan_template_assignments WHERE user_id = %s "
            "ORDER BY assigned_at ASC, id ASC",
            (user_id,),
        )
        return list(cur.fetchall() or [])


# ---------------------------------------------------------------------------
# 1. A invariante e do BANCO
# ---------------------------------------------------------------------------


def test_o_banco_recusa_uma_segunda_atribuicao_vigente(db_conn, modelos):
    """Sem passar pela API: dois INSERT crus para o mesmo usuario. O segundo
    tem de falhar - e essa a diferenca entre "a aplicacao valida" e "e
    impossivel"."""
    # A conexao de teste embrulha o erro nativo do MySQL em `DataApiError`
    # (mesma fronteira do Data API em producao), entao e por ela que o
    # teste espera - a mensagem original do InnoDB vem dentro.
    from app.db_pymysql import DataApiError

    modelo_a, modelo_b, aluno = modelos
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_template_assignments (template_id, user_id, assigned_by_user_id) "
            "VALUES (%s, %s, %s)",
            (modelo_a, aluno, aluno),
        )
    db_conn.commit()

    with pytest.raises(DataApiError) as exc:
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO workout_plan_template_assignments (template_id, user_id, assigned_by_user_id) "
                "VALUES (%s, %s, %s)",
                (modelo_b, aluno, aluno),
            )
    db_conn.rollback()
    assert "uq_wpta_active_user" in str(exc.value)


def test_o_historico_aceita_quantas_linhas_encerradas_quiser(db_conn, modelos):
    """O UNIQUE e sobre `active_user_id`, que e NULL nas encerradas - e NULL
    nao colide em indice UNIQUE. Sem isso, historico e invariante seriam
    incompativeis."""
    modelo_a, modelo_b, aluno = modelos
    with db_conn.cursor() as cur:
        for template in (modelo_a, modelo_b, modelo_a):
            cur.execute(
                "INSERT INTO workout_plan_template_assignments "
                "(template_id, user_id, assigned_by_user_id, unassigned_at) VALUES (%s, %s, %s, NOW())",
                (template, aluno, aluno),
            )
    db_conn.commit()
    assert len(_linhas(db_conn, aluno)) == 3


# ---------------------------------------------------------------------------
# 2. `POST` troca em vez de somar
# ---------------------------------------------------------------------------


def test_atribuir_um_segundo_modelo_encerra_o_primeiro_no_mesmo_instante(api, db_conn, modelos):
    modelo_a, modelo_b, aluno = modelos

    assert api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": aluno}).status_code == 200
    assert api.post(f"/workout-plan-templates/{modelo_b}/assignments", json={"user_id": aluno}).status_code == 200

    linhas = _linhas(db_conn, aluno)
    assert [l["template_id"] for l in linhas] == [modelo_a, modelo_b]
    # A anterior foi encerrada...
    assert linhas[0]["unassigned_at"] is not None
    # ...exatamente quando a seguinte comecou: a linha do tempo nao tem buraco
    # nem sobreposicao, entao "qual era o Modelo neste instante" sempre tem
    # uma resposta e so uma.
    assert linhas[0]["unassigned_at"] == linhas[1]["assigned_at"]
    assert linhas[1]["unassigned_at"] is None


def test_a_troca_registra_quem_encerrou(api, db_conn, modelos, catalog):
    modelo_a, modelo_b, aluno = modelos
    api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": aluno})
    api.post(f"/workout-plan-templates/{modelo_b}/assignments", json={"user_id": aluno})
    assert _linhas(db_conn, aluno)[0]["unassigned_by_user_id"] == catalog.user_id


def test_reatribuir_o_modelo_que_ja_e_o_vigente_e_409_e_nao_corta_o_historico(api, db_conn, modelos):
    """Encerrar e reabrir a MESMA vigencia criaria um corte falso na linha do
    tempo - um "troquei de ficha" que nunca aconteceu."""
    modelo_a, _modelo_b, aluno = modelos
    api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": aluno})
    antes = _linhas(db_conn, aluno)

    resposta = api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": aluno})
    assert resposta.status_code == 409
    assert "vigente" in resposta.json()["detail"]
    assert _linhas(db_conn, aluno) == antes


def test_voltar_para_um_modelo_ja_largado_funciona(api, db_conn, modelos):
    """O `UNIQUE (template_id, user_id)` antigo proibia exatamente isto - e
    trocar de ficha e voltar e um caso real, nao uma curiosidade."""
    modelo_a, modelo_b, aluno = modelos
    for template in (modelo_a, modelo_b, modelo_a):
        assert api.post(f"/workout-plan-templates/{template}/assignments", json={"user_id": aluno}).status_code == 200

    linhas = _linhas(db_conn, aluno)
    assert [l["template_id"] for l in linhas] == [modelo_a, modelo_b, modelo_a]
    assert [l["unassigned_at"] is None for l in linhas] == [False, False, True]


def test_dois_usuarios_podem_seguir_o_mesmo_modelo_ao_mesmo_tempo(api, db_conn, modelos, catalog):
    """A invariante e "um Modelo por USUARIO", nao "um usuario por Modelo"."""
    modelo_a, _modelo_b, aluno = modelos
    assert api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": aluno}).status_code == 200
    resposta = api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": catalog.user_id})
    assert resposta.status_code == 200
    assert len(resposta.json()["items"]) == 2


# ---------------------------------------------------------------------------
# 3. `DELETE` nao apaga mais
# ---------------------------------------------------------------------------


def test_desatribuir_encerra_a_vigencia_e_mantem_a_linha(api, db_conn, modelos):
    modelo_a, _modelo_b, aluno = modelos
    api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": aluno})

    assert api.delete(f"/workout-plan-templates/{modelo_a}/assignments/{aluno}").status_code == 200

    linhas = _linhas(db_conn, aluno)
    assert len(linhas) == 1, "a linha tem de FICAR - era isso que o DELETE fisico destruia"
    assert linhas[0]["unassigned_at"] is not None


def test_desatribuir_duas_vezes_e_404_e_nao_reescreve_o_primeiro_fim(api, db_conn, modelos):
    modelo_a, _modelo_b, aluno = modelos
    api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": aluno})
    api.delete(f"/workout-plan-templates/{modelo_a}/assignments/{aluno}")
    fim = _linhas(db_conn, aluno)[0]["unassigned_at"]

    assert api.delete(f"/workout-plan-templates/{modelo_a}/assignments/{aluno}").status_code == 404
    assert _linhas(db_conn, aluno)[0]["unassigned_at"] == fim


def test_depois_de_desatribuir_o_usuario_pode_receber_qualquer_modelo(api, db_conn, modelos):
    modelo_a, modelo_b, aluno = modelos
    api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": aluno})
    api.delete(f"/workout-plan-templates/{modelo_a}/assignments/{aluno}")
    assert api.post(f"/workout-plan-templates/{modelo_b}/assignments", json={"user_id": aluno}).status_code == 200
    assert [l["unassigned_at"] is None for l in _linhas(db_conn, aluno)] == [False, True]


# ---------------------------------------------------------------------------
# 4. A listagem separa vigente de historico
# ---------------------------------------------------------------------------


def test_a_listagem_padrao_mostra_so_quem_segue_o_modelo_hoje(api, modelos):
    modelo_a, modelo_b, aluno = modelos
    api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": aluno})
    api.post(f"/workout-plan-templates/{modelo_b}/assignments", json={"user_id": aluno})

    vigentes = api.get(f"/workout-plan-templates/{modelo_a}/assignments").json()["items"]
    assert vigentes == [], "o aluno trocou para o Modelo B; ninguem segue o A hoje"


def test_include_history_traz_a_vigencia_encerrada_com_as_duas_pontas(api, modelos):
    modelo_a, modelo_b, aluno = modelos
    api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": aluno})
    api.post(f"/workout-plan-templates/{modelo_b}/assignments", json={"user_id": aluno})

    historico = api.get(f"/workout-plan-templates/{modelo_a}/assignments?include_history=1").json()["items"]
    assert len(historico) == 1
    assert historico[0]["user_id"] == aluno
    assert historico[0]["assigned_at"] is not None
    assert historico[0]["unassigned_at"] is not None


# ---------------------------------------------------------------------------
# 5. Quem le "qual e o Modelo deste usuario"
# ---------------------------------------------------------------------------


def test_o_resolvedor_segue_a_troca(api, modelos):
    """`resolve_active_template_id` e o unico lugar que responde essa pergunta
    (antes eram quatro copias com `ORDER BY assigned_at DESC LIMIT 1`)."""
    from app.template_assignments import resolve_active_template_id

    modelo_a, modelo_b, aluno = modelos
    assert resolve_active_template_id(aluno) is None

    api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": aluno})
    assert resolve_active_template_id(aluno) == modelo_a

    api.post(f"/workout-plan-templates/{modelo_b}/assignments", json={"user_id": aluno})
    assert resolve_active_template_id(aluno) == modelo_b

    api.delete(f"/workout-plan-templates/{modelo_b}/assignments/{aluno}")
    assert resolve_active_template_id(aluno) is None


def test_resolver_por_data_devolve_o_modelo_da_epoca(api, db_conn, modelos):
    """O coracao do M23: um instante DENTRO da vigencia encerrada resolve o
    Modelo antigo, nao o de hoje. E o que faz um ciclo antigo ser medido
    contra a meta do plano daquela epoca.

    As duas vigencias sao escritas com instantes EXPLICITOS em vez de dois
    POST seguidos: o teste roda em milissegundos, os dois POST cairiam no
    MESMO segundo e a primeira vigencia teria duracao zero - o caso do teste
    seguinte, nao deste. Datas fixas tambem evitam depender do relogio.
    """
    from datetime import datetime

    from app.template_assignments import resolve_assignment_at

    modelo_a, modelo_b, aluno = modelos
    troca = datetime(2026, 9, 14, 15, 46, 2)
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_template_assignments "
            "(template_id, user_id, assigned_by_user_id, assigned_at, unassigned_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (modelo_a, aluno, aluno, datetime(2026, 8, 27, 18, 58, 3), troca),
        )
        cur.execute(
            "INSERT INTO workout_plan_template_assignments "
            "(template_id, user_id, assigned_by_user_id, assigned_at) VALUES (%s, %s, %s, %s)",
            (modelo_b, aluno, aluno, troca),
        )
    db_conn.commit()

    assert resolve_assignment_at(aluno, datetime(2026, 9, 3, 10, 0, 0)).template_id == modelo_a
    # O instante da troca pertence ao Modelo que ENTROU: o intervalo e
    # semiaberto `[assigned_at, unassigned_at)`, senao ele seria de dois.
    assert resolve_assignment_at(aluno, troca).template_id == modelo_b
    assert resolve_assignment_at(aluno, datetime(2026, 9, 24, 10, 0, 0)).template_id == modelo_b

    # Uma DATA (e nao um instante) resolve pelo FIM do dia: o dia 14 inteiro
    # pertence ao Modelo que entrou as 15h46 - e a mesma regra de `_as_moment`
    # que o dashboard usa ao resolver por `window.date_to`.
    from datetime import date

    assert resolve_assignment_at(aluno, date(2026, 9, 14)).template_id == modelo_b
    assert resolve_assignment_at(aluno, date(2026, 9, 13)).template_id == modelo_a


def test_duas_atribuicoes_no_mesmo_segundo_a_segunda_vence(api, db_conn, modelos):
    """Corrigir a escolha logo depois de erra-la: a primeira vigencia nasce e
    morre no mesmo instante e nunca foi valida para nenhum momento. Resolver
    por data tem de devolver a SEGUNDA - a que de fato valeu."""
    from app.template_assignments import resolve_assignment_at

    modelo_a, modelo_b, aluno = modelos
    api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": aluno})
    api.post(f"/workout-plan-templates/{modelo_b}/assignments", json={"user_id": aluno})

    linhas = _linhas(db_conn, aluno)
    if linhas[0]["unassigned_at"] != linhas[0]["assigned_at"]:
        pytest.skip("os dois POST cairam em segundos diferentes; o caso deste teste nao ocorreu")
    assert resolve_assignment_at(aluno, linhas[0]["assigned_at"]).template_id == modelo_b


def test_antes_da_primeira_atribuicao_nao_havia_modelo(api, db_conn, modelos):
    """Nao cair no vigente de hoje e o ponto: dizer "Modelo B" para um periodo
    em que o usuario nao tinha Modelo nenhum era exatamente o defeito."""
    from datetime import timedelta

    from app.template_assignments import resolve_assignment_at

    modelo_a, _modelo_b, aluno = modelos
    api.post(f"/workout-plan-templates/{modelo_a}/assignments", json={"user_id": aluno})
    inicio = _linhas(db_conn, aluno)[0]["assigned_at"]

    assert resolve_assignment_at(aluno, inicio - timedelta(days=1)) is None
