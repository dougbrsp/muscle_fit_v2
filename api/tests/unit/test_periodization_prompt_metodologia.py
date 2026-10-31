"""Prompt do Motor de Periodizacao: volume POR GRUPO DENTRO DO SLOT e 4 series
como padrao (2a correcao do diagnostico de 2026-09-28), parte ESTRUTURAL.

O v2 ja pedia sessao completa, mas com um teto de 6-9 exercicios por slot que a
LLM leu como orcamento do DIA: repartiu esses 9 entre todos os grupos e os 10
grupos medidos em DEV sairam TODOS em METHODOLOGY_BELOW_RANGE (large 1x com 11
series quando a celula pede 15-20). O v3 amarra a faixa da celula ao par
(slot, grupo), da precedencia a ela sobre o alvo por slot e fecha com um
checklist.

M103 P3: a prosa do prompt mora no repositorio privado
(`api/privado/prompts/periodization/`), e os asserts que citam frases dela
foram para `api/privado/tests/unit/test_periodization_prompt.py`. Aqui fica o
que nao depende do texto, sem banco e sem rede, e sem por o prompt no log
(`tests.support.privado.exigir`):

1. no Modo B o prompt nomeia os codigos do validador e traz a TABELA das
   linhas do cadastro de macro-grupos (057; prompt v5) - numeros lidos do
   MESMO cadastro que o validador aplica, com a meta SEMANAL de cada
   macro-grupo; os tetos e as series por exercicio sao os dos donos;
1b. o checklist final fecha o prompt do Modo B (inclusive depois do bloco do
   usuario) e nao aparece no Modo A;
2. sem perfil de metas, a secao de metas e a DERIVADA da metodologia
   (macro-grupo, faixa por frequencia e meta semanal, por grupo); com perfil,
   a secao de sempre ganha o macro-grupo de cada grupo;
3. o Modo A nao paga a tabela (token) e continua como era;
4. determinismo (`sort_keys`, ordem fixa).
"""

from __future__ import annotations

import json


from app.intelligence.methodology_volume import (
    FREQUENCY_BUCKETS,
    _SETS_PER_EXERCISE_MAX,
    _SETS_PER_EXERCISE_MIN,
)
from app.periodization import context as ctx
from app.periodization import validation
from app.periodization.core import ENGINE_VERSION, PROMPT_VERSION
from app.prompt_user_data import USER_DATA_CLOSE
from tests.support.methodology_paridade import SIZE_TO_CLASS_ID, seed_volume_classes
from tests.support.fake_db import CursorFactoryConn, NullContext
from tests.support.privado import exigir

# Cadastro = seed da 057 (os numeros da v1): Grandes, Pequenos, Outros.
CLASSES = seed_volume_classes()
GRANDES, PEQUENOS, OUTROS = SIZE_TO_CLASS_ID["large"], SIZE_TO_CLASS_ID["small"], SIZE_TO_CLASS_ID["abs"]


def _g(gid, name, class_id, parent=None):
    return {
        "id": gid,
        "name": name,
        "volume_class_id": class_id,
        "macro_grupo": CLASSES[class_id].name if class_id else None,
        "methodology_parent_group_id": parent,
    }


GRUPOS = [
    _g(52, "Peitoral", GRANDES),
    _g(12, "Peitoral superior", GRANDES, 52),  # fatia: macro-grupo EFETIVO (o do pai)
    _g(5, "Bíceps", PEQUENOS),
    _g(1, "Abdômen", OUTROS),
    _g(55, "Core", None),
]


def _contexto(volume_mode="equipment_series", com_perfil=False, **overrides):
    targets = (
        [
            {
                "muscle_group_id": 52,
                "muscle_group_name": "Peitoral",
                "macro_grupo": "Grandes",
                "min_weekly_sets": 10,
                "target_weekly_sets": 14,
                "max_weekly_sets": 18,
            }
        ]
        if com_perfil
        else []
    )
    base = {
        "request": {
            "volume_mode": volume_mode,
            "days_per_cycle": 8,
            "training_type_id": 4 if com_perfil else None,
            "gym_id": 7,
            "rag_sources": ["6. Volume por Grupo Muscular (Leandro Twin)"],
        },
        "muscle_groups": GRUPOS,
        "targets": targets,
        "methodology_ranges": ctx.methodology_ranges_table(CLASSES),
        "methodology_targets": [] if com_perfil else ctx.derive_methodology_targets(GRUPOS, CLASSES),
        "techniques": [{"code": "drop_set", "name": "Drop set", "failure_requirement": "requires_failure"}],
        "gym_catalog": {"Peitoral": [{"mapping_id": 1001, "equipment_id": 5, "max_safe_rpe": 9}]},
        "rag_text": "## Arquivo: PROGRESSAO.md",
        "instructions": "",
    }
    base.update(overrides)
    return base


def _checklist(contexto) -> str:
    return ctx._final_checklist(contexto["request"]["days_per_cycle"], ctx.counting_mode_of_context(contexto))


# ---------------------------------------------------------------------------
# 1. Regras do Modo B: sessao completa e conta por grupo
# ---------------------------------------------------------------------------


def test_versao_do_prompt_subiu_para_v6():
    # v4 = v3 (volume por grupo) + as regras duras repetidas no checklist final
    # e o trio copiado de UMA linha do catalogo (confiabilidade, 2026-09-28).
    # v5 (Fase 6) = tabela lida do cadastro de macro-grupos + meta semanal.
    # v6 (Fase 7, Frente B) = item com `rest_seconds`, sempre null.
    # v7 (Fase 5) = equivalente planejado da tecnica.
    # v8 (M76 Lote 4c) = todo numero interpolado da fonte.
    # v9 (IA-06 b) = so o RAG muda (aquecimento/ajuste e reps remetem aos motores).
    # v10 (IA-05) = RAG logo depois das regras (prefixo cacheavel).
    # v11 (modo de contagem, Lote 5) = regra de contagem e checklist pelo modo do pedido.
    # v12 (M97 F5) = objetivo por dia; v13 (M98 C5) = cardio no Modelo quando pedido.
    # v15 = referencia do catalogo (`catalog_refs`) no lugar do trio; sem `rest_seconds` no schema.
    assert PROMPT_VERSION == "periodization-v15"
    assert ENGINE_VERSION.startswith("periodization-v8.")


def test_modo_b_nomeia_os_codigos_do_validador_e_usa_os_tetos_do_dono():
    prompt = ctx.build_prompt(_contexto())
    # Piso do validador intocado; teto por slot com folga para a faixa por grupo.
    assert (ctx.SESSION_EXERCISES_MIN, ctx.SESSION_EXERCISES_TARGET_MIN, ctx.SESSION_EXERCISES_MAX) == (6, 8, 12)
    assert ctx.SESSION_EXERCISES_MIN == validation.SESSION_EXERCISES_MIN == 6
    # Os codigos que o validador vai emitir estao nomeados para a LLM.
    for code in (
        "SLOT_TOO_FEW_EXERCISES",
        "METHODOLOGY_BELOW_RANGE",
        "METHODOLOGY_ABOVE_RANGE",
        validation.METHODOLOGY_WEEKLY_BELOW_RANGE,
        validation.METHODOLOGY_WEEKLY_ABOVE_RANGE,
    ):
        exigir(code in prompt, f"codigo {code} no prompt do Modo B")
    exigir(ctx._mode_rules("equipment_series", _contexto()) in prompt, "regras do Modo B no prompt")


def test_modo_b_traz_a_tabela_com_todas_as_linhas_do_cadastro():
    prompt = ctx.build_prompt(_contexto())
    tabela = ctx.methodology_ranges_table(CLASSES)
    exigir(json.dumps(tabela, ensure_ascii=False, sort_keys=True) in prompt, "tabela da metodologia no prompt")
    assert len(tabela) == 9
    for spec in CLASSES.values():
        for bucket in FREQUENCY_BUCKETS:
            cell = spec.range_for(bucket)
            linha = next(t for t in tabela if t["macro_grupo"] == spec.name and t["frequency_bucket"] == bucket)
            assert (linha["sets_per_session_min"], linha["sets_per_session_max"]) == (cell.sets_min, cell.sets_max)
            assert (linha["exercises_per_session_min"], linha["exercises_per_session_max"]) == (
                cell.exercises_min,
                cell.exercises_max,
            )
            assert (linha["weekly_sets_min"], linha["weekly_sets_max"]) == (spec.weekly_sets_min, spec.weekly_sets_max)
    # Ordem do cadastro (Grandes, Pequenos, Outros), depois o bucket.
    assert [t["macro_grupo"] for t in tabela[::3]] == ["Grandes", "Pequenos", "Outros"]
    # Numeros do autor no prompt (EXG-015: Grandes 1x = 15-20 series, 4-5 exercicios).
    exigir('"sets_per_session_min": 15' in prompt and '"sets_per_session_max": 20' in prompt, "celula EXG-015")
    exigir('"evidence": "EXG-015"' in prompt, "evidencia EXG-015")
    # Meta semanal do macro-grupo (seed: 2x a faixa por sessao).
    exigir('"weekly_sets_max": 32, "weekly_sets_min": 18' in prompt, "meta semanal do macro-grupo")
    # Celula derivada e declarada como tal, sem paragrafo de justificativa (token).
    exigir('"evidence": "derivada (EXG-004)"' in prompt, "celula derivada")
    exigir("proposta D2" not in prompt, "sem a justificativa da proposta D2")
    # O vocabulario antigo (porte fixo) saiu do prompt.
    exigir("size_class" not in prompt and '"large"' not in prompt, "sem o porte fixo antigo")


def test_tabela_omite_linha_vazia_e_marca_celula_do_admin():
    from app.intelligence import methodology_volume as mv

    lombar = mv.VolumeClassSpec(
        id=9,
        name="Lombar",
        ranges={"2x": mv.MethodologyRange(sets_min=4.5, sets_max=8, sets_source=mv.RangeSource.ADMIN)},
    )
    tabela = ctx.methodology_ranges_table({9: lombar})
    assert tabela == [
        {
            "macro_grupo": "Lombar",
            "frequency_bucket": "2x",
            "exercises_per_session_min": None,
            "exercises_per_session_max": None,
            "sets_per_session_min": 4.5,
            "sets_per_session_max": 8,
            "weekly_sets_min": None,
            "weekly_sets_max": None,
            "evidence": "definida pelo admin",
        }
    ]


def test_grupos_musculares_vem_com_o_macro_grupo_ao_lado_do_id():
    prompt = ctx.build_prompt(_contexto())
    exigir(
        '"id": 52, "macro_grupo": "Grandes", "methodology_parent_group_id": null, "name": "Peitoral"' in prompt,
        "grupo com macro-grupo",
    )
    exigir('"id": 12, "macro_grupo": "Grandes", "methodology_parent_group_id": 52' in prompt, "fatia com o pai")
    exigir('"id": 55, "macro_grupo": null' in prompt, "grupo sem macro-grupo")


def test_series_padrao_e_piso_sao_as_de_exg004():
    """v3: o v2 dizia so "3 a 4 series" e a LLM entregou 3 em quase todo item.
    Sem literal solto: os dois numeros do texto sao os da constante de EXG-004
    (o texto em si e travado no privado e pelo snapshot)."""
    assert (_SETS_PER_EXERCISE_MIN, _SETS_PER_EXERCISE_MAX) == (3, 4)


def test_faixa_da_celula_cita_os_campos_da_tabela():
    prompt = ctx.build_prompt(_contexto())
    # Os nomes dos campos da tabela, para a LLM saber onde ler a faixa do par.
    for campo in (
        "exercises_per_session_min",
        "exercises_per_session_max",
        "sets_per_session_min",
        "sets_per_session_max",
    ):
        exigir(campo in prompt, f"campo {campo} no prompt")
    # O exemplo do erro a evitar sai da TABELA do contexto (M76 Lote 4c).
    exemplo = ctx.methodology_cell_example(_contexto()["methodology_ranges"], _SETS_PER_EXERCISE_MIN)
    exigir(bool(exemplo) and exemplo in prompt, "exemplo da celula no prompt")


def test_checklist_final_fecha_o_prompt_do_modo_b():
    contexto = _contexto()
    exigir(ctx.build_prompt(contexto).endswith(_checklist(contexto)), "checklist fecha o prompt do Modo B")


def test_checklist_vem_depois_do_bloco_do_usuario_e_fica_com_a_ultima_palavra():
    """Ordem fixa: o checklist fecha o prompt inclusive quando ha instrucao
    livre - assim nenhuma preferencia do usuario fica na posicao final."""
    contexto = _contexto(instructions="Enfase em costas.")
    prompt = ctx.build_prompt(contexto)
    exigir(prompt.index(USER_DATA_CLOSE) < prompt.index(_checklist(contexto)), "checklist depois do bloco do usuario")
    exigir(prompt.endswith(_checklist(contexto)), "checklist com a ultima palavra")


def test_catalogo_cabe_na_faixa_de_um_grupo_grande_de_frequencia_1x():
    """Com 4-5 exercicios exigidos de um mesmo grupo num unico slot, 6
    candidatos por grupo nao deixavam escolha nenhuma a LLM."""
    assert ctx.MAX_CATALOG_ITEMS_PER_GROUP == 8
    maior_exigencia = max(
        cell.exercises_max for spec in CLASSES.values() for cell in spec.ranges.values() if cell.exercises_max
    )
    assert ctx.MAX_CATALOG_ITEMS_PER_GROUP > maior_exigencia


# ---------------------------------------------------------------------------
# 2. Metas: derivadas sem perfil, com macro-grupo quando ha perfil
# ---------------------------------------------------------------------------


def test_sem_perfil_a_secao_de_metas_e_a_derivada_da_metodologia():
    contexto = _contexto(com_perfil=False)
    prompt = ctx.build_prompt(contexto)
    secao = ctx._targets_section(contexto, mode_b=True)
    exigir(secao in prompt and secao != ctx._text(".sem_metas"), "metas derivadas no prompt")
    exigir(ctx._text(".sem_metas") not in prompt, "sem a secao de 'sem perfil'")
    # Peitoral (Grandes): 1x 15-20, 2x 9-16, 3x+ derivado, semanal 18-32; Core sem faixa.
    exigir('"1x": {"max": 20, "min": 15}' in prompt, "faixa 1x do Peitoral")
    exigir('"2x": {"max": 16, "min": 9}' in prompt, "faixa 2x do Peitoral")
    exigir('"weekly_sets": {"max": 32, "min": 18}' in prompt, "meta semanal do Peitoral")
    exigir('"muscle_group_name": "Core"' in prompt, "Core nas metas")
    exigir('"1x": null' in prompt and '"weekly_sets": null' in prompt, "grupo sem faixa")


def test_derive_methodology_targets_e_deterministica_e_por_id():
    derivadas = ctx.derive_methodology_targets(GRUPOS, CLASSES)
    assert [d["muscle_group_id"] for d in derivadas] == [1, 5, 12, 52, 55]
    assert derivadas[0]["macro_grupo"] == "Outros"
    assert derivadas[0]["sets_per_session_by_frequency"]["2x"] == {
        "min": CLASSES[OUTROS].range_for("2x").sets_min,
        "max": CLASSES[OUTROS].range_for("2x").sets_max,
    }
    assert derivadas[0]["weekly_sets"] == {"min": 12, "max": 24}
    assert derivadas[-1]["sets_per_session_by_frequency"] == {"1x": None, "2x": None, "3x_plus": None}
    assert derivadas[-1]["macro_grupo"] is None and derivadas[-1]["weekly_sets"] is None
    # Serializavel e estavel (o prompt usa sort_keys).
    assert json.dumps(derivadas, sort_keys=True) == json.dumps(
        ctx.derive_methodology_targets(list(reversed(GRUPOS)), CLASSES), sort_keys=True
    )


def test_com_perfil_a_secao_de_metas_e_a_do_perfil_com_o_macro_grupo():
    contexto = _contexto(com_perfil=True)
    prompt = ctx.build_prompt(contexto)
    exigir(ctx._targets_section(contexto, mode_b=True) in prompt, "metas do perfil no prompt")
    exigir('"macro_grupo": "Grandes", "max_weekly_sets": 18' in prompt, "macro-grupo ao lado da meta do perfil")
    derivadas = json.dumps(ctx.derive_methodology_targets(GRUPOS, CLASSES), ensure_ascii=False, sort_keys=True)
    exigir(derivadas not in prompt, "sem as metas derivadas")
    # A tabela continua: e ela que dimensiona a sessao mesmo com perfil.
    tabela = json.dumps(contexto["methodology_ranges"], ensure_ascii=False, sort_keys=True)
    exigir(tabela in prompt, "tabela com perfil")


# ---------------------------------------------------------------------------
# 3. Modo A nao paga a tabela; 4. determinismo e compatibilidade
# ---------------------------------------------------------------------------


def test_modo_a_nao_recebe_tabela_nem_metas_derivadas():
    contexto = _contexto(volume_mode="percent_budget")
    prompt = ctx.build_prompt(contexto)
    exigir(ctx._mode_rules("percent_budget") in prompt, "regras do Modo A")
    exigir(ctx._mode_rules("equipment_series", contexto) not in prompt, "sem as regras do Modo B")
    tabela = json.dumps(contexto["methodology_ranges"], ensure_ascii=False, sort_keys=True)
    exigir(tabela not in prompt, "sem a tabela")
    exigir(ctx._text(".sem_metas") in prompt, "Modo A sem perfil: secao de sempre")
    exigir(prompt.startswith(ctx._text(".cabecalho")), "o cabecalho e comum")
    # O checklist e a regra por grupo sao do Modo B: o Modo A nao paga esses tokens.
    exigir(_checklist(contexto) not in prompt, "sem o checklist")


def test_prompt_e_deterministico_nos_dois_modos():
    for modo in ("equipment_series", "percent_budget"):
        assert ctx.build_prompt(_contexto(volume_mode=modo)) == ctx.build_prompt(_contexto(volume_mode=modo))


def test_contexto_antigo_sem_as_chaves_novas_continua_montando_o_prompt():
    """Compatibilidade: um contexto sem `methodology_*` (fixture antiga, ou
    cadastro vazio) monta o prompt e diz que nao ha faixa - sem inventar."""
    contexto = _contexto()
    del contexto["methodology_ranges"]
    del contexto["methodology_targets"]
    prompt = ctx.build_prompt(contexto)
    exigir(ctx._text(".tabela", {"tabela": ctx._text(".sem_faixas")}) in prompt, "tabela vazia declarada")
    exigir(ctx._text(".sem_metas") in prompt, "sem metas")


# ---------------------------------------------------------------------------
# Leitura dos grupos e do cadastro: as MESMAS leituras do motor, num cursor so
# ---------------------------------------------------------------------------


def test_load_methodology_catalog_traz_macro_grupo_efetivo_ordenado_por_nome(monkeypatch):
    from app.intelligence import methodology_volume_service as svc
    from app.intelligence.methodology_volume import MethodologyGroupInput

    cursores = []
    monkeypatch.setattr(
        svc,
        "load_methodology_groups",
        lambda cur: (
            cursores.append(cur)
            or (
                MethodologyGroupInput(52, "Peitoral", volume_class_id=GRANDES),
                MethodologyGroupInput(5, "Bíceps", volume_class_id=PEQUENOS),
                # fatia associada a outro macro-grupo: vale o do pai
                MethodologyGroupInput(12, "Peitoral superior", volume_class_id=PEQUENOS, methodology_parent_group_id=52),
                MethodologyGroupInput(55, "Core"),
            )
        ),
    )
    monkeypatch.setattr(svc, "load_volume_classes", lambda cur, mode=None: cursores.append(cur) or CLASSES)

    grupos, classes = ctx.load_methodology_catalog(CursorFactoryConn(NullContext))
    assert classes is CLASSES
    assert len(cursores) == 2 and cursores[0] is cursores[1]  # mesma leitura, um cursor
    assert grupos == [
        {"id": 5, "name": "Bíceps", "volume_class_id": PEQUENOS, "macro_grupo": "Pequenos", "methodology_parent_group_id": None},
        {"id": 55, "name": "Core", "volume_class_id": None, "macro_grupo": None, "methodology_parent_group_id": None},
        {"id": 52, "name": "Peitoral", "volume_class_id": GRANDES, "macro_grupo": "Grandes", "methodology_parent_group_id": None},
        {"id": 12, "name": "Peitoral superior", "volume_class_id": GRANDES, "macro_grupo": "Grandes", "methodology_parent_group_id": 52},
    ]
