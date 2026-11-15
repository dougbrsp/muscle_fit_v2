"""Modelos de treino por dono/visibilidade (RBAC lote 6A) contra MySQL 8 real, nos DOIS
backends (PyMySQL de dev e shim da RDS Data API de producao).

Cobre o que o cursor falso dos unitarios nao prova:

- `workout_plan_templates.visibility` existe com default `publico` (migracao 116);
- a listagem de um personal trainer traz os publicos e os proprios, e nao o privado alheio;
- atribuicao a aluno com vinculo ativo passa; sem vinculo e recusada (403).

Dados com prefixo `itg_mdono_`; limpeza restrita a eles, antes e depois.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import workout_plan_templates as wpt
from app.security import Identity, get_current_identity

PREFIXO = "itg_mdono_"
USUARIOS = {
    "pt": (f"{PREFIXO}pt", "personal_trainer"),
    "pt2": (f"{PREFIXO}pt2", "personal_trainer"),
    "aluno": (f"{PREFIXO}aluno", "regular"),
    "estranho": (f"{PREFIXO}estranho", "regular"),
}
NOMES = tuple(nome for nome, _ in USUARIOS.values())


def _marcas(n: int) -> str:
    return ", ".join(["%s"] * n)


def _limpar(raw) -> None:
    with raw.cursor() as cur:
        cur.execute(
            f"SELECT id FROM users WHERE username IN ({_marcas(len(NOMES))})", NOMES
        )
        ids = [r["id"] for r in cur.fetchall()]
        cur.execute(
            "SELECT id FROM workout_plan_templates WHERE name LIKE %s", (f"{PREFIXO}%",)
        )
        tids = [r["id"] for r in cur.fetchall()]
        if tids:
            m = _marcas(len(tids))
            cur.execute(
                f"DELETE FROM workout_plan_template_assignments WHERE template_id IN ({m})",
                tuple(tids),
            )
            cur.execute(
                f"DELETE FROM workout_plan_templates WHERE id IN ({m})", tuple(tids)
            )
        if ids:
            m = _marcas(len(ids))
            cur.execute(
                f"DELETE FROM workout_plan_template_assignments WHERE user_id IN ({m})",
                tuple(ids),
            )
            cur.execute(
                f"DELETE FROM access_audit_log WHERE actor_user_id IN ({m})", tuple(ids)
            )
            cur.execute(
                f"DELETE FROM trainer_students WHERE trainer_user_id IN ({m}) OR student_user_id IN ({m})",
                tuple(ids) + tuple(ids),
            )
            cur.execute(
                f"DELETE FROM user_access_groups WHERE user_id IN ({m})", tuple(ids)
            )
            cur.execute(f"DELETE FROM users WHERE id IN ({m})", tuple(ids))
    raw.commit()


@pytest.fixture
def dados(raw, conceder_grupo):
    _limpar(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        for chave, (nome, papel) in USUARIOS.items():
            cur.execute(
                "INSERT INTO users (username, full_name, password_hash, role) VALUES (%s, %s, 'x', %s)",
                (nome, nome.upper(), papel),
            )
            ids[chave] = cur.lastrowid
            conceder_grupo(cur, ids[chave], papel)
        cur.execute(
            "INSERT INTO trainer_students (trainer_user_id, student_user_id, created_by_user_id) "
            "VALUES (%s, %s, %s)",
            (ids["pt"], ids["aluno"], ids["pt"]),
        )
        modelos: dict[str, int] = {}
        for chave, nome, dono, vis in (
            (
                "pub_pt2",
                "pub_pt2",
                "pt2",
                None,
            ),  # sem visibility: usa o default da coluna
            ("priv_pt2", "priv_pt2", "pt2", "privado"),
            ("priv_pt", "priv_pt", "pt", "privado"),
        ):
            if vis is None:
                cur.execute(
                    "INSERT INTO workout_plan_templates (name, is_active, created_by_user_id) VALUES (%s, 1, %s)",
                    (PREFIXO + nome, ids[dono]),
                )
            else:
                cur.execute(
                    "INSERT INTO workout_plan_templates (name, is_active, created_by_user_id, visibility) "
                    "VALUES (%s, 1, %s, %s)",
                    (PREFIXO + nome, ids[dono], vis),
                )
            modelos[chave] = cur.lastrowid
    raw.commit()
    ids_all = {"usuarios": ids, "modelos": modelos}
    yield ids_all
    _limpar(raw)


@pytest.fixture
def client(db_backend, dados, fake_rds) -> TestClient:
    app = FastAPI()
    app.include_router(wpt.router)
    pt = Identity(
        user_id=dados["usuarios"]["pt"], role="personal_trainer", source="session"
    )
    app.dependency_overrides[get_current_identity] = lambda: pt
    yield TestClient(app, raise_server_exceptions=False)
    if db_backend == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


def test_coluna_visibility_tem_default_publico(client, raw, dados):
    with raw.cursor() as cur:
        cur.execute(
            "SELECT visibility FROM workout_plan_templates WHERE id = %s",
            (dados["modelos"]["pub_pt2"],),
        )
        assert cur.fetchone()["visibility"] == "publico"


def test_listagem_do_pt_ve_publicos_e_proprios_e_nao_o_privado_alheio(client, dados):
    resp = client.get("/workout-plan-templates")
    assert resp.status_code == 200, resp.text
    nossos = {i["name"] for i in resp.json()["items"] if i["name"].startswith(PREFIXO)}
    assert PREFIXO + "pub_pt2" in nossos
    assert PREFIXO + "priv_pt" in nossos
    assert PREFIXO + "priv_pt2" not in nossos


def test_atribuicao_a_aluno_com_vinculo_passa_e_sem_vinculo_e_recusada(
    client, raw, dados
):
    modelo = dados["modelos"]["pub_pt2"]
    sem_vinculo = client.post(
        f"/workout-plan-templates/{modelo}/assignments",
        json={"user_id": dados["usuarios"]["estranho"]},
    )
    assert sem_vinculo.status_code == 403, sem_vinculo.text
    com_vinculo = client.post(
        f"/workout-plan-templates/{modelo}/assignments",
        json={"user_id": dados["usuarios"]["aluno"]},
    )
    assert com_vinculo.status_code in (200, 201), com_vinculo.text
    with raw.cursor() as cur:
        cur.execute(
            "SELECT user_id FROM workout_plan_template_assignments WHERE template_id = %s",
            (modelo,),
        )
        assert [r["user_id"] for r in cur.fetchall()] == [dados["usuarios"]["aluno"]]
