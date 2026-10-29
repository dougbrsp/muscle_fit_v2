"""Cenários de referência da paridade do motor da metodologia (Fase 6, PR 2).

O motor deixou de ter as faixas em código e passou a ler o cadastro de
macro-grupos (057). A garantia do PR: com os valores do seed da 057, o status
POR SESSÃO de cada eixo (exercícios e séries) é idêntico ao do motor antigo.

`tests/unit/data/methodology_volume_paridade_pre_f6.json` foi gerado pelo motor
ANTES da mudança (as faixas fixas de `METHODOLOGY_RANGES`), rodando exatamente
os cenários abaixo; o teste de paridade roda os mesmos cenários no motor novo
e compara. Os cenários ficam aqui, e não no teste, para o arquivo congelado e
o teste nunca divergirem.

Fase 7 (métrica v3, janela de 7 dias a partir do primeiro slot de treino): o
arquivo foi ajustado DE PROPÓSITO num único cenário, `ciclo_3`. O peitoral
treinado no slot 1 de um ciclo de 3 slots cai 3 vezes na janela (1,2,3,1,2,3,1):
a frequência passa de 2,33 (7/3, coluna 2x) para 3 (coluna 3x_plus), e as
faixas por sessão passam a ser as da célula 3x_plus. Os status continuam
WITHIN_RANGE. Os cenários de 7 slots e o `ciclo_9` (os dois treinos caem
dentro da janela) não mudaram.
"""

from __future__ import annotations

import functools
from typing import Any, Callable

from app import migrate as m
from app.intelligence import methodology_volume as mv

MIGRATION_057 = m.DEFAULT_MIGRATIONS_DIR / "057_macro_grupos_volume.py"


@functools.lru_cache(maxsize=1)
def _seed_057() -> dict[str, Any]:
    return m.load_python_migration(MIGRATION_057).__globals__


# Porte da 055 -> id do macro-grupo no fixture (a ordem do seed: Grandes,
# Pequenos, Outros). `unclassified` fica sem macro-grupo, como no seed.
SIZE_TO_CLASS_ID: dict[str, int] = {
    size: pos for pos, (_n, _d, _s, size, *_r) in enumerate(_seed_057()["SEED_CLASSES"], start=1)
}


def seed_volume_classes() -> dict[int, mv.VolumeClassSpec]:
    """Os macro-grupos do seed da 057 no formato do motor - lidos do PRÓPRIO
    módulo da migração, para o fixture nunca divergir do que o banco recebe.
    Uso EXCLUSIVO de teste: em produção quem monta isto é
    `methodology_volume_service.load_volume_classes`, a partir do banco."""
    specs: dict[int, mv.VolumeClassSpec] = {}
    for pos, (name, _d, _s, _size, wmin, wmax, faixas) in enumerate(_seed_057()["SEED_CLASSES"], start=1):
        ranges = {
            bucket: mv.MethodologyRange(
                sets_min=s_min,
                sets_max=s_max,
                sets_source=mv.RangeSource(s_src),
                sets_evidence=s_evid,
                exercises_min=ex_min,
                exercises_max=ex_max,
                exercises_source=mv.RangeSource(ex_src),
                exercises_evidence=ex_evid,
            )
            for bucket, ex_min, ex_max, s_min, s_max, ex_src, ex_evid, s_src, s_evid in faixas
        }
        specs[pos] = mv.VolumeClassSpec(
            id=pos,
            name=name,
            ranges=ranges,
            weekly_sets_min=wmin,
            weekly_sets_max=wmax,
            weekly_source=mv.RangeSource.DERIVED,
            weekly_evidence=_seed_057()["WEEKLY_EVIDENCE"],
        )
    return specs


def semear_macro_grupos(cur: Any) -> dict[str, int]:
    """Integração (suíte v3): regrava no banco os macro-grupos do seed da 057
    depois do `truncar` (as duas tabelas estão em `TABELAS_V3`) e devolve
    `porte da 055 -> id do macro-grupo`, para o arquivo associar os grupos que
    ele mesmo insere. Os valores vêm do módulo da migração, como no unitário."""
    seed = _seed_057()
    ids: dict[str, int] = {}
    for name, description, sort_order, size, wmin, wmax, faixas in seed["SEED_CLASSES"]:
        cur.execute(
            "INSERT INTO muscle_volume_classes (name, description, sort_order, weekly_sets_min, weekly_sets_max, "
            "weekly_source, weekly_evidence) VALUES (%s, %s, %s, %s, %s, 'derived', %s)",
            (name, description, sort_order, wmin, wmax, seed["WEEKLY_EVIDENCE"]),
        )
        ids[size] = int(cur.lastrowid)
        for bucket, ex_min, ex_max, s_min, s_max, ex_src, ex_evid, s_src, s_evid in faixas:
            cur.execute(
                "INSERT INTO muscle_volume_class_ranges (class_id, frequency_bucket, sets_min, sets_max, "
                "exercises_min, exercises_max, sets_source, sets_evidence, exercises_source, exercises_evidence) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (ids[size], bucket, s_min, s_max, ex_min, ex_max, s_src, s_evid, ex_src, ex_evid),
            )
    return ids


def grupo(
    group_id: int, name: str, size_class: str, parent: int | None = None
) -> mv.MethodologyGroupInput:
    """Grupo com o macro-grupo que o seed da 057 daria ao porte dele."""
    return mv.MethodologyGroupInput(
        group_id,
        name,
        volume_class_id=SIZE_TO_CLASS_ID.get(size_class),
        methodology_parent_group_id=parent,
        size_class=size_class,
    )

PEITORAL, PEITORAL_SUP = 52, 12
COSTAS_LARG, COSTAS_MIOLO = 6, 7
BICEPS, TRICEPS, ABDOMEN, CORE = 5, 14, 1, 55

# (id, nome, porte da 055, pai)
GRUPOS: tuple[tuple[int, str, str, int | None], ...] = (
    (PEITORAL, "Peitoral", "large", None),
    (PEITORAL_SUP, "Peitoral superior", "large", PEITORAL),
    (COSTAS_LARG, "Costas - largura dorsal", "large", None),
    (COSTAS_MIOLO, "Costas - miolo/densidade", "large", COSTAS_LARG),
    (BICEPS, "Biceps", "small", None),
    (TRICEPS, "Triceps", "small", None),
    (ABDOMEN, "Abdomen", "abs", None),
    (CORE, "Core", "unclassified", None),
)

PRIMARY = {
    100: frozenset({PEITORAL}), 107: frozenset({PEITORAL}), 101: frozenset({PEITORAL_SUP}),
    110: frozenset({PEITORAL}), 111: frozenset({PEITORAL_SUP}), 112: frozenset({PEITORAL}),
    113: frozenset({PEITORAL}), 102: frozenset({COSTAS_LARG}), 103: frozenset({COSTAS_MIOLO}),
    109: frozenset({COSTAS_LARG}), 104: frozenset({BICEPS}), 105: frozenset({ABDOMEN}),
    106: frozenset({TRICEPS}), 120: frozenset({CORE}),
}
CANONICAL = {100: 1000, 107: 1000, 101: 1001, 110: 1002, 111: 1003, 112: 1004, 113: 1005,
             102: 1010, 103: 1011, 109: 1012, 104: 1020, 105: 1030, 106: 1040, 120: 1200}


def _it(mapping_id: int, sets_count: int | None = 3) -> mv.MethodologyItemInput:
    return mv.MethodologyItemInput(
        mapping_id=mapping_id, canonical_exercise_id=CANONICAL[mapping_id], sets_count=sets_count
    )


def _grupo_a(group_id: int, sets_count: int | None) -> mv.MethodologyItemInput:
    return mv.MethodologyItemInput(muscle_group_id=group_id, sets_count=sets_count)


def _treino(seq: int, *items: mv.MethodologyItemInput) -> mv.MethodologySlotInput:
    return mv.MethodologySlotInput(sequence=seq, slot_type="normal", name=f"S{seq}", items=tuple(items))


def _descanso(seq: int) -> mv.MethodologySlotInput:
    return mv.MethodologySlotInput(sequence=seq, slot_type="descanso", name="Descanso")


def _ciclo(dias: int, **treinos: tuple[mv.MethodologyItemInput, ...]) -> list[mv.MethodologySlotInput]:
    por_dia = {int(k[1:]): v for k, v in treinos.items()}
    return [_treino(d, *por_dia[d]) if d in por_dia else _descanso(d) for d in range(1, dias + 1)]


_PADRAO = mv.MethodologyIntent()

# nome -> (slots, volume_mode, intenção)
CENARIOS: dict[str, tuple[list[mv.MethodologySlotInput], str, mv.MethodologyIntent]] = {
    "dentro_1x": (_ciclo(7, d1=(_it(100), _it(107), _it(101), _it(110), _it(111))), "equipment_series", _PADRAO),
    "abaixo_1x": (_ciclo(7, d1=(_it(100, 4), _it(101, 4), _it(110, 4))), "equipment_series", _PADRAO),
    "acima_1x": (
        _ciclo(7, d1=tuple(_it(m, 4) for m in (100, 101, 110, 111, 112, 113))),
        "equipment_series",
        _PADRAO,
    ),
    "ciclo_3": (_ciclo(3, d1=(_it(100, 4), _it(101, 4), _it(110, 4))), "equipment_series", _PADRAO),
    "ciclo_9": (
        _ciclo(9, d1=(_it(100, 4), _it(101, 4), _it(110, 4)), d6=(_it(100, 4), _it(101, 4), _it(110, 4))),
        "equipment_series",
        _PADRAO,
    ),
    "tres_x": (
        _ciclo(7, d1=(_it(100, 4), _it(110, 4)), d3=(_it(100, 4), _it(110, 4)), d5=(_it(100, 4), _it(110, 4))),
        "equipment_series",
        _PADRAO,
    ),
    "misto_ppl": (
        _ciclo(
            7,
            d1=(_it(100, 4), _it(102, 4), _it(104, 3), _it(105, 3), _it(106, 4), _it(120, 3)),
            d2=(_it(103, 4), _it(109, 4), _it(104, 3), _it(105, 3)),
            d4=(_it(100, 3), _it(106, 3), _it(105, 4)),
            d6=(_it(104, 2), _it(105, 2), _it(106, 2)),
        ),
        "equipment_series",
        _PADRAO,
    ),
    "cutting": (
        _ciclo(7, d1=(_it(100, 4), _it(101, 4), _it(110, 4))),
        "equipment_series",
        mv.MethodologyIntent("cutting"),
    ),
    "especializacao": (
        _ciclo(7, d1=tuple(_it(m, 4) for m in (100, 101, 110, 111, 112, 113)), d3=(_it(104, 3),)),
        "equipment_series",
        mv.MethodologyIntent("specialization", frozenset({PEITORAL_SUP})),
    ),
    "modo_a_series": (
        _ciclo(7, d1=(_grupo_a(PEITORAL, 16), _grupo_a(BICEPS, 4)), d2=(_grupo_a(BICEPS, 20), _grupo_a(ABDOMEN, 6))),
        "percent_budget",
        _PADRAO,
    ),
    "modo_a_percentual": (_ciclo(7, d1=(_grupo_a(PEITORAL, None),)), "percent_budget", _PADRAO),
    "sem_sessao": (_ciclo(3), "equipment_series", _PADRAO),
}


def _eixo(axis: Any) -> list[Any]:
    def _v(x: Any) -> Any:
        return getattr(x, "value", x)

    return [
        _v(axis.status),
        _v(axis.reason),
        _v(axis.severity),
        axis.value_per_session,
        axis.range_min,
        axis.range_max,
        _v(axis.range_source),
        axis.range_evidence,
    ]


def instantaneo(
    avaliar: Callable[[list, str, mv.MethodologyIntent], mv.MethodologyVolumeEvaluation],
) -> dict[str, Any]:
    """`cenário -> grupo -> {bucket, exercícios, séries}` com status, motivo,
    severidade, valor e a faixa comparada (com origem e evidência)."""
    saida: dict[str, Any] = {}
    for nome, (slots, modo, intencao) in CENARIOS.items():
        resultado = avaliar(slots, modo, intencao)
        saida[nome] = {
            str(g.muscle_group_id): {
                "frequency_bucket": g.frequency_bucket,
                "direct_exercises_axis": _eixo(g.direct_exercises_axis),
                "direct_sets_axis": _eixo(g.direct_sets_axis),
            }
            for g in resultado.groups
        }
    return saida
