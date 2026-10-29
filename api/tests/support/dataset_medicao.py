"""Datasets das MEDICOES de statements, em um lugar so - GRANDE e PEQUENO.

Por que existe (M30): a contagem de statements **nao depende do volume de
dados**. Um N+1 por grupo muscular emite duas consultas por grupo tanto sobre
3 grupos quanto sobre 12; a subconsulta correlacionada por serie reaparece
tanto sobre 500 series quanto sobre 16.000. O que muda com o volume e o
RELOGIO - e o relogio ja deixou de ser criterio de aprovacao no PR #221
(`tests/support/medicao.py`).

Consequencia pratica: as mesmas afirmacoes que hoje exigem o dataset de 3 anos
(400 sessoes / 4.000 exercicios / 16.000 series, dezenas de segundos de carga,
marcador `slow`, fora do caminho critico do CI) valem sobre um dataset de
poucos segundos que roda em TODO PR. As duas especificacoes abaixo tem a mesma
FORMA - mesmos 12 grupos, mesmo catalogo ponderado (PRIMARY/SECONDARY/
STABILIZER), mesma distribuicao de sessoes - e diferem so nas dimensoes:

    GRANDE   3 anos   400 sessoes  x 10 exercicios x 4 series = 16.000 series
    PEQUENO  120 dias  30 sessoes  x  6 exercicios x 3 series =    540 series

A forma e que da poder de deteccao: e o numero de GRUPOS na janela (12 nos
dois) e o numero de EXERCICIOS distintos (48 nos dois) que multiplicam a
contagem quando um N+1 volta.

`garantir()` semeia sob demanda e reaproveita: a suite inteira compartilha UM
schema de teste, e todo arquivo trunca o que le (`isolamento_v3.TABELAS_V3`),
entao um dataset semeado por um arquivo pode ser apagado pelo seguinte. Em vez
de escopo de sessao cego (que devolveria um dataset ja truncado), cada uso
confere um SENTINELA no banco - uma consulta barata - e ressemeia so quando
precisa. E isso que permite os dois datasets conviverem na MESMA execucao.
"""

from __future__ import annotations

import datetime as _dt
import random
from dataclasses import dataclass
from typing import Any

from tests.support.isolamento_v3 import truncar
from tests.support.template_versions import versao_v1

# Instante congelado das medicoes. Os dois datasets sao semeados RELATIVOS a
# ele, e os testes congelam o relogio no mesmo ponto (`congelar_relogio`), para
# `hours_since_trained`/`dose_last_72h` nao mudarem entre duas chamadas.
FROZEN_NOW = _dt.datetime(2026, 9, 22, 19, 0, 0)

MUSCLE_GROUPS: tuple[str, ...] = (
    "Peitoral",
    "Costas",
    "Quadriceps",
    "Posterior de coxa",
    "Ombros",
    "Biceps",
    "Triceps",
    "Gluteos",
    "Panturrilha",
    "Abdomen",
    "Antebraco",
    "Trapezio",
)


@dataclass(frozen=True)
class SpecDataset:
    """Dimensoes de um dataset de medicao. `nome` identifica o dataset
    semeado no schema (e a chave do cache e o `username` do atleta)."""

    nome: str
    span_days: int
    sessions: int
    exercises_per_session: int
    sets_per_exercise: int
    exercises_per_group: int = 4
    seed: int = 20260922
    groups: tuple[str, ...] = MUSCLE_GROUPS

    @property
    def username(self) -> str:
        return f"atleta-perf-{self.nome}"

    @property
    def total_sets(self) -> int:
        return self.sessions * self.exercises_per_session * self.sets_per_exercise


# 3 anos de treino, na ordem de grandeza do usuario real de producao com folga.
# E o dataset das medicoes `slow` (relatorio de tempo na descricao dos PRs).
GRANDE = SpecDataset(
    nome="grande",
    span_days=3 * 365,
    sessions=400,
    exercises_per_session=10,
    sets_per_exercise=4,
)

# ~2s de carga: mesma FORMA (12 grupos, 48 exercicios de catalogo), volume
# suficiente para haver sessoes nas duas ultimas semanas, na janela de 28d e
# fora dela. E o dataset das afirmacoes que rodam em TODO PR.
PEQUENO = SpecDataset(
    nome="pequeno",
    span_days=120,
    sessions=30,
    exercises_per_session=6,
    sets_per_exercise=3,
)


def _scalar(row) -> Any:
    return row[0] if isinstance(row, (tuple, list)) else next(iter(row.values()))


def semear(conn, spec: SpecDataset, *, agora: _dt.datetime = FROZEN_NOW) -> dict[str, Any]:
    """Recria o dataset `spec` do zero na conexao NEUTRA `conn`.

    Comeca por `truncar`: este dataset alimenta varios arquivos de medicao, e
    qualquer tabela lida pelos endpoints e nao limpa aqui vira dado de outro
    arquivo DENTRO das medicoes - a armadilha do PR #199.
    """
    rng = random.Random(spec.seed)
    truncar(conn)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'admin')",
            (spec.username,),
        )
        user_id = cur.lastrowid

        group_ids: dict[str, int] = {}
        for name in spec.groups:
            cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (name,))
            group_ids[name] = cur.lastrowid

        # Catalogo: cada exercicio com PRIMARY no proprio grupo e
        # SECONDARY/STABILIZER em grupos vizinhos (exercita o caminho
        # ponderado de `_resolve_contributions`, nao so o fallback legado).
        exercise_ids: list[int] = []
        primary_group_of: dict[int, str] = {}
        contributions: list[tuple] = []
        next_id = 5000
        for index, group in enumerate(spec.groups):
            for variant in range(spec.exercises_per_group):
                exercise_id = next_id
                next_id += 1
                name = f"{group} exercicio {variant + 1}"
                cur.execute(
                    "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                    (exercise_id, name, name),
                )
                cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (exercise_id, name))
                secondary = spec.groups[(index + 1) % len(spec.groups)]
                stabilizer = spec.groups[(index + 2) % len(spec.groups)]
                contributions.append((exercise_id, group_ids[group], "PRIMARY", 1.0, 1.0))
                contributions.append((exercise_id, group_ids[secondary], "SECONDARY", 0.5, 0.5))
                contributions.append((exercise_id, group_ids[stabilizer], "STABILIZER", 0.25, 0.2))
                exercise_ids.append(exercise_id)
                primary_group_of[exercise_id] = group
        cur.executemany(
            "INSERT INTO exercise_muscle_contributions "
            "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) VALUES (%s,%s,%s,%s,%s)",
            contributions,
        )

        # Sessoes distribuidas no periodo, a mais recente ontem (para haver
        # dose/recuperacao "quente" a calcular).
        step = spec.span_days / spec.sessions
        activities = []
        for i in range(spec.sessions):
            days_ago = 1 + int((spec.sessions - 1 - i) * step)
            day = agora.date() - _dt.timedelta(days=days_ago)
            started = _dt.datetime.combine(day, _dt.time(hour=7 + (i % 3), minute=30))
            activities.append((user_id, day, started, f"Treino {i + 1}"))
        cur.executemany(
            "INSERT INTO activities (user_id, local_date, started_at, name, activity_type) "
            "VALUES (%s, %s, %s, %s, 'musculacao')",
            activities,
        )
        cur.execute("SELECT id FROM activities WHERE user_id = %s ORDER BY id ASC", (user_id,))
        activity_ids = [_scalar(row) for row in cur.fetchall()]

        exercise_rows = []
        for activity_id in activity_ids:
            for sequence, mapping_id in enumerate(
                rng.sample(exercise_ids, spec.exercises_per_session), start=1
            ):
                exercise_rows.append(
                    (
                        activity_id,
                        sequence,
                        primary_group_of[mapping_id],
                        mapping_id,
                        spec.sets_per_exercise,
                        rng.choice([None, 0, 1, 2]),
                        rng.choice([0, 0, 0, 1]),
                    )
                )
        cur.executemany(
            "INSERT INTO exercises "
            "(activity_id, sequence, original_name, mapping_id, sets_count, muscle_pain, joint_pain) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            exercise_rows,
        )
        cur.execute(
            "SELECT e.id FROM exercises e JOIN activities a ON a.id = e.activity_id "
            "WHERE a.user_id = %s ORDER BY e.id ASC",
            (user_id,),
        )
        db_exercise_ids = [_scalar(row) for row in cur.fetchall()]

        set_rows = []
        for db_exercise_id in db_exercise_ids:
            base_weight = rng.randrange(20, 120)
            for set_number in range(1, spec.sets_per_exercise + 1):
                set_rows.append(
                    (
                        db_exercise_id,
                        set_number,
                        "warmup" if set_number == 1 else "working",
                        rng.randrange(5, 15),
                        base_weight + rng.randrange(0, 10),
                        rng.choice([6.0, 7.0, 7.5, 8.0, 8.5, 9.0, 10.0]),
                    )
                )
        cur.executemany(
            "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            set_rows,
        )
    conn.commit()
    return {
        "spec": spec.nome,
        "user_id": user_id,
        "muscle_group_id": group_ids[spec.groups[0]],
        "activities": len(activity_ids),
        "exercises": len(exercise_rows),
        "sets": len(set_rows),
    }


_CACHE: dict[str, dict[str, Any]] = {}


def _sentinela_valida(conn, spec: SpecDataset, dados: dict[str, Any]) -> bool:
    """O dataset ainda esta no banco, inteiro? Uma consulta barata.

    Confere o atleta E a contagem de series: `truncar` de outro arquivo apaga
    as duas coisas, e `users` volta a AUTO_INCREMENT 1 - so conferir o id
    daria falso positivo."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM exercise_sets es "
            "JOIN exercises e ON e.id = es.exercise_id "
            "JOIN activities a ON a.id = e.activity_id "
            "JOIN users u ON u.id = a.user_id "
            "WHERE u.id = %s AND u.username = %s",
            (dados["user_id"], spec.username),
        )
        return int(_scalar(cur.fetchone() or [0])) == spec.total_sets


def garantir(conn, spec: SpecDataset, *, agora: _dt.datetime = FROZEN_NOW) -> dict[str, Any]:
    """Devolve o dataset `spec`, semeando-o se ainda nao estiver no banco.

    Duas medicoes seguidas do mesmo dataset pagam so a consulta do sentinela;
    alternar entre GRANDE e PEQUENO (ou rodar depois de um arquivo que trunca)
    ressemeia. E o que permite o dataset PEQUENO rodar no caminho critico sem
    quebrar as medicoes `slow` que usam o GRANDE na mesma execucao."""
    dados = _CACHE.get(spec.nome)
    if dados is not None and _sentinela_valida(conn, spec, dados):
        return dados
    dados = semear(conn, spec, agora=agora)
    _CACHE.clear()  # `semear` trunca: o outro dataset (se havia) morreu aqui.
    _CACHE[spec.nome] = dados
    return dados


# ---------------------------------------------------------------------------
# Modelo de Ficha `equipment_series` atribuido ao atleta do dataset
# ---------------------------------------------------------------------------

TEMPLATE_SLOTS = 4
TEMPLATE_ITEMS_PER_SLOT = 6
TEMPLATE_SETS_PER_ITEM = 4


def criar_modelo_por_equipamento(conn, *, user_id: int) -> int:
    """Modelo `equipment_series` atribuido a `user_id`, com itens apontando
    para o catalogo semeado. Devolve o `template_id` (use
    `remover_modelo` no teardown: o dataset sobrevive ao teste)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT exercise_id, MIN(muscle_group_id) AS muscle_group_id "
            "FROM exercise_muscle_contributions WHERE role = 'PRIMARY' "
            "GROUP BY exercise_id ORDER BY exercise_id ASC LIMIT %s",
            (TEMPLATE_SLOTS * TEMPLATE_ITEMS_PER_SLOT,),
        )
        catalogo = [(row["exercise_id"], row["muscle_group_id"]) for row in cur.fetchall()]
        cur.execute(
            "INSERT INTO workout_plan_templates (name, volume_mode, created_by_user_id) "
            "VALUES ('Equipamentos perf', 'equipment_series', %s)",
            (user_id,),
        )
        template_id = cur.lastrowid
        for sequence in range(1, TEMPLATE_SLOTS + 1):
            cur.execute(
                "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
                "VALUES (%s, %s, %s, %s, 'normal')",
                (versao_v1(cur, template_id), template_id, sequence, f"Slot {sequence}"),
            )
            slot_id = cur.lastrowid
            inicio = (sequence - 1) * TEMPLATE_ITEMS_PER_SLOT
            for ordem, (mapping_id, group_id) in enumerate(
                catalogo[inicio : inicio + TEMPLATE_ITEMS_PER_SLOT], 1
            ):
                cur.execute(
                    "INSERT INTO workout_plan_template_slot_items "
                    "(slot_id, item_order, item_kind, muscle_group_id, mapping_id, sets_count) "
                    "VALUES (%s, %s, 'equipment', %s, %s, %s)",
                    (slot_id, ordem, group_id, mapping_id, TEMPLATE_SETS_PER_ITEM),
                )
        cur.execute(
            "INSERT INTO workout_plan_template_assignments (template_id, user_id, assigned_by_user_id) "
            "VALUES (%s, %s, %s)",
            (template_id, user_id, user_id),
        )
    return template_id


def remover_modelo(conn, template_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM workout_plan_templates WHERE id = %s", (template_id,))


__all__ = [
    "FROZEN_NOW",
    "GRANDE",
    "MUSCLE_GROUPS",
    "PEQUENO",
    "SpecDataset",
    "criar_modelo_por_equipamento",
    "garantir",
    "remover_modelo",
    "semear",
]
