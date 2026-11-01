"""Contexto e prompt do Motor de Periodizacao (PLANO_20260912.md secao 7.1,
passo 5).

TODO o contexto e montado pelo BACKEND, a partir do banco e do RAG local - a
LLM nao busca nada, nao chama ferramenta nenhuma e nao ve id que o backend nao
tenha colocado no prompt. Isso e o que torna a validacao deterministica
possivel depois: todo id que a resposta pode citar saiu daqui.

Partes do contexto:
- RAG: `select_rag_text` (PROGRESSAO.md inteiro + chunks de HIPERTROFIA.md/
  FULLBODY.md/ANTEBRACO.md dos grupos alvo) + o conteudo de cada pasta escolhida
  de `rag/markdown/TIPOS_TREINO/*` (`load_rag_folder_text`, que ja valida o nome
  contra `list_training_type_rag_folders` - protecao contra path traversal);
- metas semanais por grupo (`training_type_muscle_group_targets`), cada uma
  com o macro-grupo do grupo ao lado (Fase 6);
- lista de `muscle_groups` (id, nome, macro-grupo EFETIVO e pai da fatia -
  lidos por `load_methodology_catalog`, a mesma leitura do motor);
- no Modo B, a TABELA da metodologia de volume por grupo (as linhas do
  cadastro de macro-grupos da 057, macro-grupo x frequencia, faixa POR SESSAO
  E POR GRUPO mais a meta SEMANAL do macro-grupo - prompt v5) e,
  quando o pedido nao traz perfil de metas, as METAS DERIVADAS dessa tabela por
  grupo - sem isso o prompt nao tinha ancora numerica nenhuma e a LLM entregava
  um esboco de 3 exercicios x 3 series por dia (diagnostico de 2026-09-28);
- no Modo B (prompt v3), a regra de que a faixa da celula vale POR GRUPO DENTRO
  DE CADA SLOT, e um CHECKLIST final mandando conferir par (slot, grupo) antes
  de emitir o JSON. A medicao de 2026-09-28 mostrou o prompt v2 lendo "6-9
  exercicios por slot" como orcamento do DIA: os 10 grupos avaliados sairam
  todos em METHODOLOGY_BELOW_RANGE porque cada um ficou com 2-3 exercicios
  quando a celula `large 1x` pede 4-5 exercicios / 15-20 series daquele grupo
  naquela sessao;
- no Modo B, catalogo resumido da academia com as `SafetyCapabilities` de cada
  par (reuso de `equipment_recommender.recommend_all`, que ja resolve
  `max_safe_rpe`/tecnicas permitidas sem N+1);
- codigos de tecnica elegiveis (`advanced_techniques`, so as ativas).

**Instrucoes livres do usuario (defesa contra injecao de prompt).** Sao DADO,
nunca instrucao:
- truncadas em `MAX_INSTRUCTIONS_CHARS`;
- sem caractere de controle (viram espaco) - nada de quebrar a delimitacao;
- qualquer ocorrencia do proprio delimitador e neutralizada;
- entregues dentro de `<dados_do_usuario>`, com a regra explicita, ANTES e
  DEPOIS do bloco, de que ali dentro nao existem ordens - so preferencias.
E, principalmente: mesmo que a injecao funcione, a saida ainda passa pelo
validador deterministico e pela edicao humana. O prompt e a primeira barreira,
nao a unica.

A prosa do prompt mora no repositorio privado (M103 P3,
`api/privado/prompts/periodization/`); aqui ficam a ordem, os ramos e os
valores (secao "Prompt" abaixo).
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from ..intelligence.methodology_volume import (
    COUNTED_ROLES_BY_MODE,
    DEFAULT_VOLUME_COUNTING_MODE,
    SECONDARY_SESSION_MIN_SETS,
    VolumeCountingMode,
)
from .. import prompt_store, rag_placeholders
from ..models import PeriodizationDraftIn
from ..prompt_user_data import USER_DATA_CLOSE, USER_DATA_OPEN, sanitize_user_text
from ..rag_chunking import (  # noqa: F401 - MAX_RAG_BASE_CHARS reexportado (contrato dos testes)
    MAX_RAG_BASE_CHARS,
    MAX_RAG_FOLDER_CHARS,
    MAX_RAG_TOTAL_CHARS,
    rag_base_limit,
    truncate_rag,
)
from . import catalog_refs, goal_plan
from .limits import (
    MAX_PRIORITIZED_PER_SLOT,
    SESSION_EXERCISES_MAX,
    SESSION_EXERCISES_MIN,
    SESSION_EXERCISES_TARGET_MIN,
    SESSION_EXERCISES_TYPICAL_MAX,
)

# Limites de tamanho: prompt grande custa dinheiro e aumenta a latencia. Os
# tetos do RAG (MAX_RAG_*) e o corte com marca visivel moram em `rag_chunking`
# (dono unico desde o IA-02, compartilhados com Insights e Analise por IA) e
# sao reexportados aqui (contrato dos testes).
MAX_INSTRUCTIONS_CHARS = 2000
# Candidatos por grupo no catalogo enviado a LLM. Subiu de 6 para 8 em v3: a
# regra de volume POR GRUPO DENTRO DO SLOT exige 4-5 exercicios de um mesmo
# grupo grande de baixa frequencia num unico slot, e um grupo treinado 2x no
# ciclo precisa de 3-4 em CADA slot - com 6 candidatos a LLM ficava sem
# escolha (e repetia o mesmo par em dias diferentes) justamente no caso que
# esta correcao ataca. Custo medido no PR: ~2 itens x ~200 caracteres por
# grupo com catalogo.
MAX_CATALOG_ITEMS_PER_GROUP = 8

# Tamanho de uma SESSAO COMPLETA no Modo B (exercicios por slot de treino):
# donos em `limits.py` (M76, Lote 4c, E9), reexportados aqui (contrato dos
# testes). O PISO e o unico numero que o validador confere
# (`SLOT_TOO_FEW_EXERCISES`); o TETO do prompt e folga, nao orcamento (a faixa
# POR GRUPO tem precedencia). As series POR EXERCICIO vem de EXG-004 via
# `methodology_volume` (unica regra de conversao).

# Delimitador e saneamento do texto livre do usuario: dono unico em
# `prompt_user_data` (IA-10, compartilhado com a avaliacao de serie).
_USER_DATA_OPEN = USER_DATA_OPEN
_USER_DATA_CLOSE = USER_DATA_CLOSE


def sanitize_instructions(raw: Optional[str]) -> str:
    """Instrucao livre do usuario pronta para entrar no prompt como DADO.

    Trunca, remove caractere de controle e neutraliza o delimitador - sem isso,
    um texto contendo `</dados_do_usuario>` fecharia o bloco e o resto viraria
    instrucao de sistema aos olhos do modelo."""
    return sanitize_user_text(raw, MAX_INSTRUCTIONS_CHARS)


_truncate = truncate_rag


# ---------------------------------------------------------------------------
# Leitura do contexto
# ---------------------------------------------------------------------------


def load_methodology_catalog(
    conn: Any, counting_mode: Any = None
) -> tuple[list[dict[str, Any]], Mapping[int, Any]]:
    """Grupos musculares (com macro-grupo e pai da fatia) e o cadastro de
    macro-grupos (057), pelas MESMAS leituras do motor
    (`load_methodology_groups` + `load_volume_classes`) e num cursor so.
    `counting_mode`: as faixas e metas sao as DO MODO do Modelo a gerar (073),
    as mesmas que o validador aplica; ausente = o default do motor.

    Os grupos saem ordenados por nome, como sempre foram. A fatia herda o
    macro-grupo do PAI (e avaliada no bloco dele): o prompt mostra o
    macro-grupo EFETIVO, o mesmo que o validador aplica."""
    from ..intelligence.methodology_volume import DEFAULT_VOLUME_COUNTING_MODE
    from ..intelligence.methodology_volume_service import load_methodology_groups, load_volume_classes

    with conn.cursor() as cur:
        groups = load_methodology_groups(cur)
        volume_classes = load_volume_classes(cur, counting_mode or DEFAULT_VOLUME_COUNTING_MODE)
    by_id = {group.id: group for group in groups}

    def _effective_class(group: Any) -> Optional[int]:
        seen: set[int] = set()
        current = group
        while current.methodology_parent_group_id in by_id and current.id not in seen:
            seen.add(current.id)
            current = by_id[current.methodology_parent_group_id]
        return current.volume_class_id

    rows = []
    for group in sorted(groups, key=lambda g: (g.name, g.id)):
        class_id = _effective_class(group)
        spec = volume_classes.get(class_id) if class_id is not None else None
        rows.append(
            {
                "id": group.id,
                "name": group.name,
                "volume_class_id": spec.id if spec is not None else None,
                "macro_grupo": spec.name if spec is not None else None,
                "methodology_parent_group_id": group.methodology_parent_group_id,
            }
        )
    return rows, volume_classes


def load_muscle_groups(conn: Any) -> list[dict[str, Any]]:
    """Só os grupos de `load_methodology_catalog` (compatibilidade)."""
    return load_methodology_catalog(conn)[0]


def methodology_ranges_table(volume_classes: Mapping[int, Any]) -> list[dict[str, Any]]:
    """As linhas do cadastro de macro-grupos (macro-grupo x frequencia, faixa
    POR SESSAO, mais a meta SEMANAL do macro-grupo) num formato serializavel e
    em ordem fixa (a ordem do cadastro, depois o bucket) - e a tabela que o
    prompt mostra a LLM e a mesma que o validador (`_check_methodology`)
    aplica, porque as duas leem o mesmo cadastro. Linha vazia no cadastro
    nao aparece (o validador tambem nao avalia aquele bucket)."""
    from ..intelligence.methodology_volume import FREQUENCY_BUCKETS

    table: list[dict[str, Any]] = []
    for spec in volume_classes.values():
        for bucket in FREQUENCY_BUCKETS:
            cell = spec.range_for(bucket)
            if cell is None:
                continue
            table.append(
                {
                    "macro_grupo": spec.name,
                    "frequency_bucket": bucket,
                    "exercises_per_session_min": cell.exercises_min,
                    "exercises_per_session_max": cell.exercises_max,
                    "sets_per_session_min": cell.sets_min,
                    "sets_per_session_max": cell.sets_max,
                    "weekly_sets_min": spec.weekly_sets_min,
                    "weekly_sets_max": spec.weekly_sets_max,
                    "evidence": _evidence_label(cell),
                }
            )
    return table


def _evidence_label(cell: Any) -> str:
    """Codigo EXG-NNN do autor quando algum eixo o tem; senao a celula e
    derivada (propostas D1/D2, sempre via EXG-004) ou editada pelo admin."""
    for evidence in (cell.sets_evidence, cell.exercises_evidence):
        if str(evidence or "").startswith("EXG-"):
            return str(evidence)
    if "admin" in (getattr(cell.sets_source, "value", None), getattr(cell.exercises_source, "value", None)):
        return "definida pelo admin"
    return "derivada (EXG-004)"


def derive_methodology_targets(
    groups: list[dict[str, Any]], volume_classes: Mapping[int, Any]
) -> list[dict[str, Any]]:
    """METAS DERIVADAS DA METODOLOGIA para o pedido SEM perfil de metas: por
    grupo, o macro-grupo, a faixa de series por sessao em cada frequencia e a
    meta semanal. Grupo sem macro-grupo entra sem faixa (null). Ordem por id,
    para o prompt ser deterministico."""
    from ..intelligence.methodology_volume import FREQUENCY_BUCKETS

    derived: list[dict[str, Any]] = []
    for group in sorted(groups, key=lambda g: g["id"]):
        class_id = group.get("volume_class_id")
        spec = volume_classes.get(class_id) if class_id is not None else None
        per_frequency = {
            bucket: (
                {"min": cell.sets_min, "max": cell.sets_max}
                if spec is not None and (cell := spec.range_for(bucket)) is not None
                else None
            )
            for bucket in FREQUENCY_BUCKETS
        }
        weekly = (
            {"min": spec.weekly_sets_min, "max": spec.weekly_sets_max}
            if spec is not None and spec.has_weekly_target
            else None
        )
        derived.append(
            {
                "muscle_group_id": group["id"],
                "muscle_group_name": group["name"],
                "macro_grupo": spec.name if spec is not None else None,
                "sets_per_session_by_frequency": per_frequency,
                "weekly_sets": weekly,
            }
        )
    return derived


def load_gym_catalog(conn: Any, gym_id: int, user_id: Optional[int]) -> dict[str, list[dict[str, Any]]]:
    """Catalogo RESUMIDO da academia, por grupo muscular, com as capacidades de
    seguranca de cada par. Reusa `equipment_recommender.recommend_all` - mesmo
    ranking (estrelas/recencia/nome) que o recomendador entrega na UI, entao a
    LLM ve primeiro o que o usuario tambem veria primeiro."""
    from ..equipment_recommender import recommend_all

    payload = recommend_all(conn, gym_id=gym_id, user_id=user_id)
    catalog: dict[str, list[dict[str, Any]]] = {}
    for group, group_payload in (payload.get("groups") or {}).items():
        items = [group_payload.get("recommended"), *(group_payload.get("alternatives") or [])]
        resumo = [
            {
                "mapping_id": item["mapping_id"],
                "equipment_id": item["equipment_id"],
                "equipment_name": item.get("equipment_name"),
                "exercise_name": item.get("mapping_exercise_name"),
                "max_safe_rpe": item.get("max_safe_rpe"),
                "failure_allowed": item.get("failure_allowed"),
                "allowed_advanced_techniques": [
                    technique["code"] for technique in item.get("allowed_advanced_techniques") or []
                ],
            }
            for item in items[:MAX_CATALOG_ITEMS_PER_GROUP]
            if item
        ]
        if resumo:
            catalog[group] = resumo
    return catalog


def request_counting_mode(request: Any) -> Any:
    """Modo de contagem do volume do pedido de geracao (`VolumeCountingMode`):
    o escolhido pelo usuario em `PeriodizationDraftIn.volume_counting_mode`;
    ausente = o default do motor. Unico ponto da Periodizacao que resolve o
    modo do pedido (contexto, validador e complemento leem daqui)."""
    from ..intelligence.methodology_volume import DEFAULT_VOLUME_COUNTING_MODE, VolumeCountingMode

    value = getattr(request, "volume_counting_mode", None)
    return VolumeCountingMode(value) if value else DEFAULT_VOLUME_COUNTING_MODE


def counting_mode_of_context(context: Mapping[str, Any]) -> Any:
    """Modo do contexto ja montado (`request.volume_counting_mode`). Contexto
    antigo/congelado sem a chave = o default do motor."""
    from ..intelligence.methodology_volume import DEFAULT_VOLUME_COUNTING_MODE, VolumeCountingMode

    value = (context.get("request") or {}).get("volume_counting_mode")
    return VolumeCountingMode(value) if value else DEFAULT_VOLUME_COUNTING_MODE


def attach_volume_contributions(
    conn: Any,
    catalog: dict[str, list[dict[str, Any]]],
    counting_mode: Any,
    groups: list[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Fora do default do motor, cada par do catalogo ganha
    `volume_contributions`: os BLOCOS que o MOTOR conta para o exercicio no
    modo, com o peso que ele usa (`methodology_volume.block_contributions`
    sobre o cadastro `exercise_muscle_contributions`, com a heranca do
    canonico de `load_inherited_contributions`). `muscle_group_id` e o id do
    bloco: fatia + pai no mesmo bloco viram UMA entrada, com o maior peso, como
    o validador conta - assim a LLM nao soma em dobro. `groups`: os grupos do
    contexto (`load_methodology_catalog`), de onde sai a cadeia fatia -> pai.
    Sem isso a LLM nao tem como fazer a conta ponderada. No default
    (`primary_only`) o catalogo sai intocado: o grupo do par JA e o primario,
    e o prompt fica igual ao de antes do modo existir. Uma leitura so, no
    cursor da transacao do contexto."""
    from ..intelligence.methodology_volume import (
        DEFAULT_VOLUME_COUNTING_MODE,
        SETS_DECIMAL_PLACES,
        MethodologyGroupInput,
        block_contributions,
        block_of_group,
    )
    from ..intelligence.methodology_volume_service import load_inherited_contributions

    if counting_mode is DEFAULT_VOLUME_COUNTING_MODE or not catalog:
        return catalog
    mapping_ids = {int(item["mapping_id"]) for items in catalog.values() for item in items if item.get("mapping_id")}
    with conn.cursor() as cur:
        _canonical, contributions = load_inherited_contributions(cur, mapping_ids, counting_mode)
    by_id = {
        int(g["id"]): MethodologyGroupInput(
            id=int(g["id"]), name=str(g.get("name") or ""), methodology_parent_group_id=g.get("methodology_parent_group_id")
        )
        for g in groups
    }
    block_of = {group_id: block_of_group(group_id, by_id) for group_id in by_id}
    for items in catalog.values():
        for item in items:
            blocks = block_contributions(contributions.get(int(item["mapping_id"])), counting_mode, block_of)
            item["volume_contributions"] = [
                {
                    "muscle_group_id": block,
                    "weight": round(float(c.weight), SETS_DECIMAL_PLACES),
                    "primary": c.is_primary,
                }
                for block, c in sorted(blocks.items())
            ]
    return catalog


def load_rag_text(rag_sources: list[str], target_groups: list[str], training_goals: Any = None) -> str:
    """RAG das fontes escolhidas + a base de sempre. `load_rag_folder_text`
    valida cada nome contra `list_training_type_rag_folders()` e levanta
    `ValueError` para pasta inexistente - o endpoint ja barrou isso com 422
    antes de criar o job, e aqui a excecao viraria job `failed`.

    M97 F5: `training_goals` (objetivos do Modelo e dos dias) traz o material
    proprio do objetivo na frente da base, com o teto da base somado ao dele
    (`rag_base_limit`). Sem objetivo nao-default: texto e teto de sempre."""
    from ..rag_chunking import load_rag_folder_text, select_rag_text

    parts = [
        _truncate(
            select_rag_text(target_groups, has_training_type=True, training_goal=training_goals),
            rag_base_limit(training_goals),
            "RAG base",
        )
    ]
    for folder in rag_sources:
        parts.append(
            f"## Fonte escolhida: {folder}\n\n"
            + _truncate(load_rag_folder_text(folder), MAX_RAG_FOLDER_CHARS, f"fonte {folder}")
        )
    return _truncate("\n\n---\n\n".join(parts), MAX_RAG_TOTAL_CHARS, "material de referencia")


def load_compound_flags(conn: Any, mapping_ids: Any) -> dict[int, bool]:
    """`exercise_catalog.is_compound` dos exercicios (M97 F5: composto x
    isolador num dia de forca). Uma leitura; so roda com objetivo
    nao-default no pedido."""
    ids = sorted({int(mapping_id) for mapping_id in mapping_ids or () if mapping_id})
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, is_compound FROM exercise_catalog WHERE id IN ({placeholders})", tuple(ids))
        rows = cur.fetchall() or []
    return {int(row["id"]): bool(row.get("is_compound")) for row in rows}


def _attach_compound_flags(conn: Any, catalog: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Catalogo da academia com `is_compound` em cada par (so com plano de
    objetivo: o prompt de hipertrofia nao muda)."""
    flags = load_compound_flags(conn, [item["mapping_id"] for items in catalog.values() for item in items])
    for items in catalog.values():
        for item in items:
            item["is_compound"] = flags.get(int(item["mapping_id"]), False)
    return catalog


def build_context(conn: Any, payload: PeriodizationDraftIn, user_id: Optional[int]) -> dict[str, Any]:
    """Todo o contexto factual do prompt, num dict serializavel (fica facil de
    inspecionar num teste e de logar o tamanho sem logar o conteudo)."""
    from ..advanced_techniques import list_techniques
    from ..training_types import get_training_type_muscle_group_targets

    counting_mode = request_counting_mode(payload)
    groups, volume_classes = load_methodology_catalog(conn, counting_mode)
    names_by_id = {group["id"]: group["name"] for group in groups}

    targets_raw = (
        get_training_type_muscle_group_targets(payload.training_type_id) if payload.training_type_id else {}
    )
    macro_by_id = {group["id"]: group.get("macro_grupo") for group in groups}
    targets = [
        {
            "muscle_group_id": group_id,
            "muscle_group_name": names_by_id.get(group_id, str(group_id)),
            "macro_grupo": macro_by_id.get(group_id),
            "min_weekly_sets": values["min"],
            "target_weekly_sets": values["target"],
            "max_weekly_sets": values["max"],
        }
        for group_id, values in sorted(targets_raw.items())
    ]

    # Grupos alvo do RAG: os que tem meta (ou todos, sem perfil de metas).
    target_group_names = [entry["muscle_group_name"] for entry in targets] or [g["name"] for g in groups]
    # M97 F5: objetivo por dia so com objetivo nao-default no pedido (sem ele,
    # contexto e prompt sao os de sempre).
    with_goal_plan = goal_plan.has_goal_plan(payload)
    gym_catalog = (
        attach_volume_contributions(conn, load_gym_catalog(conn, payload.gym_id, user_id), counting_mode, groups)
        if payload.gym_id
        else {}
    )
    if with_goal_plan and gym_catalog:
        gym_catalog = _attach_compound_flags(conn, gym_catalog)

    context = {
        "request": {
            "volume_mode": payload.volume_mode,
            "days_per_cycle": payload.days_per_cycle,
            "training_type_id": payload.training_type_id,
            "gym_id": payload.gym_id,
            "rag_sources": list(payload.rag_sources),
            "volume_counting_mode": counting_mode.value,
        },
        "muscle_groups": groups,
        "targets": targets,
        # Tabela da metodologia (Modo B) e metas derivadas dela quando o pedido
        # nao traz perfil - a unica ancora numerica nesse caso.
        "methodology_ranges": methodology_ranges_table(volume_classes),
        "methodology_targets": derive_methodology_targets(groups, volume_classes) if not targets else [],
        "techniques": [
            {
                "code": technique["code"],
                "name": technique["name"],
                "failure_requirement": technique["failure_requirement"],
                "min_experience": technique["min_experience"],
                # Fase 5 (PR 5): coeficientes do equivalente planejado (EXG-014).
                "activation_set_equivalent": technique.get("activation_set_equivalent"),
                "max_equivalent_per_anchor": technique.get("max_equivalent_per_anchor"),
                "default_sets_count": technique.get("default_sets_count"),
            }
            for technique in list_techniques()
        ],
        "gym_catalog": gym_catalog,
        "rag_text": load_rag_text(
            list(payload.rag_sources),
            target_group_names,
            goal_plan.all_goals(payload) if with_goal_plan else None,
        ),
        "instructions": sanitize_instructions(payload.instructions),
    }
    if with_goal_plan:
        context["goal_plan"] = goal_plan.plan_for_prompt(payload)
    # M98 C5: cardio so com pedido (flag ou preferencias); sem pedido, nem a
    # chave existe e o prompt/schema sao os da v12.
    from .cardio_plan import build_cardio_context

    cardio = build_cardio_context(payload, groups, user_id)
    if cardio is not None:
        context["cardio"] = cardio
    return context


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

# M103 P3: a PROSA do prompt mora no repositorio privado,
# `api/privado/prompts/periodization/<versao>.<parte>.md` (`prompt_store`),
# com `<versao>` = `core.PROMPT_VERSION`. Aqui ficam a ordem das secoes, os
# ramos (modo A/B, modo de contagem, objetivo, cardio, preferencias) e os
# valores, todos lidos dos donos (nenhum numero no template).
PROMPT_FEATURE_DIR = "periodization"

# Fragmento da regra de contagem do Modo B por modo de contagem do Modelo
# (`VolumeCountingMode`, Lote 5 do modo de contagem): entra no lugar de
# `{{counting_rule}}` das regras do Modo B (`.contagem_*`) e de
# `{{checklist_count}}` do checklist final (`.checklist_contagem_*`). O texto de
# `PRIMARY_ONLY` e, letra por letra, o de antes do modo existir (paridade do
# prompt). O de `PRIMARY_SECONDARY` nao tem numero escrito a mao: o modo, os
# papeis contados e o limiar de sessao saem do motor (`_counting_rule`), e os
# pesos vem do cadastro, por par, em `volume_contributions` do catalogo.
_COUNTING_PARTS: dict[VolumeCountingMode, str] = {
    VolumeCountingMode.PRIMARY_ONLY: "primario",
    VolumeCountingMode.PRIMARY_SECONDARY: "ponderada",
}


def prompt_template_version() -> str:
    """Versao dos templates = `core.PROMPT_VERSION` (import tardio: `core`
    importa este modulo)."""
    from .core import PROMPT_VERSION

    return PROMPT_VERSION


def _text(part: str, values: Optional[Mapping[str, str]] = None) -> str:
    return prompt_store.render(PROMPT_FEATURE_DIR, f"{prompt_template_version()}{part}", values or {})


def _counting_rule(mode: VolumeCountingMode, part: str) -> str:
    """Texto do modo (`part` = `.contagem` ou `.checklist_contagem`) com o modo,
    os papeis contados e o limiar de sessao do secundario interpolados do motor
    (`methodology_volume`). Cada template usa so os valores que cita."""
    min_sets = SECONDARY_SESSION_MIN_SETS
    values = {
        "mode": mode.value,
        "default_mode": DEFAULT_VOLUME_COUNTING_MODE.value,
        "roles": " e ".join(sorted(COUNTED_ROLES_BY_MODE[mode])),
        "min_sets": f"{_number(min_sets)} serie" + ("" if min_sets == 1 else "s"),
    }
    name = f"{part}_{_COUNTING_PARTS[mode]}"
    used = set(rag_placeholders.placeholders_in(prompt_store.load(PROMPT_FEATURE_DIR, prompt_template_version() + name)))
    return _text(name, {key: value for key, value in values.items() if key in used})


def build_prompt(context: dict[str, Any]) -> str:
    """Prompt completo. Determinismo de montagem (mesma ordem de secoes, JSON
    com `sort_keys`) para que dois pedidos iguais gerem o mesmo prompt - isso
    e o que torna um teste de prompt possivel."""
    request = context["request"]
    mode_b = request["volume_mode"] == "equipment_series"
    parts = [
        *_stable_parts(context),
        _text(".grupos", {"grupos": _json(context["muscle_groups"])}),
        _targets_section(context, mode_b),
        _text(".tecnicas", {"tecnicas": _json(context["techniques"])}),
    ]
    if mode_b:
        # Cadastro de macro-grupos vazio (ou contexto antigo sem a chave): o
        # prompt diz isso com todas as letras (`.sem_faixas`), em vez de
        # inventar faixa - o validador tambem nao avalia grupo sem macro-grupo.
        ranges = _json(context["methodology_ranges"]) if context.get("methodology_ranges") else _text(".sem_faixas")
        parts.append(_text(".tabela", {"tabela": ranges}))
        # v15: cada linha com a referencia curta do motor (`catalog_refs`) no
        # lugar do par de ids; a LLM escolhe a referencia e o motor monta o trio.
        parts.append(_text(".catalogo", {"catalogo": _json(catalog_refs.catalog_for_prompt(context))}))
    if context.get("goal_plan"):
        parts.append(goal_plan_section(context["goal_plan"], mode_b))
    if context.get("cardio"):
        from .cardio_plan import prompt_section

        parts.append(prompt_section(context["cardio"]))
    if context["instructions"]:
        parts.append(
            _text(
                ".dados_usuario",
                {"open": _USER_DATA_OPEN, "instructions": context["instructions"], "close": _USER_DATA_CLOSE},
            )
        )
    if mode_b:
        # Checklist de fechamento do Modo B. Vai por ULTIMO no prompt de
        # proposito: e o passo que a medicao de 2026-09-28 mostrou faltar - a
        # LLM montava um dia plausivel e nunca conferia a conta por grupo
        # contra a celula.
        parts.append(_final_checklist(request["days_per_cycle"], counting_mode_of_context(context)))
    return "\n\n".join(parts)


def _stable_parts(context: dict[str, Any]) -> list[str]:
    """Cabecalho + regras + RAG: o prefixo do prompt igual entre geracoes com
    o mesmo pedido de dias/modo e as mesmas fontes."""
    request = context["request"]
    return [
        _text(".cabecalho"),
        _text(
            ".regras",
            {
                "days": f"{request['days_per_cycle']}",
                "volume_mode": f"{request['volume_mode']}",
                "max_prioritized": f"{MAX_PRIORITIZED_PER_SLOT}",
                "mode_rules": _mode_rules(request["volume_mode"], context),
            },
        ),
        # Ordem fixa (IA-05, v9): instrucoes -> RAG -> dados do pedido. O RAG
        # vem logo depois das regras para o prefixo (cabecalho + regras + RAG)
        # ser identico entre geracoes com as mesmas fontes: o prompt caching
        # automatico da OpenAI so reaproveita PREFIXO identico.
        _text(".rag", {"rag": context["rag_text"]}),
    ]


def build_prompt_cache_prefix(context: dict[str, Any]) -> str:
    """Comeco estavel de `build_prompt` (as mesmas partes, a mesma juncao),
    para o `cache_control` do provedor Anthropic. Nao muda o prompt."""
    return "\n\n".join(_stable_parts(context))


def _final_checklist(days: int, counting_mode: VolumeCountingMode = DEFAULT_VOLUME_COUNTING_MODE) -> str:
    """Checklist de fechamento do Modo B, com o numero de series padrao lido da
    MESMA constante do resto do prompt (texto e constante nunca divergem) e o
    numero de dias do PEDIDO (item 0: as regras duras conferidas no fim, nao so
    no topo - a medicao de 2026-09-28 mostrou o modelo cumprindo o volume e
    esquecendo a regra dura). O item 2 (a conta por grupo) e o do modo de
    contagem do Modelo (`.checklist_contagem_*`)."""
    from ..intelligence.methodology_volume import _SETS_PER_EXERCISE_MAX

    return _text(
        ".checklist",
        {
            "sets_max": f"{_SETS_PER_EXERCISE_MAX}",
            "days": f"{days}",
            "max_prioritized": f"{MAX_PRIORITIZED_PER_SLOT}",
            "checklist_count": _counting_rule(counting_mode, ".checklist_contagem"),
        },
    )


def _mode_rules(volume_mode: str, context: Optional[Mapping[str, Any]] = None) -> str:
    """Regras do modo com TODO numero interpolado da fonte (M76, Lote 4c - E10):
    nenhuma faixa, limiar ou fator escrito a mao no texto.

    - Modo A: a faixa de cobertura (90%/110%) e a de `template_volume_domain`,
      a mesma que `validate_template_volume_coverage` aplica;
    - Modo B: tamanho da sessao de `limits.py`, series por exercicio de EXG-004
      (`methodology_volume`), o exemplo de celula da TABELA DA METODOLOGIA do
      proprio contexto (o cadastro de macro-grupos que o validador aplica), o
      exemplo de tecnica calculado com o catalogo `techniques` do contexto
      (`technique_accounting.planned_equivalent`) e os textos de exemplo de
      reps/RPE dos defaults de prescricao; a regra de contagem por grupo e a
      do modo de contagem do Modelo (`.contagem_*`)."""
    from ..domain.prescription_defaults import DEFAULT_REPS_TARGET, DEFAULT_RPE_TARGET
    from ..intelligence.methodology_volume import _SETS_PER_EXERCISE_MAX, _SETS_PER_EXERCISE_MIN
    from ..intelligence.template_volume_domain import (
        OVER_ALLOCATION_THRESHOLD_PCT,
        UNDER_ALLOCATION_THRESHOLD_PCT,
    )

    if volume_mode != "equipment_series":
        return _text(
            ".modo_a",
            {
                "coverage_min": _number(UNDER_ALLOCATION_THRESHOLD_PCT),
                "coverage_max": _number(OVER_ALLOCATION_THRESHOLD_PCT),
            },
        )
    context = context or {}
    return _text(
        ".modo_b",
        {
            "ex_min": f"{SESSION_EXERCISES_MIN}",
            "ex_target_min": f"{SESSION_EXERCISES_TARGET_MIN}",
            "ex_typical_max": f"{SESSION_EXERCISES_TYPICAL_MAX}",
            "ex_max": f"{SESSION_EXERCISES_MAX}",
            "sets_min": f"{_SETS_PER_EXERCISE_MIN}",
            "sets_max": f"{_SETS_PER_EXERCISE_MAX}",
            "methodology_example": methodology_cell_example(
                context.get("methodology_ranges") or [], _SETS_PER_EXERCISE_MIN
            ),
            "technique_example": technique_equivalent_example(context.get("techniques") or [], _SETS_PER_EXERCISE_MAX),
            "reps_example": f"{DEFAULT_REPS_TARGET}",
            "rpe_example": f"{DEFAULT_RPE_TARGET}",
            "counting_rule": _counting_rule(counting_mode_of_context(context), ".contagem"),
        },
    )


def _goal_day_rules(goal: str, mode_b: bool) -> str:
    """Regras de UM objetivo nao-default num dia, com todo numero dos donos
    (`prescription_defaults.defaults_for`, `training_safety`, `limits`)."""
    from ..domain.prescription_defaults import defaults_for
    from ..domain.training_goal import goal_label
    from ..domain.training_safety import goal_disallowed_techniques
    from .limits import day_item_goal, heavy_compound_range, session_exercises_min

    label = goal_label(goal)
    defaults = defaults_for(goal)
    lines = [_text(".objetivo_regras", {"objetivo": label})]
    if not mode_b:
        lines.append(_text(".objetivo_modo_a", {"objetivo": label}))
        return "\n".join(lines)
    compound_range = heavy_compound_range(goal)
    isolation_goal = day_item_goal(goal, False)
    isolation = defaults_for(isolation_goal)
    if compound_range is not None:
        lines.append(
            _text(
                ".objetivo_compostos",
                {
                    "compostos_min": f"{compound_range[0]}",
                    "compostos_max": f"{compound_range[1]}",
                    "reps": f"{defaults.reps_target}",
                    "rpe": f"{defaults.rpe_target}",
                    "series": f"{defaults.sets_count}",
                },
            )
        )
    if isolation_goal.value != goal:
        lines.append(
            _text(
                ".objetivo_isoladores",
                {
                    "objetivo_isoladores": goal_label(isolation_goal),
                    "reps": f"{isolation.reps_target}",
                    "rpe": f"{isolation.rpe_target}",
                },
            )
        )
    techniques = sorted(goal_disallowed_techniques(goal))
    if techniques:
        lines.append(_text(".objetivo_tecnicas", {"tecnicas": ", ".join(techniques), "objetivo": label}))
    lines.append(
        _text(
            ".objetivo_descanso",
            {"descanso_min": f"{defaults.min_rest_seconds}", "descanso_max": f"{defaults.max_rest_seconds}"},
        )
    )
    lines.append(_text(".objetivo_minimo", {"exercicios_min": f"{session_exercises_min(goal)}"}))
    return "\n".join(lines)


def goal_plan_section(plan: Mapping[str, Any], mode_b: bool) -> str:
    """OBJETIVO POR DIA (M97 F5): o objetivo do Modelo, o de cada dia e as
    regras de cada objetivo nao-default presente. So entra no prompt com
    objetivo nao-default no pedido (`goal_plan.has_goal_plan`)."""
    from ..domain.training_goal import is_default

    days = plan.get("days") or []
    day_lines = ", ".join(
        _text(".objetivo_dia", {"sequencia": f"{day['sequence']}", "rotulo": f"{day['label']}"}) for day in days
    )
    goals = []
    for goal in [plan.get("template_goal"), *(day["training_goal"] for day in days)]:
        if goal and not is_default(goal) and goal not in goals:
            goals.append(goal)
    rules = "\n\n".join(_goal_day_rules(goal, mode_b) for goal in goals)
    return _text(
        ".objetivo",
        {"objetivo_modelo": f"{plan.get('template_goal_label')}", "dias": day_lines, "regras": rules},
    )


def _number(value: Any) -> str:
    """Numero no texto do prompt: inteiro sem casas; fracao com virgula e no
    maximo 2 casas (4.8 -> "4,8")."""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".").replace(".", ",")


# Bucket de frequencia do exemplo da celula: o caso que a medicao de 2026-09-28
# mostrou falhar (grupo de faixa alta treinado uma vez na janela).
_EXAMPLE_FREQUENCY_BUCKET = "1x"
# Aplicacoes da tecnica no exemplo de equivalente planejado.
_EXAMPLE_TECHNIQUE_APPLICATIONS = 2
# Ordem de preferencia da tecnica do exemplo (a primeira com equivalente > 0).
_EXAMPLE_TECHNIQUES: tuple[str, ...] = ("rest_pause", "drop_set")


# Exercicios do slot "magro" do exemplo quando a celula nao tem faixa de
# exercicios (o admin pode cadastrar so series - e o caso de producao).
_EXAMPLE_FEW_EXERCISES = 2


def methodology_cell_example(ranges: list[Mapping[str, Any]], sets_min: int) -> str:
    """Exemplo do erro a evitar, com os numeros da TABELA DA METODOLOGIA do
    contexto (nunca escritos a mao - antes o texto dizia "4-5 exercicios e
    15-20 series", o seed da 057, e a tabela de producao ja era outra): a celula
    1x de maior PISO de series e um slot com poucos exercicios no PISO de series
    por exercicio, abaixo da faixa. A faixa de exercicios entra no texto so
    quando a celula a tem. Tabela vazia (ou sem celula que sirva) = sem
    exemplo; a regra continua valendo."""
    cells = [
        row
        for row in ranges
        if row.get("frequency_bucket") == _EXAMPLE_FREQUENCY_BUCKET
        and row.get("sets_per_session_min")
        and row.get("sets_per_session_max")
    ]
    if not cells:
        return ""
    cell = max(cells, key=lambda row: float(row["sets_per_session_min"]))
    ex_min, ex_max = cell.get("exercises_per_session_min"), cell.get("exercises_per_session_max")
    few_exercises = max(1, int(float(ex_min)) // 2) if ex_min else _EXAMPLE_FEW_EXERCISES
    few_sets = few_exercises * int(sets_min)
    if few_sets >= float(cell["sets_per_session_min"]) or (ex_min and few_exercises >= float(ex_min)):
        return ""
    exercises = (
        _text(".exemplo_celula_exercicios", {"exercicios_min": _number(ex_min), "exercicios_max": _number(ex_max)})
        if ex_min and ex_max
        else ""
    )
    return _text(
        ".exemplo_celula",
        {
            "macro_grupo": f"{cell.get('macro_grupo')}",
            "frequencia": _EXAMPLE_FREQUENCY_BUCKET,
            "exercicios": exercises,
            "series_min": _number(cell["sets_per_session_min"]),
            "series_max": _number(cell["sets_per_session_max"]),
            "poucos_exercicios": f"{few_exercises}",
            "series_por_exercicio": f"{sets_min}",
            "poucas_series": f"{few_sets}",
        },
    )


def technique_equivalent_example(techniques: list[Mapping[str, Any]], sets_count: int) -> str:
    """Exemplo do equivalente planejado da tecnica (EXG-014) calculado com os
    coeficientes do catalogo do contexto - o mesmo `planned_equivalent` que o
    validador e o preview usam. Sem tecnica com equivalente > 0 no catalogo,
    sem exemplo."""
    from ..domain.technique_accounting import planned_equivalent

    by_code = {str(row.get("code")): row for row in techniques if row.get("code")}
    for code in _EXAMPLE_TECHNIQUES:
        row = by_code.get(code)
        if row is None:
            continue
        equivalent = planned_equivalent(code, _EXAMPLE_TECHNIQUE_APPLICATIONS, {code: row})
        if equivalent <= 0:
            continue
        total = sets_count + equivalent
        return _text(
            ".exemplo_tecnica",
            {
                "series": f"{sets_count}",
                "tecnica": code,
                "aplicacoes": f"{_EXAMPLE_TECHNIQUE_APPLICATIONS}",
                "equivalente": _number(equivalent),
                "total": _number(total),
            },
        )
    return ""


def _targets_section(context: dict[str, Any], mode_b: bool) -> str:
    """Metas por grupo. Com perfil: as metas semanais do perfil (com o
    macro-grupo de cada grupo ao lado). Sem perfil, no Modo B: as METAS
    DERIVADAS DA METODOLOGIA - sem elas a LLM nao tinha alvo numerico nenhum.
    Modo A sem perfil segue como antes (o volume ali e % de uma meta que nao
    existe)."""
    if context["targets"]:
        return _text(".metas_perfil", {"metas": _json(context["targets"])})
    derived = context.get("methodology_targets") or []
    if mode_b and derived:
        return _text(".metas_derivadas", {"metas": _json(derived)})
    return _text(".sem_metas")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
