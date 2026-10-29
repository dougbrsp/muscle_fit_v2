"""Coleta dos CASOS CONGELADOS dos evals de LLM (IA-11) - SOMENTE LEITURA.

Le o MySQL LOCAL do DEV (dados de producao do usuario 1 copiados por
`scripts/prod_to_dev.py`), monta a ENTRADA de cada feature de texto pelo
MESMO codigo que a producao usa (`activity_detail`, `exercise_summary_context`,
`build_package_for_question`, `build_training_evolution_payload`,
`CardioAgentContextService.build_context`, `periodization.build_context`,
linhas do catalogo) e grava `tests/eval/casos/<feature>.json`.

Os arquivos gerados sao a fonte das DUAS metades:

- snapshot do prompt renderizado (`tests/unit/test_llm_prompt_renderizado_snapshot.py`,
  roda no CI, sem chamada paga);
- eval pago manual (`tests/eval/test_llm_features_eval.py`, `-m llm_eval`).

Nao precisa rodar de novo para os testes: os casos ficam commitados. Rode so
para ACRESCENTAR/TROCAR casos - e entao regere os snapshots (ver
`docs/ia/EVALS.md`).

Repositorio publico (M103 L2, 2026-10-29): os casos COMMITADOS sao
SINTETICOS - mesma estrutura e cenarios, com medidas de saude/treino, datas,
notas livres e ids inventados. Esta coleta grava dado REAL do DEV: o
resultado dela nunca vai para o repositorio sem antes ser trocado por valores
sinteticos.

Seguranca:

- recusa rodar fora de `DB_BACKEND=pymysql` com `MYSQL_HOST` local - nunca
  aponta para Aurora/producao;
- toda conexao abre com `SET SESSION TRANSACTION READ ONLY`: um INSERT/UPDATE
  acidental de qualquer funcao chamada aqui falha no MySQL;
- anonimiza nome de academia, nome/usuario/e-mail do dono dos dados.

Uso (a partir de `api/`, credenciais do `.env` raiz no AMBIENTE, nunca no repo):

    DB_BACKEND=pymysql MYSQL_HOST=127.0.0.1 MYSQL_USER=... MYSQL_PASSWORD=... \\
        MYSQL_DATABASE=... python -m tests.eval.coletar_casos_llm --user-id 1

O RAG NAO e congelado aqui: ele e conteudo versionado em `api/privado/rag/markdown` (submodulo privado) e o
eval o carrega na hora (o snapshot usa um marcador fixo no lugar dele).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any

CASOS_DIR = Path(__file__).with_name("casos")
DATA_COLETA = "2026-10-08"
HORA_COLETA = "07:30"


def _guarda_ambiente() -> None:
    if os.environ.get("DB_BACKEND") != "pymysql":
        sys.exit("Recusado: DB_BACKEND precisa ser 'pymysql' (MySQL local do DEV).")
    if os.environ.get("MYSQL_HOST", "127.0.0.1") not in ("127.0.0.1", "localhost"):
        sys.exit("Recusado: MYSQL_HOST precisa ser local (127.0.0.1/localhost).")


def _somente_leitura() -> None:
    """Toda conexao pymysql nasce READ ONLY."""
    import pymysql

    original = pymysql.connect

    def _connect(*args, **kwargs):
        conn = original(*args, **kwargs)
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
        return conn

    pymysql.connect = _connect


# ---------------------------------------------------------------------------
# Especificacao dos casos (ids reais do DEV, usuario 1) + criterios do eval
# ---------------------------------------------------------------------------

PROIBE_REDUZIR = ["DECREASE_LOAD"]  # set-eval-v4: categorias = acoes do motor
# set-eval-v6 (2026-10-12): a direcao da carga da proxima serie e do MOTOR
# (`domain/next_set_load.py`); o eval confere categoria e carga contra a
# sugestao do motor calculada do proprio caso (`DIRECAO_DIFERENTE_DO_MOTOR`).
# `categorias_aceitas` repete a direcao esperada so para leitura do caso.
MANTER = ["HOLD"]
AUMENTAR = ["INCREASE_LOAD"]
REDUZIR = ["DECREASE_LOAD"]

SET_EVALUATION = [
    {
        "id": "progresso_reps_acima_da_faixa",
        "descricao": "Remada baixa no cabo, decisao INCREASE_LOAD; reps acima da faixa 10-12 com a mesma carga (ate 15 reps em RPE 10: manter).",
        "exercise_id": 622, "set_number": 2,
        "expect": {"categorias_aceitas": MANTER},
    },
    {
        "id": "queda_de_reps_rpe_10",
        "descricao": "Supino inclinado na maquina, reps caindo ate 6, RPE 10 em todas, ultima serie (6 reps em RPE 10: manter).",
        "exercise_id": 665, "set_number": 3,
        "expect": {"categorias_aceitas": MANTER},
    },
    {
        "id": "plato_hack_squat",
        "descricao": "Hack squat com queda de reps na mesma carga da sessao anterior, ultima serie em RPE 10.",
        "exercise_id": 657, "set_number": 2,
        "expect": {"categorias_aceitas": MANTER},
    },
    {
        "id": "rpe_10_primeira_serie",
        "descricao": "Desenvolvimento na maquina, serie 1 de 4 ja em RPE 10 dentro da faixa (manter; o alvo de carga do motor e da sessao, nao da proxima serie).",
        "exercise_id": 666, "set_number": 1,
        "expect": {"categorias_aceitas": MANTER},
    },
    {
        "id": "apos_tecnica_avancada",
        "descricao": "Supino declinado na maquina: queda forte de reps, rest-pause na serie 6; esta e a 2a aplicacao (sem RPE; fora da regra da proxima serie).",
        "exercise_id": 574, "set_number": 7,
        "expect": {"categorias_proibidas": PROIBE_REDUZIR},
    },
    {
        "id": "dor_muscular_alta_com_nota",
        "descricao": "Rosca inversa com barra reta: poucas reps, dor muscular 9/10 e nota do usuario sobre a dor.",
        "exercise_id": 313, "set_number": 3,
        "expect": {"categorias_aceitas": REDUZIR, "carga_sugerida_ate_a_executada": True},
    },
    {
        "id": "dor_articular",
        "descricao": "Triceps na polia alta com barra V, ultima serie; dor articular registrada no exercicio.",
        "exercise_id": 164, "set_number": 5, "joint_pain_do_exercicio": True,
        "expect": {"categorias_aceitas": REDUZIR, "carga_sugerida_ate_a_executada": True},
    },
    {
        "id": "assimetria_unilateral_nota",
        "descricao": "Flexao de joelho em pe: nota de assimetria direita/esquerda; carga ja reduzida um passo na serie.",
        "exercise_id": 609, "set_number": 3,
        "expect": {"categorias_aceitas": MANTER},
    },
    {
        "id": "reps_acima_do_limite_rpe_10",
        "descricao": "Remada baixa na maquina, serie 1: mais de 15 reps em RPE 10 (aumentar um passo).",
        "exercise_id": 673, "set_number": 1,
        "expect": {"categorias_aceitas": AUMENTAR},
    },
    {
        "id": "falha_antes_de_6_reps",
        "descricao": "Leg press 45, serie 3: RPE 10 com menos de 6 reps (falha antes de 6 reps: reduzir um passo).",
        "exercise_id": 631, "set_number": 3,
        "expect": {"categorias_aceitas": REDUZIR},
    },
]

EXERCISE_SUMMARY = [
    {"id": "progresso_increase_load", "exercise_id": 622, "descricao": "Remada baixa no cabo com decisao INCREASE_LOAD e reps acima da faixa."},
    {"id": "queda_de_reps", "exercise_id": 665, "descricao": "Supino inclinado na maquina: reps caindo serie a serie em RPE 10."},
    {"id": "plato", "exercise_id": 657, "descricao": "Hack squat repetindo carga e reps da sessao anterior."},
    {"id": "tecnica_avancada", "exercise_id": 574, "descricao": "Supino declinado com queda forte de reps e duas aplicacoes de rest-pause."},
    {"id": "dor_muscular_alta", "exercise_id": 313, "descricao": "Rosca inversa com dor muscular 7 e 9/10 e notas.", "expect": {"menciona_dor": True}},
    {"id": "assimetria_com_notas", "exercise_id": 609, "descricao": "Flexao de joelho em pe com notas de assimetria."},
]

WORKOUT_SUMMARY = [
    {"id": "peito_ombro_triceps_queda", "activity_id": 239, "descricao": "Treino F com queda de reps no supino inclinado e 8 exercicios."},
    {"id": "posterior_gluteos", "activity_id": 238, "descricao": "Treino E de posterior/gluteos, decisoes SET_REP_RANGE_10_12."},
    {"id": "costas_biceps_increase", "activity_id": 234, "descricao": "Treino A com decisao INCREASE_LOAD e rest-pause."},
    {"id": "quadriceps_tecnicas", "activity_id": 228, "descricao": "Treino B com rest-pause e drop-set."},
    {"id": "peito_decrease_load", "activity_id": 226, "descricao": "Treino F com DECREASE_LOAD no supino reto."},
    {"id": "sessao_longa_outra_academia", "activity_id": 220, "descricao": "Sessao longa em outra academia, sem decisoes do motor."},
]

INSIGHTS = [
    {"id": "volume_peito", "question": "Meu peito está com volume suficiente?", "window": "28d"},
    {"id": "estagnacao", "question": "Algum exercício meu parou de evoluir?", "window": "28d"},
    {"id": "aderencia_modelo", "question": "Estou cumprindo o que o Modelo pede?", "window": "28d"},
    {"id": "recuperacao_7d", "question": "Posso aumentar a intensidade esta semana?", "window": "7d"},
    {"id": "dado_ausente_cintura", "question": "Minha cintura diminuiu no período?", "window": "90d", "expect": {"admite_dado_ausente": True}},
]

WORKOUTS_ANALYSIS = [
    {"id": "dias_7", "days": 7},
    {"id": "dias_30", "days": 30},
    {"id": "dias_60", "days": 60},
    {"id": "dias_90", "days": 90},
    {"id": "dias_180", "days": 180},
]

CARDIO_AGENT = [
    {"id": "avulso_14d", "days": 14, "descricao": "Tela /agente-cardio, sem plano (reference_date = hoje, como o dispatcher)."},
    {"id": "avulso_7d", "days": 7, "descricao": "Janela curta de 7 dias."},
    {"id": "avulso_30d", "days": 30, "descricao": "Janela de 30 dias (teto do contexto de cardio do relatorio)."},
    {"id": "com_plano_planejado", "days": 14, "workout_plan_id": 134, "reference_time": "15:01",
     "descricao": "Plano planejado de hoje (proposed_workout no formato do frontend) + reference_date/time."},
    {"id": "data_sem_plano", "days": 14, "reference_date": "2026-10-10", "reference_time": "06:00",
     "descricao": "Data/hora futura sem ficha (pergunta generica para aquela data)."},
]

PERIODIZATION = [
    {"id": "modo_b_6_dias_abc", "request": {"volume_mode": "equipment_series", "days_per_cycle": 6, "training_type_id": 2, "gym_id": 1, "rag_sources": ["2. ABC"]}},
    {"id": "modo_b_4_dias_instrucoes", "request": {"volume_mode": "equipment_series", "days_per_cycle": 4, "training_type_id": 5, "gym_id": 1, "rag_sources": [],
                                                    "instructions": "Prefiro máquinas a pesos livres; desconforto no ombro, evitar desenvolvimento com barra."}},
    {"id": "modo_b_5_dias_sem_perfil", "request": {"volume_mode": "equipment_series", "days_per_cycle": 5, "training_type_id": None, "gym_id": 47, "rag_sources": []}},
    {"id": "modo_a_5_dias", "request": {"volume_mode": "percent_budget", "days_per_cycle": 5, "training_type_id": 2, "gym_id": None, "rag_sources": ["2. ABC"]}},
    {"id": "modo_a_3_dias_sem_perfil", "request": {"volume_mode": "percent_budget", "days_per_cycle": 3, "training_type_id": None, "gym_id": None, "rag_sources": [],
                                                    "instructions": "Só treino segunda, quarta e sexta."}},
]

EXERCISE_DESCRIPTION = [
    {"id": "hack_squat", "exercise_id": 60},
    {"id": "supino_reto_barra", "exercise_id": 53},
    {"id": "rosca_scott_maquina", "exercise_id": 62},
    {"id": "elevacao_pelvica_articulada", "exercise_id": 125},
    {"id": "remada_serrote", "exercise_id": 82},
    {"id": "abdominal_crunch_maquina", "exercise_id": 27},
]


# ---------------------------------------------------------------------------
# Serializacao / anonimizacao
# ---------------------------------------------------------------------------


def _str_default(value: Any) -> Any:
    """`default=str`: o mesmo que `report_ai._dump`/`cardio_agent_ai` usam no
    prompt - congelar assim mantem o JSON renderizado identico."""
    return str(value)


def congelar(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    return json.loads(json.dumps(value, default=_str_default, ensure_ascii=False))


def _anonimizar(value: Any, trocas: dict[str, str]) -> Any:
    if isinstance(value, str):
        for original, novo in trocas.items():
            if original:
                value = value.replace(original, novo)
        return value
    if isinstance(value, list):
        return [_anonimizar(v, trocas) for v in value]
    if isinstance(value, dict):
        # ids opacos de integracao (Zepp/Intervals): nao entram em prompt
        # nenhum, mas identificam a sessao no aparelho - ficam de fora.
        return {
            k: ("anonimizado" if k.endswith("external_id") and v else _anonimizar(v, trocas))
            for k, v in value.items()
        }
    return value


def _trocas(conn, user_id: int) -> dict[str, str]:
    trocas: dict[str, str] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT username, full_name, email FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone() or {}
        for campo, novo in (("email", "usuario@exemplo.com"), ("full_name", "Usuário Exemplo"), ("username", "usuario")):
            if row.get(campo) and len(str(row[campo])) >= 4:
                trocas[str(row[campo])] = novo
        cur.execute(
            "SELECT DISTINCT g.id, g.name FROM gyms g WHERE g.id IN (SELECT gym_id FROM activities WHERE user_id = %s) "
            "OR g.id IN (SELECT gym_id FROM workout_plans WHERE user_id = %s) ORDER BY g.id",
            (user_id, user_id),
        )
        for indice, gym in enumerate(cur.fetchall() or []):
            if gym.get("name"):
                novo = f"Academia {chr(ord('A') + indice)}"
                trocas[str(gym["name"])] = novo
                # o 1o nome sozinho tambem identifica ("Full Body <nome> A")
                primeiro = str(gym["name"]).split()[0]
                if len(primeiro) >= 5 and primeiro.lower() not in ("academia", "studio", "smart"):
                    trocas.setdefault(primeiro, novo)
    # nomes longos primeiro: "Academia X Centro" antes de "Academia X"
    return dict(sorted(trocas.items(), key=lambda item: -len(item[0])))


# ---------------------------------------------------------------------------
# Coletores (mesmo codigo da producao)
# ---------------------------------------------------------------------------


def _sets_do_exercicio(conn, exercise_id: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_number, set_type, reps, weight_kg, rpe, muscle_pain, joint_pain, notes, rpe_target_effective "
            "FROM exercise_sets WHERE exercise_id = %s ORDER BY set_number",
            (exercise_id,),
        )
        return list(cur.fetchall() or [])


def _num(value: Any) -> Any:
    if value is None:
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def coletar_set_evaluation(conn, user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    """Payload no formato de `SetEvaluationIn` (o que o Modo Treino envia) +
    o contexto do motor e o teto que `dispatch_evaluate_set` resolve no
    servidor. A decisao vem do snapshot congelado da ficha do exercicio
    (`load_logged_exercise_decision`): no DEV nao ha sessao em andamento."""
    from app.domain import effective_intensity
    from app.domain.effective_intensity import equipment_id_of_gym_equipment
    from app.domain.prescription import UNKNOWN_PAIR_SAFETY
    from app.engine_decision_context import (
        equipment_step_kg,
        load_logged_exercise_decision,
        read_pair_load_convention,
    )
    from app.progression_state import load_equipment_steps

    with conn.cursor() as cur:
        cur.execute(
            "SELECT e.id, e.original_name, e.mapping_id, e.equipment_id, e.joint_pain, wpe.reps_target, "
            "wpe.load_target, wpe.rpe_target, wpe.sets_count AS plan_sets_count "
            "FROM exercises e JOIN activities a ON a.id = e.activity_id "
            "LEFT JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id "
            "WHERE e.id = %s AND a.user_id = %s",
            (spec["exercise_id"], user_id),
        )
        ex = cur.fetchone()
    sets = _sets_do_exercicio(conn, spec["exercise_id"])
    atual = next(s for s in sets if s["set_number"] == spec["set_number"])
    anteriores = [s for s in sets if s["set_number"] < spec["set_number"]]
    joint = bool(atual.get("joint_pain")) or (bool(ex.get("joint_pain")) and spec.get("joint_pain_do_exercicio", False))
    payload = {
        "exercise_name": ex["original_name"],
        "reps_target": ex.get("reps_target"),
        "load_target": ex.get("load_target"),
        "rpe_target": ex.get("rpe_target"),
        "reps": atual.get("reps"),
        "weight_kg": _num(atual.get("weight_kg")),
        "rpe": _num(atual.get("rpe")),
        "set_number": atual["set_number"],
        # Serie que nao e de trabalho leva o tipo (o Modo Treino envia; a
        # regra da proxima serie e o status do motor dependem dele).
        **({"set_type": atual["set_type"]} if atual.get("set_type") not in (None, "working") else {}),
        "sets_count": max(int(ex.get("plan_sets_count") or 0), len(sets)),
        "previous_sets_this_session": [
            {
                "set_number": s["set_number"],
                "reps": s.get("reps"),
                "weight_kg": _num(s.get("weight_kg")),
                "rpe": _num(s.get("rpe")),
                "set_type": s.get("set_type"),
            }
            for s in anteriores
        ],
        "muscle_pain": atual.get("muscle_pain"),
        "joint_pain": joint if (joint or atual.get("joint_pain") is not None) else None,
        "notes": atual.get("notes"),
        "mapping_id": ex.get("mapping_id"),
        "equipment_id": ex.get("equipment_id"),
        "effective_rpe_ceiling": atual.get("rpe_target_effective"),
    }
    decision = load_logged_exercise_decision(conn, user_id, spec["exercise_id"])
    equipment_id = equipment_id_of_gym_equipment(conn, ex.get("equipment_id")) if ex.get("equipment_id") else None
    raw_step = load_equipment_steps(conn, [equipment_id]).get(equipment_id) if equipment_id is not None else None
    safety = (
        effective_intensity.resolve_pair_safety(conn, ex.get("mapping_id"), ex.get("equipment_id"))
        if ex.get("mapping_id") and ex.get("equipment_id")
        else None
    )
    intensity = effective_intensity.restrict_ceiling(
        effective_intensity.from_safety(safety or UNKNOWN_PAIR_SAFETY), payload["effective_rpe_ceiling"]
    )
    composition = effective_intensity.composition_of(intensity)
    return {
        "payload": congelar(payload),
        "engine": {
            "step_kg": str(equipment_step_kg(raw_step)),
            "decision": congelar(decision) if decision else None,
            # set-eval-v6: convencao de carga do par (a regra da proxima serie
            # nao vale para `assisted`/`bodyweight`).
            "load_convention": read_pair_load_convention(conn, ex.get("mapping_id"), ex.get("equipment_id")),
        },
        "composition": {"ceiling": composition.ceiling, "failure_allowed": composition.failure_allowed, "target": composition.target},
    }


def coletar_exercise_summary(conn, user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    """Exatamente o `ai_payload` de `dispatch_exercise_summary`."""
    from app.advanced_techniques import technique_display_names
    from app.engine_decision_context import load_logged_exercise_decision
    from app.services import exercise_summary_context

    context = exercise_summary_context(spec["exercise_id"], user_id=user_id)
    decision = load_logged_exercise_decision(conn, user_id, spec["exercise_id"])
    return {
        "payload": congelar(
            {
                "engine_decision": dataclasses.asdict(decision) if decision else None,
                "exercise_name": context["original_name"],
                "reps_target": context.get("reps_target"),
                "load_target": context.get("load_target"),
                "rpe_target": context.get("rpe_target"),
                "sets": context.get("sets") or [],
                "muscle_pain": context.get("muscle_pain"),
                "joint_pain": context.get("joint_pain"),
                "notes": context.get("notes"),
                # M95b: nomes do catalogo, como `dispatch_exercise_summary`.
                "technique_names": technique_display_names(),
            }
        )
    }


def coletar_workout_summary(conn, user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    from app.advanced_techniques import list_techniques
    from app.engine_decision_context import load_activity_decisions
    from app.services import activity_detail
    from app.set_labels import technique_names

    detail = activity_detail(spec["activity_id"], user_id=user_id)
    decisions = load_activity_decisions(conn, user_id, spec["activity_id"])
    return {
        "activity": congelar(detail),
        "technique_names": technique_names(list_techniques(include_inactive=True)),
        "engine_decisions": {
            "exercises": {str(k): dataclasses.asdict(v) for k, v in decisions["exercises"].items()},
            "groups": [dataclasses.asdict(g) for g in decisions["groups"]],
        },
    }


def coletar_insights(conn, user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    """Pacote de `build_package_for_question`, congelado com o MESMO
    `_json_default` do prompt (datas em ISO). `window` e `data_quality` vao
    marcados para serem revividos como `WindowInfo`/`DataQuality` - o prompt
    os formata com f-string e com `str()`, e o snapshot precisa ver o mesmo
    texto da producao."""
    from app.insights_core import build_package_for_question
    from app.insights_prompt import _json_default

    package, groups = build_package_for_question(user_id=user_id, question=spec["question"], window=spec["window"])
    window = package["window"]
    frozen = json.loads(json.dumps({k: v for k, v in package.items() if k != "window"}, default=_json_default, ensure_ascii=False))
    frozen["window"] = window.model_dump(mode="json")
    return {"question": spec["question"], "package": frozen, "rag_groups": list(groups)}


def coletar_workouts_analysis(conn, user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    from app.intelligence.dashboard_aggregator_service import DashboardAggregatorService

    training = DashboardAggregatorService().build_training_evolution_payload(days=spec["days"], user_id=user_id)
    return {"days": spec["days"], "training": congelar(training)}


def coletar_cardio_agent(conn, user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    """Contexto de `CardioAgentContextService.build_context` + os parametros do
    dispatcher (`reference_date` = hoje quando ausente). `proposed_workout` no
    formato do frontend (`PlanDetailClient.tsx`: "<nome>: ex1, ex2, ...")."""
    from app.intelligence.cardio_context_service import CardioAgentContextService

    context = CardioAgentContextService().build_context(days=spec["days"], user_id=user_id)
    proposed = None
    reference_date = spec.get("reference_date") or DATA_COLETA
    if spec.get("workout_plan_id"):
        with conn.cursor() as cur:
            cur.execute("SELECT name, plan_date FROM workout_plans WHERE id = %s AND user_id = %s", (spec["workout_plan_id"], user_id))
            plan = cur.fetchone()
            cur.execute("SELECT exercise_name FROM workout_plan_exercises WHERE plan_id = %s ORDER BY sequence", (spec["workout_plan_id"],))
            nomes = [row["exercise_name"] for row in cur.fetchall() or []]
        proposed = f"{plan['name']}: {', '.join(nomes)}"
        reference_date = str(plan["plan_date"])
    return {
        "days": spec["days"],
        "context": congelar(context),
        "proposed_workout": proposed,
        "reference_date": reference_date,
        "reference_time": spec.get("reference_time"),
        "today": DATA_COLETA,
        "now_time": HORA_COLETA,
    }


def coletar_periodization(conn, user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    """`periodization.context.build_context` sem o `rag_text` (o RAG e
    carregado na hora pelo eval; o snapshot usa o marcador)."""
    from app.models import PeriodizationDraftIn
    from app.periodization import context as periodization_context

    request = PeriodizationDraftIn(**spec["request"])
    original = periodization_context.load_rag_text
    periodization_context.load_rag_text = lambda *_a, **_k: ""
    try:
        context = periodization_context.build_context(conn, request, user_id)
    finally:
        periodization_context.load_rag_text = original
    context.pop("rag_text", None)
    target_groups = [entry["muscle_group_name"] for entry in context["targets"]] or [g["name"] for g in context["muscle_groups"]]
    return {"request": spec["request"], "context": congelar(context), "rag_target_groups": target_groups}


def coletar_exercise_description(conn, user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    """Mesmas leituras de `exercise_description_service.generate_and_store`."""
    from app.exercise_description_service import _equipment_names, _exercise_row, _muscle_rows

    with conn.cursor() as cur:
        row = dict(_exercise_row(cur, spec["exercise_id"]))
        row["equipment_names"] = _equipment_names(cur, spec["exercise_id"])
        muscles = _muscle_rows(cur, spec["exercise_id"])
    for campo in ("execution_description", "execution_description_prompt_version", "execution_description_reviewed_by"):
        row.pop(campo, None)
    return {"exercise": congelar(row), "muscles": congelar(muscles)}


FEATURES = {
    "set_evaluation": (SET_EVALUATION, coletar_set_evaluation),
    "exercise_summary": (EXERCISE_SUMMARY, coletar_exercise_summary),
    "workout_summary": (WORKOUT_SUMMARY, coletar_workout_summary),
    "insights": (INSIGHTS, coletar_insights),
    "workouts_analysis": (WORKOUTS_ANALYSIS, coletar_workouts_analysis),
    "cardio_agent": (CARDIO_AGENT, coletar_cardio_agent),
    "periodization": (PERIODIZATION, coletar_periodization),
    "exercise_description": (EXERCISE_DESCRIPTION, coletar_exercise_description),
}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--feature", action="append", choices=sorted(FEATURES), help="so estas (default: todas)")
    args = parser.parse_args(argv)

    _guarda_ambiente()
    _somente_leitura()
    from app.db import transaction

    CASOS_DIR.mkdir(exist_ok=True)
    with transaction() as conn:
        trocas = _trocas(conn, args.user_id)
        for feature in args.feature or sorted(FEATURES):
            specs, coletor = FEATURES[feature]
            casos = []
            for spec in specs:
                entrada = _anonimizar(coletor(conn, args.user_id, spec), trocas)
                casos.append(
                    {
                        "id": spec["id"],
                        "descricao": spec.get("descricao", ""),
                        "input": entrada,
                        "expect": spec.get("expect", {}),
                    }
                )
            destino = CASOS_DIR / f"{feature}.json"
            casos, compartilhado = deduplicar(casos)
            destino.write_text(
                serializar(
                    {
                        "feature": feature,
                        "fonte": f"DEV (copia de producao), usuario {args.user_id}, anonimizado; coletado em {DATA_COLETA}",
                        "compartilhado": compartilhado,
                        "cases": casos,
                    }
                ),
                encoding="utf-8",
                newline="\n",
            )
            print(f"{feature}: {len(casos)} casos -> {destino} ({destino.stat().st_size} bytes)")
    return 0


# Blocos grandes repetidos entre casos da MESMA feature (ex.: a recuperacao
# muscular do cardio e igual nos 5 contextos coletados no mesmo dia) vao uma
# vez so para `compartilhado` e o caso guarda `{"$compartilhado": <chave>}`.
# `tests/support/llm_casos.py` resolve a referencia ao carregar.
MIN_BYTES_COMPARTILHADO = 10_000


def deduplicar(casos: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import hashlib

    vistos: dict[str, int] = {}

    def _contar(value: Any) -> None:
        if isinstance(value, (dict, list)):
            texto = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if len(texto) >= MIN_BYTES_COMPARTILHADO:
                chave = hashlib.sha256(texto.encode("utf-8")).hexdigest()[:12]
                vistos[chave] = vistos.get(chave, 0) + 1
            for filho in value.values() if isinstance(value, dict) else value:
                _contar(filho)

    for caso in casos:
        _contar(caso["input"])

    compartilhado: dict[str, Any] = {}

    def _trocar(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            texto = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if len(texto) >= MIN_BYTES_COMPARTILHADO:
                chave = hashlib.sha256(texto.encode("utf-8")).hexdigest()[:12]
                if vistos.get(chave, 0) > 1:
                    compartilhado[chave] = value
                    return {"$compartilhado": chave}
            if isinstance(value, dict):
                return {k: _trocar(v) for k, v in value.items()}
            return [_trocar(v) for v in value]
        return value

    return [{**caso, "input": _trocar(caso["input"])} for caso in casos], compartilhado


def serializar(documento: dict[str, Any]) -> str:
    """JSON compacto, um caso por linha (o arquivo e grande; indentacao so
    multiplicaria o tamanho sem ajudar a ler)."""
    compacto = {"ensure_ascii": False, "separators": (",", ":")}
    linhas = ["{"]
    linhas.append(f'"feature":{json.dumps(documento["feature"], **compacto)},')
    linhas.append(f'"fonte":{json.dumps(documento["fonte"], **compacto)},')
    linhas.append(f'"compartilhado":{json.dumps(documento["compartilhado"], **compacto)},')
    linhas.append('"cases":[')
    casos = [json.dumps(caso, **compacto) for caso in documento["cases"]]
    linhas.append(",\n".join(casos))
    linhas.append("]}")
    return "\n".join(linhas) + "\n"


if __name__ == "__main__":  # pragma: no cover - ferramenta manual
    raise SystemExit(main())
