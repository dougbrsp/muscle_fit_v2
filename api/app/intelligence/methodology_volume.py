"""Motor PURO da avaliacao de Modelo pela metodologia de volume por grupo
(EXERCICIOS_GRUPOS / Leandro Twin) - Fase 4, PR C.

Metrica `methodology-volume-v5` (modo de contagem, Lote 2): o payload expoe
`counting_mode` (o `VolumeCountingMode` em que o Modelo foi avaliado, gravado
na versao do Modelo pela migracao 073) e as faixas/metas chegam JA DO MODO
(cadastro por modo, lido por `methodology_volume_service.load_volume_classes`).
Em `primary_only` os numeros sao os da v4, bit a bit; so o campo novo entra.

Metrica `methodology-volume-v4` (Fase 5, PR 5): `direct_sets` de um item =
`sets_count` + o equivalente PLANEJADO da tecnica avancada do item
(`technique_accounting.planned_equivalent`: aplicacoes x
`activation_set_equivalent`, limitado pelo teto; FST-7/GVT = 0). O motor
recebe o numero pronto em `MethodologyItemInput.technique_equivalent` e
devolve a parcela separada (`technique_equivalent*`) para a tela escrever
"4 series + 0,8 equiv.". Modelo sem tecnica: numeros identicos aos da v3.

Metrica `methodology-volume-v3` (Fase 7): a frequencia, a media por sessao
e o eixo semanal passam a ser medidos numa JANELA DE 7 DIAS a partir do
primeiro slot de treino (ver "A unidade"), sem o fator `7/cycle_days` da v2.

Metrica `methodology-volume-v2` (Fase 6, PR 2). A v1 tinha as faixas em
codigo (`METHODOLOGY_RANGES`) e o porte fixo da 055; na v2 as faixas vem do
cadastro de macro-grupos (migração 057, editável na aba admin), recebidas como
ENTRADA (`volume_classes`), e o grupo aponta para o macro-grupo pelo
`volume_class_id`. Com os valores do seed da 057, o status por sessao e
identico ao da v1 (teste de paridade).

Segunda leitura do mesmo Modelo, ao lado
da regua atual de series equivalentes ponderadas - as duas unidades NAO se
somam e nenhum campo daqui se chama `sets` ou `volume` sozinho: sempre
`direct_sets` / `direct_exercises`, para ninguem cruzar um "12" ponderado com
um "12" direto.

Sem I/O, sem banco, sem FastAPI: recebe slots, contribuicoes do modo, grupos e
intencao como ENTRADA e devolve dataclasses serializaveis. Quem le o banco e o
PR E (endpoints); quem projeta a ativacao ponderada e o PR D.

A unidade (contrato do plano, secao "Desenho"):

- Sessao = slot de treino (`slot_type != 'descanso'`; o modelo grava
  `'normal'`, o plano chama de `'treino'` - os dois sao aceitos). Descanso nao
  e sessao.
- Modo de contagem (`VolumeCountingMode`, Lote 1 do "modo de contagem do
  volume por Modelo"): `PRIMARY_ONLY` (default, o de todas as versoes
  anteriores) ou `PRIMARY_SECONDARY`. Os papeis contados por modo sao
  `COUNTED_ROLES_BY_MODE` (estabilizador nunca) e o peso de cada contribuicao
  sai de `counted_contributions`.
- Trabalho direto no grupo g (Modo B) = item cujo `mapping_id` tem
  contribuicao ATIVA em g num papel contado pelo modo. O chamador entrega SO
  essas contribuicoes, com o `stimulus_weight` do cadastro e o papel.
- `direct_sets(s, g)` = soma de `sets_count` (+ equivalente planejado da
  tecnica, v4) dos itens diretos x peso, SEM aquecimento/feeler (nao sao
  series validas). `PRIMARY_ONLY`: peso 1, sem `stimulus_weight` (ponderar
  contaria o indireto duas vezes contra as faixas do primario - EXG-014, que
  vale para este modo). `PRIMARY_SECONDARY`: peso = `stimulus_weight`
  cadastrado, primario e secundario (opcao do usuario, com faixas proprias a
  partir do Lote 2); as fracoes se somam entre exercicios, sem arredondar
  para serie inteira. Sessao do grupo (frequencia): trabalho PRIMARY nele ou
  fracoes de SECONDARY na sessao >= `SECONDARY_SESSION_MIN_SETS`. Exercicio
  do grupo: so PRIMARY, nos dois modos.
- `direct_exercises(s, g)` = COUNT DISTINCT do exercicio canonico; sem
  canonico cai para o `mapping_id` e conta em
  `data_quality.items_without_canonical`.
- Janela de avaliacao (v3): 7 dias de calendario (um slot = um dia) a
  partir do PRIMEIRO slot de treino, percorrendo o ciclo em ordem; ciclo com
  menos de 7 slots da a volta (3 slots -> 1,2,3,1,2,3,1) e ciclo com mais de
  7 slots corta o que passa do 7o dia. Descanso dentro da janela conta como
  dia (so nao tem sessao). Ver `evaluation_window`.
- `frequency_raw` (= `weekly_frequency`) = sessoes DA JANELA com trabalho
  direto (inteiro); series/exercicios por sessao = media dessas sessoes;
  series semanais = soma da janela. Sem a escala `7 / cycle_days` da v2 (que
  dava 0,875 a um grupo treinado 1x num ciclo de 8 slots).
- `cycle_days = len(slots)` (so descreve o ciclo) - a mesma convencao de
  `volume_service.template_cycle_length_days`, usada desde 2026-10-02 por
  `resolve_template_plan` (antes ela usava `7 * slots`).
- Fatias (`methodology_parent_group_id`) sao somadas no pai ANTES de
  comparar; a fatia nao aparece como bloco proprio.

Comparacao: a metodologia prescreve POR SESSAO (EXG-015..018, EXG-031). O
valor comparado com a faixa e a MEDIA por sessao com trabalho direto
(`direct_exercises_per_session`, `direct_sets_per_session`), e o payload traz
cada sessao ao lado para a tela mostrar de onde a media veio.

Eixo SEMANAL (Fase 6; janela na v3): series diretas da janela de 7 dias comparadas com
a meta semanal do macro-grupo (`weekly_sets_axis`), com o mesmo `_compare`.
O grupo ganha um `status` COMBINADO dos eixos avaliados (ver `_combine`). A
antiga coluna `weekly_projection` (proposta D1, sem status) fica no payload
por uma versao, deprecated.

Sem tolerancia de +-5%: `volume_service.ON_TARGET_TOLERANCE_PCT` existe para
comparar um numero com um ALVO pontual. Aqui a faixa ja E um intervalo
dimensionado pelo autor (4-5, 3-4...): alargar 5% para cada lado seria
reescrever a metodologia. Por isso a comparacao e `min <= valor <= max`, seca.

Intencao declarada (standard | specialization | maintenance | cutting) NUNCA
muda o status: ela so troca `severity` de `warning` para `informational`
quando o desvio e do lado que a intencao espera (EXG-022, EXG-024, EXG-030).
Alvo de especializacao ABAIXO da faixa continua `warning`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final, Iterable, Literal, Mapping, Optional, Protocol, Sequence, TypeVar

from ..domain.free_exercise_item import FREE_EXERCISE_PRIMARY_WEIGHT
from ..domain.training_goal import DEFAULT_GOAL, TrainingGoal, read_goal, sql_goal_case
from .dashboard.metrics_registry import metric_version, threshold

METRIC_NAME: Final[str] = "methodology_volume"

# ---------------------------------------------------------------------------
# Vocabulario
# ---------------------------------------------------------------------------

# Porte da 055. Deprecated desde a Fase 6: o motor le `volume_class_id`; o
# porte so volta no payload (`size_class`) por uma versao, para o frontend.
SizeClass = Literal["large", "small", "abs", "unclassified"]
FrequencyBucket = Literal["1x", "2x", "3x_plus"]
VolumeMode = Literal["equipment_series", "percent_budget"]
IntentKind = Literal["standard", "specialization", "maintenance", "cutting"]

SIZE_CLASSES: Final[tuple[str, ...]] = ("large", "small", "abs", "unclassified")
FREQUENCY_BUCKETS: Final[tuple[str, ...]] = ("1x", "2x", "3x_plus")
INTENT_KINDS: Final[tuple[str, ...]] = ("standard", "specialization", "maintenance", "cutting")

# Slot que NAO e sessao. Tudo o mais ('normal' gravado pelo modelo, 'treino'
# no vocabulario do plano) conta como sessao de treino.
REST_SLOT_TYPE: Final[str] = "descanso"


class VolumeCountingMode(str, Enum):
    """Modo de contagem do volume da metodologia, escolha do usuario por
    Modelo: gravado na versao do Modelo (`workout_plan_template_versions.
    volume_counting_mode`, migracao 073). Avaliam no modo do Modelo: editor,
    diagnostico, avisos e a comparacao entre modos (Lote 3), validador e
    complemento da IA no modo do rascunho (Lote 5); progressao (teto de
    ativacao, trava de ADD_SET) e realizado de 7 dias, no modo do Modelo que
    originou a sessao (Lote 4). So o rotulo de grupo do relatorio Markdown
    fica em `PRIMARY_ONLY` (rotulo nao e volume).

    - `PRIMARY_ONLY`: so o grupo PRIMARIO conta, com peso 1 (EXG-014: nao usar
      meias-series). E o comportamento das metricas v1..v4.
    - `PRIMARY_SECONDARY`: as SERIES de PRIMARY e SECONDARY entram ponderadas
      pelo `stimulus_weight` do cadastro (`exercise_muscle_contributions`): a
      puxada com biceps SECONDARY de peso 0,5 x 2 series = 1 serie de biceps;
      3 series com SECONDARY 0,15 = 0,45 serie; as fracoes se somam entre
      exercicios. A sessao conta na frequencia do grupo com trabalho PRIMARY
      ou com fracoes de SECONDARY >= `SECONDARY_SESSION_MIN_SETS` nela;
      `direct_exercises` so conta PRIMARY (decisao do usuario, 2026-10-16).
      Opcao do usuario, avaliada contra faixas PROPRIAS do modo
      (Lote 2), para o indireto nao ser contado em dobro contra as faixas do
      primario.

    Estabilizador nunca conta, em nenhum modo. (Volume da metodologia apenas:
    a recuperacao usa o `fatigue_weight` de todos os papeis e nao depende
    deste modo - ver `VolumeService._session_dose_for_item`.)"""

    PRIMARY_ONLY = "primary_only"
    PRIMARY_SECONDARY = "primary_secondary"


DEFAULT_VOLUME_COUNTING_MODE: Final["VolumeCountingMode"] = VolumeCountingMode.PRIMARY_ONLY

# Papeis de `exercise_muscle_contributions.role` contados por modo. O loader
# (`methodology_volume_service.load_volume_contributions`) le SO estes papeis;
# STABILIZER fica de fora de todos.
COUNTED_ROLES_BY_MODE: Final[Mapping[VolumeCountingMode, frozenset[str]]] = {
    VolumeCountingMode.PRIMARY_ONLY: frozenset({"PRIMARY"}),
    VolumeCountingMode.PRIMARY_SECONDARY: frozenset({"PRIMARY", "SECONDARY"}),
}

# Peso de TODA contribuicao em `PRIMARY_ONLY` - presenca, nao peso de papel
# (o peso de papel vem sempre do cadastro, `stimulus_weight`). Inteiro de
# proposito: a soma continua inteira e o payload sai bit a bit igual ao da v4.
PRIMARY_ONLY_WEIGHT: Final[int] = 1


# --- Objetivo de treino no volume HIPERTRÓFICO (M97 F2, 2026-10-25; §66.2) --
# Decisão do usuário (25/10): série de FORÇA conta no volume de hipertrofia
# (séries semanais por grupo, metas/status, mapa corporal, metodologia do
# realizado, teto de ativação da progressão) com este peso; série de
# hipertrofia conta 1. A recuperação/fadiga (dose, `fatigue_equivalent_sets`,
# contexto de fadiga prévia) conta 100% e NÃO passa por aqui. Valor a calibrar
# (PR da F2): proposta inicial 0,5 (3-5 reps pesadas estimulam hipertrofia,
# mas menos por série que 6-12 perto da falha).
STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT: Final[float] = 0.5

_HYPERTROPHY_VOLUME_WEIGHT_BY_GOAL: Final[Mapping[TrainingGoal, float]] = {
    TrainingGoal.HYPERTROPHY: 1.0,
    TrainingGoal.STRENGTH: STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT,
}


_DEFAULT_GOAL_VALUE: Final[str] = DEFAULT_GOAL.value


def hypertrophy_volume_weight(training_goal: Any = None) -> float:
    """Peso de UMA série do objetivo no volume hipertrófico (leitura tolerante
    do dono `training_goal`: ausente/desconhecido = hipertrofia = 1)."""
    return _HYPERTROPHY_VOLUME_WEIGHT_BY_GOAL[read_goal(training_goal)]


def hypertrophy_volume_units(units: float, training_goal: Any = None) -> float:
    """Séries (equivalentes) de uma linha no volume hipertrófico. Hipertrofia:
    as mesmas `units`, intocadas (sem multiplicar por 1.0, para o float sair
    idêntico ao de antes)."""
    if training_goal is None or training_goal == _DEFAULT_GOAL_VALUE:
        return units  # caminho quente (toda linha de hipertrofia): sem Enum
    weight = hypertrophy_volume_weight(training_goal)
    return units if weight == 1.0 else units * weight


def sql_hypertrophy_volume_weight(alias: str = "e") -> str:
    """O MESMO peso de `hypertrophy_volume_weight` como expressão SQL sobre a
    linha `alias` de `exercises` (M97 F4), para as agregações que somam o
    volume hipertrófico no próprio SQL (`SUM(series * peso * <isto>)`):
    força = `STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT`, qualquer outro = 1. O
    número é interpolado desta constante, nunca escrito no consumidor. Só o
    ESTÍMULO/séries diretas usam; fadiga, dose e séries físicas seguem cheias."""
    return sql_goal_case(alias, TrainingGoal.STRENGTH, format(float(STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT), "f").rstrip("0").rstrip(".") or "0", "1")

# `PRIMARY_SECONDARY`: uma sessao SEM trabalho PRIMARY no grupo conta para a
# frequencia dele (bucket 1x/2x/3x+) quando as fracoes de SECONDARY do grupo
# NAQUELA sessao somam ao menos isto (1 serie inteira; decisao do usuario,
# 2026-10-16). `direct_exercises` nunca conta o secundario.
SECONDARY_SESSION_MIN_SETS: Final[int] = 1

# Rotulo de cada modo (seletor do editor de Modelo, diagnostico e aba admin).
# O FE le daqui via `motor-constants.generated.json` (`export_for_frontend`),
# nunca escreve o valor nem o rotulo do modo a mao.
VOLUME_COUNTING_MODE_LABELS: Final[Mapping["VolumeCountingMode", str]] = {
    VolumeCountingMode.PRIMARY_ONLY: "Só primários",
    VolumeCountingMode.PRIMARY_SECONDARY: "Primários + secundários (ponderado)",
}

# Unidade do numero contado em cada modo (texto de aviso e de tela). Em
# `PRIMARY_ONLY` e o "series diretas" de sempre (sem ponderacao); no modo
# ponderado o numero soma o secundario pela participacao cadastrada.
COUNTED_SETS_UNIT_LABELS: Final[Mapping["VolumeCountingMode", str]] = {
    VolumeCountingMode.PRIMARY_ONLY: "séries diretas",
    VolumeCountingMode.PRIMARY_SECONDARY: "séries ponderadas",
}
COUNTED_SETS_UNIT_SINGULAR_LABELS: Final[Mapping["VolumeCountingMode", str]] = {
    VolumeCountingMode.PRIMARY_ONLY: "série direta",
    VolumeCountingMode.PRIMARY_SECONDARY: "série ponderada",
}


def counting_mode_description(mode: "VolumeCountingMode | str") -> str:
    """Regua do modo em uma frase, com o limiar interpolado da constante
    (o FE nunca escreve o numero a mao)."""
    mode = VolumeCountingMode(mode)
    if mode is VolumeCountingMode.PRIMARY_ONLY:
        return (
            "Conta só o grupo primário de cada exercício, sem ponderação. "
            "Secundários e estabilizadores ficam fora."
        )
    sessao = "série" if SECONDARY_SESSION_MIN_SETS == 1 else "séries"
    return (
        "Primário e secundário somam séries × participação cadastrada no exercício; "
        "estabilizadores ficam fora. A sessão conta na frequência do grupo com primário "
        f"ou com ao menos {SECONDARY_SESSION_MIN_SETS:g} {sessao} somada de secundário. "
        "Exercícios por sessão contam só o primário."
    )


def export_for_frontend() -> dict[str, object]:
    """Bloco `methodology_volume` de `motor-constants.generated.json`: valores,
    rotulos e regua de cada modo de contagem, para o FE nunca escrever o modo."""
    return {
        # Buckets de frequencia (1x/2x/3x+) na ordem do motor: a aba admin
        # conta o que falta num modo sem lista propria.
        "frequency_buckets": list(FREQUENCY_BUCKETS),
        # Rotulo do balde sem macro-grupo (o FE nunca escreve o texto).
        "unclassified_volume_class_label": VOLUME_CLASS_UNCLASSIFIED_LABEL,
        "default_counting_mode": DEFAULT_VOLUME_COUNTING_MODE.value,
        "counting_modes": [
            {
                "value": mode.value,
                "label": VOLUME_COUNTING_MODE_LABELS[mode],
                "description": counting_mode_description(mode),
                "sets_unit_label": COUNTED_SETS_UNIT_LABELS[mode],
                "sets_unit_label_singular": COUNTED_SETS_UNIT_SINGULAR_LABELS[mode],
            }
            for mode in VolumeCountingMode
        ],
    }

# Casas decimais de series e pesos que o motor entrega (`_num`) - e com que
# consumidores (ex.: o catalogo do prompt da Periodizacao) arredondam peso.
SETS_DECIMAL_PLACES: Final[int] = 2


@dataclass(frozen=True)
class ContributionRow:
    """Linha CRUA de `exercise_muscle_contributions` (ativa), como o loader a
    le: grupo, papel e `stimulus_weight`. A juncao das linhas (papel contado,
    peso, mesmo grupo em dois papeis) e so do motor (`counted_contributions`)."""

    muscle_group_id: int
    role: str
    stimulus_weight: Optional[float]


@dataclass(frozen=True)
class GroupContribution:
    """Contribuicao de um exercicio num grupo JA RESOLVIDA para o modo: o peso
    que conta e se ha papel PRIMARY (so a presenca PRIMARY conta
    sessao/exercicio do grupo)."""

    weight: float
    is_primary: bool


# Contribuicoes de UM exercicio, em uma de tres formas:
# - linhas cruas do cadastro (`ContributionRow`, o que o loader entrega);
# - `{grupo: GroupContribution}` ja resolvido;
# - conjunto de grupos (a forma da v4): atalho de `PRIMARY_ONLY`, todos PRIMARY.
ExerciseContribution = (
    Sequence[ContributionRow] | Mapping[int, GroupContribution] | frozenset[int] | set[int] | tuple[int, ...]
)
# `mapping_id -> contribuicoes do exercicio`, entrada do motor.
VolumeContributions = Mapping[int, ExerciseContribution]


def counted_roles(mode: "VolumeCountingMode | str") -> frozenset[str]:
    """Papeis contados no modo (`ValueError` para modo desconhecido)."""
    return COUNTED_ROLES_BY_MODE[VolumeCountingMode(mode)]


def counted_roles_for_modes(modes: Iterable["VolumeCountingMode | str"]) -> frozenset[str]:
    """Uniao dos papeis contados por varios modos - o que um loader le UMA vez
    para avaliar janelas em modos diferentes (cada modo filtra o seu no motor,
    `counted_contributions`). Sem modo = os do default."""
    roles: frozenset[str] = frozenset()
    for mode in modes:
        roles |= counted_roles(mode)
    return roles or counted_roles(DEFAULT_VOLUME_COUNTING_MODE)


def counts_secondary(mode: "VolumeCountingMode | str") -> bool:
    """O modo conta o papel SECONDARY (fracoes ponderadas no grupo)."""
    return "SECONDARY" in counted_roles(mode)


def _merge_rows(rows: Sequence[ContributionRow], mode: VolumeCountingMode) -> dict[int, GroupContribution]:
    """Linhas cruas -> `{grupo: GroupContribution}`, so com os papeis do modo
    (`COUNTED_ROLES_BY_MODE`; estabilizador nunca). Mesmo grupo em dois
    papeis contados: fica o maior peso, e e primario se algum papel for."""
    roles = counted_roles(mode)
    merged: dict[int, GroupContribution] = {}
    for row in rows:
        role = str(row.role or "").strip().upper()
        if role not in roles:
            continue
        group_id = int(row.muscle_group_id)
        weight = float(row.stimulus_weight) if row.stimulus_weight is not None else 0.0
        is_primary = role == "PRIMARY"
        current = merged.get(group_id)
        if current is not None:
            weight = max(weight, current.weight)
            is_primary = is_primary or current.is_primary
        merged[group_id] = GroupContribution(weight, is_primary)
    return merged


def counted_contributions(
    value: Optional[ExerciseContribution],
    mode: "VolumeCountingMode | str" = DEFAULT_VOLUME_COUNTING_MODE,
) -> dict[int, GroupContribution]:
    """`{grupo: GroupContribution}` de UM exercicio, com o peso QUE O MODO USA.

    `PRIMARY_ONLY`: so as PRIMARY, todas com `PRIMARY_ONLY_WEIGHT` (presenca;
    uma PRIMARY cadastrada com peso != 1 conta como na v4).
    `PRIMARY_SECONDARY`: PRIMARY e SECONDARY com o `stimulus_weight`
    cadastrado; peso <= 0 nao conta. Unica regra de peso da metodologia: o
    motor e os consumidores (complemento de volume da IA) chamam esta."""
    mode = VolumeCountingMode(mode)
    if not value:
        return {}
    if not isinstance(value, Mapping):
        items = tuple(value)
        if not any(isinstance(item, ContributionRow) for item in items):
            return {int(group): GroupContribution(PRIMARY_ONLY_WEIGHT, True) for group in items}
        value = _merge_rows(items, mode)
    if mode is VolumeCountingMode.PRIMARY_ONLY:
        return {
            int(group): GroupContribution(PRIMARY_ONLY_WEIGHT, True)
            for group, contribution in value.items()
            if contribution.is_primary
        }
    return {
        int(group): GroupContribution(float(contribution.weight), contribution.is_primary)
        for group, contribution in value.items()
        if contribution.weight is not None and float(contribution.weight) > 0
    }


def counting_mode_or_default(value: "VolumeCountingMode | str | None") -> VolumeCountingMode:
    """Modo de UMA sessao: o da versao do Modelo que a originou; sessao sem
    Modelo (treino livre, ficha sem Modelo, versao nao encontrada) = o default
    (`primary_only`). Modo desconhecido levanta `ValueError` (o ENUM do banco
    ja recusa)."""
    if value is None or value == "":
        return DEFAULT_VOLUME_COUNTING_MODE
    return VolumeCountingMode(getattr(value, "value", value))


def counting_mode_for_window(
    session_modes: Iterable["VolumeCountingMode | str | None"],
) -> VolumeCountingMode:
    """Modo de UMA janela de avaliacao do REALIZADO (teto de ativacao da
    progressao, "Ativacao 7 dias"): a janela inteira usa uma regua so, a da
    sessao MAIS RECENTE ate a data avaliada (decisao do usuario, 2026-10-16).
    `session_modes` em ordem cronologica; o ultimo vence. Janela sem sessao =
    o default. Nunca mistura modos dentro da janela."""
    last: "VolumeCountingMode | str | None" = None
    for mode in session_modes:
        last = mode
    return counting_mode_or_default(last)


def counts_as_frequency_session(has_primary: bool, secondary_sets: float) -> bool:
    """A sessao (slot do Modelo, dia do realizado) conta na FREQUENCIA do
    grupo: com trabalho PRIMARY nele, ou com as fracoes de SECONDARY do grupo
    NAQUELA sessao somando ao menos `SECONDARY_SESSION_MIN_SETS` (2 casas, como
    `_num`). Em `primary_only` nao ha fracao de secundario: so a PRIMARY conta."""
    return bool(has_primary) or round(float(secondary_sets or 0.0), SETS_DECIMAL_PLACES) >= SECONDARY_SESSION_MIN_SETS


def block_contributions(
    value: Optional[ExerciseContribution],
    mode: "VolumeCountingMode | str",
    block_of: Mapping[int, int],
) -> dict[int, GroupContribution]:
    """`{bloco: GroupContribution}` de UM exercicio no modo: os pesos de
    `counted_contributions` levados ao bloco de avaliacao (`block_of`: grupo ->
    bloco; grupo fora dele nao conta). Dois grupos do item no MESMO bloco
    (fatia + pai) contam uma vez, com o maior peso, e sao PRIMARY se algum
    for - na v4 o bloco ja entrava uma vez so. Unica regra de juncao por bloco:
    o motor e o complemento de volume da IA chamam esta."""
    blocks: dict[int, GroupContribution] = {}
    for group_id, contribution in counted_contributions(value, mode).items():
        if group_id not in block_of:
            continue
        block = block_of[group_id]
        current = blocks.get(block)
        weight, primary = (current.weight, current.is_primary) if current is not None else (0, False)
        blocks[block] = GroupContribution(max(weight, contribution.weight), primary or contribution.is_primary)
    return blocks


def block_contributions_by_member(
    value: Optional[ExerciseContribution],
    mode: "VolumeCountingMode | str",
    block_of: Mapping[int, int],
) -> dict[int, GroupContribution]:
    """`block_contributions` entregue num GRUPO-MEMBRO por bloco, para quem
    soma por grupo e junta no bloco depois (realizado de 7 dias): o bloco
    inteiro (maior peso; PRIMARY se algum for) fica no membro do item com o
    maior peso contado (empate: o proprio bloco, senao o menor id); os outros
    membros do mesmo bloco ficam fora. Somar os membros devolve exatamente o
    peso de `block_contributions` - a mesma regua do motor do Modelo."""
    counted = counted_contributions(value, mode)
    blocks = block_contributions(counted, mode, block_of)
    chosen: dict[int, int] = {}
    for group_id, contribution in counted.items():
        if group_id not in block_of:
            continue
        block = block_of[group_id]
        current = chosen.get(block)
        rank = (contribution.weight, group_id == block, -group_id)
        if current is None or rank > (counted[current].weight, current == block, -current):
            chosen[block] = group_id
    return {member: blocks[block] for block, member in chosen.items()}


class _HasSequenceAndSlotType(Protocol):
    @property
    def sequence(self) -> int: ...

    @property
    def slot_type(self) -> Optional[str]: ...


_WindowSlot = TypeVar("_WindowSlot", bound=_HasSequenceAndSlotType)


class MethodologyVolumeStatus(str, Enum):
    WITHIN_RANGE = "WITHIN_RANGE"
    BELOW_RANGE = "BELOW_RANGE"
    ABOVE_RANGE = "ABOVE_RANGE"
    # Musculo zero (EXG-025): terminal e NEUTRO, nunca BELOW_RANGE.
    NOT_DIRECTLY_TRAINED = "NOT_DIRECTLY_TRAINED"
    NOT_EVALUABLE = "NOT_EVALUABLE"


METHODOLOGY_VOLUME_STATUS_CODES: Final[tuple[str, ...]] = tuple(s.value for s in MethodologyVolumeStatus)


class NotEvaluableReason(str, Enum):
    # Grupo sem macro-grupo (`volume_class_id` NULL) - Fase 6; antes era o
    # porte `unclassified`.
    UNCLASSIFIED_GROUP = "UNCLASSIFIED_GROUP"
    MODE_A_NO_EXERCISES = "MODE_A_NO_EXERCISES"
    MODE_A_PERCENT_ONLY = "MODE_A_PERCENT_ONLY"
    NO_TRAINING_SESSIONS = "NO_TRAINING_SESSIONS"
    # Fase 6: o macro-grupo nao tem linha para o bucket de frequencia do grupo
    # (eixos por sessao de fora; o semanal continua).
    NO_RANGE_FOR_FREQUENCY = "NO_RANGE_FOR_FREQUENCY"
    # Fase 6: a linha do bucket nao tem faixa de exercicios (eixo opcional).
    NO_EXERCISES_RANGE = "NO_EXERCISES_RANGE"
    # Fase 6: o macro-grupo nao tem meta semanal (so o eixo semanal de fora).
    NO_WEEKLY_TARGET = "NO_WEEKLY_TARGET"


class MethodologySeverity(str, Enum):
    """Dominio PROPRIO, separado de `dashboard.contracts.Severity`
    (INFO/ATTENTION/ACTION e o vocabulario de insight; aqui e so "isso e um
    aviso" ou "isso e o que voce pediu")."""

    WARNING = "warning"
    INFORMATIONAL = "informational"


class RangeSource(str, Enum):
    METHODOLOGY = "methodology"  # numero do autor, com o EXG-NNN de origem
    DERIVED = "derived"  # numero nosso, com a regra de derivacao escrita
    ADMIN = "admin"  # numero editado na aba admin (Fase 6)


# ---------------------------------------------------------------------------
# Faixas: lidas do cadastro de macro-grupos (Fase 6, migração 057)
# ---------------------------------------------------------------------------


# Conversao "1 exercicio ~ 3-4 series validas" (EXG-004). Nao e mais usada pelo
# motor (as faixas vem prontas do cadastro); continua aqui porque a geração por
# IA a cita no prompt e o validador a usa como default de séries.
_SETS_PER_EXERCISE_MIN: Final[int] = int(threshold("methodology_volume_sets_per_exercise_min"))
_SETS_PER_EXERCISE_MAX: Final[int] = int(threshold("methodology_volume_sets_per_exercise_max"))


@dataclass(frozen=True)
class MethodologyRange:
    """Faixa POR SESSAO de uma linha (macro-grupo, frequencia) do cadastro.

    `*_source` e `*_evidence` sao por eixo porque ha linha em que o autor deu
    o numero de exercicios e nao o de series (abdomen, EXG-020). O eixo de
    exercicios e OPCIONAL (057): sem `exercises_min`/`exercises_max` ele nao e
    avaliado."""

    sets_min: float
    sets_max: float
    sets_source: RangeSource
    sets_evidence: Optional[str] = None
    exercises_min: Optional[int] = None
    exercises_max: Optional[int] = None
    exercises_source: Optional[RangeSource] = None
    exercises_evidence: Optional[str] = None
    # Modo de contagem (073): linha do modo ponderado ainda igual a COPIA da de
    # `primary_only` (`copied_from_counting_mode` preenchido) - "nao calibrada".
    uncalibrated: bool = False

    @property
    def has_exercises_range(self) -> bool:
        return self.exercises_min is not None and self.exercises_max is not None


@dataclass(frozen=True)
class VolumeClassSpec:
    """Um macro-grupo de volume (`muscle_volume_classes` + suas linhas em
    `muscle_volume_class_ranges`), ja reduzido ao que o motor le.

    `ranges` e `bucket -> faixa por sessao`; bucket ausente = linha vazia no
    cadastro (`NO_RANGE_FOR_FREQUENCY`). Meta semanal NULL = so o eixo semanal
    fica de fora."""

    id: int
    name: str
    ranges: Mapping[str, MethodologyRange] = field(default_factory=dict)
    weekly_sets_min: Optional[float] = None
    weekly_sets_max: Optional[float] = None
    weekly_source: Optional[RangeSource] = None
    weekly_evidence: Optional[str] = None
    # Modo de contagem (073): meta semanal ainda igual a copia de `primary_only`.
    weekly_uncalibrated: bool = False

    @property
    def has_weekly_target(self) -> bool:
        return self.weekly_sets_min is not None and self.weekly_sets_max is not None

    def range_for(self, frequency_bucket: Optional[str]) -> Optional[MethodologyRange]:
        if frequency_bucket is None:
            return None
        return self.ranges.get(frequency_bucket)


def frequency_bucket_for(weekly_frequency: float) -> Optional[str]:
    """Frequencia semanal (ja normalizada pelo ciclo) -> coluna da tabela.

    Arredondamento meio-para-cima ao inteiro: 1,56x (2 sessoes num ciclo de 9
    dias) le a coluna 2x; 2,33x (1 sessao num ciclo de 3 dias) tambem. Qualquer
    trabalho direto positivo le no minimo a coluna 1x - a metodologia nao tem
    coluna "meia vez por semana". Zero -> None (musculo zero, EXG-025)."""
    if weekly_frequency <= 0:
        return None
    rounded = int(weekly_frequency + 0.5)
    if rounded <= 1:
        return "1x"
    if rounded == 2:
        return "2x"
    return "3x_plus"


# ---------------------------------------------------------------------------
# Entradas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MethodologyItemInput:
    """Um item de slot, reduzido ao que a metodologia le.

    Modo B (`equipment_series`): `mapping_id` obrigatorio para contar; o grupo
    vem das contribuicoes PRIMARY. Excecao (M101c): o item de exercicio livre
    (`free_exercise=True`, decidido pelo dono `domain/free_exercise_item`)
    conta no grupo PRIMARIO dele (`muscle_group_id`) com o peso
    `FREE_EXERCISE_PRIMARY_WEIGHT`, como um exercicio distinto da sessao.
    Modo A (`percent_budget`): o item JA E o grupo (`muscle_group_id`);
    `sets_count` pode ou nao existir."""

    mapping_id: Optional[int] = None
    canonical_exercise_id: Optional[int] = None
    sets_count: Optional[int] = None
    muscle_group_id: Optional[int] = None
    # v4 (Fase 5, PR 5): equivalente PLANEJADO da tecnica do item, ja
    # calculado pelo chamador (`technique_volume.planned_item_equivalent`).
    # Soma em `direct_sets`; 0 sem tecnica ou sem series.
    technique_equivalent: float = 0.0
    # Posicao 0-based do item na lista `items` do slot RECEBIDO (o corpo do
    # preview). So para ecoar a fatia de cada item (`item_shares`); None = o
    # chamador nao quer a fatia.
    item_index: Optional[int] = None
    # M97 F2: objetivo de treino do item (VALOR do dono `training_goal`;
    # None = default). Serie de forca conta `hypertrophy_volume_weight` no
    # volume desta metodologia (hipertrofica).
    training_goal: Optional[str] = None
    # M101c: item do Modo B sem par ("o motor escolhe o exercicio"); quem
    # decide e o chamador, pelo dono `domain/free_exercise_item`.
    free_exercise: bool = False


@dataclass(frozen=True)
class MethodologySlotInput:
    sequence: int
    slot_type: str = "normal"
    name: str = ""
    items: tuple[MethodologyItemInput, ...] = ()

    @property
    def is_training_session(self) -> bool:
        return is_training_slot_type(self.slot_type)


def is_training_slot_type(slot_type: Optional[str]) -> bool:
    """Slot de treino = qualquer tipo que nao seja descanso."""
    return (slot_type or "").strip().lower() != REST_SLOT_TYPE


@dataclass(frozen=True)
class MethodologyGroupInput:
    """Grupo muscular. `volume_class_id` e o macro-grupo (057); a fatia usa o
    macro-grupo do PAI, porque e avaliada no bloco dele. `size_class` e so
    informativo (deprecated, ecoado no payload por uma versao)."""

    id: int
    name: str
    volume_class_id: Optional[int] = None
    methodology_parent_group_id: Optional[int] = None
    size_class: str = "unclassified"


@dataclass(frozen=True)
class MethodologyIntent:
    kind: str = "standard"
    # Grupos-alvo (ids de grupo; fatia ou pai, ambos resolvem para o bloco).
    # Especializacao exige alvo; manutencao/cutting valem para todos quando
    # vazio.
    target_group_ids: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if self.kind not in INTENT_KINDS:
            raise ValueError(f"intencao desconhecida: {self.kind!r} (esperado um de {INTENT_KINDS})")


# ---------------------------------------------------------------------------
# Saidas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AxisEvaluation:
    """Um eixo de um bloco: exercicios ou series POR SESSAO, ou series
    SEMANAIS. Nos eixos por sessao, `value_per_session` e a media por sessao
    com trabalho direto; no eixo semanal o valor comparado e `value_weekly`
    (`value_per_session` fica None). A faixa comparada vem junto, com a
    origem, para a UI nunca mostrar um numero sem dizer de quem ele e."""

    status: MethodologyVolumeStatus
    reason: Optional[NotEvaluableReason] = None
    severity: Optional[MethodologySeverity] = None
    value_per_session: Optional[float] = None
    range_min: Optional[float] = None
    range_max: Optional[float] = None
    range_source: Optional[RangeSource] = None
    range_evidence: Optional[str] = None
    value_weekly: Optional[float] = None


@dataclass(frozen=True)
class WeeklyProjection:
    """DEPRECATED (Fase 6): substituida por `GroupEvaluation.weekly_sets_axis`,
    que compara com a meta semanal do macro-grupo e gera status. Continua no
    payload por uma versao, com o mesmo calculo, para o frontend atual.

    Coluna SEMANAL - proposta D1, sempre `derived`, NUNCA gera status.

    `direct_sets_weekly` = soma da janela de 7 dias (v3). A faixa semanal e
    a faixa por sessao multiplicada pela frequencia semanal normalizada - o
    documento nao afirma esse total literalmente (EXG-016 diz "por sessao")."""

    direct_sets_weekly: float
    direct_exercises_weekly: float
    range_sets_weekly_min: Optional[float]
    range_sets_weekly_max: Optional[float]
    source: RangeSource = RangeSource.DERIVED
    evidence: str = (
        "derived (proposta D1): faixa por sessao x frequencia semanal normalizada; "
        "o documento prescreve por sessao (EXG-031) e nao afirma o total semanal"
    )


@dataclass(frozen=True)
class SessionDirectWork:
    slot_sequence: int
    slot_name: str
    # v4: inclui o equivalente planejado da tecnica (pode ser fracionario).
    direct_sets: float
    direct_exercises: int
    # v4: parcela de `direct_sets` que vem de tecnica (0 sem tecnica).
    technique_equivalent: float = 0.0


@dataclass(frozen=True)
class ItemGroupShare:
    """Quanto UM item soma num bloco, no modo do Modelo: `direct_sets` =
    (`sets_count` + equivalente da tecnica) x peso do modo (1 em
    `primary_only`; o `stimulus_weight` cadastrado no ponderado)."""

    muscle_group_id: int  # o bloco (o pai, quando o grupo do item e fatia)
    muscle_group_name: str
    direct_sets: float
    technique_equivalent: float
    weight: float
    is_primary: bool
    # Grupo do item que caiu no bloco, quando e fatia dele (None = o proprio).
    slice_group_id: Optional[int] = None
    slice_group_name: Optional[str] = None


@dataclass(frozen=True)
class ItemDirectShare:
    """Fatia de um item (Modo B) por bloco: o card do builder mostra
    "este exercicio / este dia / o Modelo" sem recalcular nada. O eco
    (`slot_sequence`, `item_index`, `item_kind`, `mapping_id`,
    `muscle_group_id`) e o mesmo das listas por item do preview de ativacao."""

    slot_sequence: int
    item_index: int
    mapping_id: Optional[int]
    muscle_group_id: Optional[int]
    groups: tuple[ItemGroupShare, ...]
    item_kind: str = "equipment"


@dataclass(frozen=True)
class GroupEvaluation:
    muscle_group_id: int
    muscle_group_name: str
    # Macro-grupo do bloco (o do grupo pai; None = sem macro-grupo).
    volume_class_id: Optional[int]
    volume_class_name: Optional[str]
    # DEPRECATED (Fase 6): porte da 055, so ecoado. O motor le o macro-grupo.
    size_class: str
    # Fatias somadas neste bloco (inclui o proprio id).
    member_group_ids: tuple[int, ...]
    # v3: sessoes DA JANELA de 7 dias com trabalho direto (um slot que a
    # janela visita duas vezes conta duas). `weekly_frequency` e o mesmo
    # numero em float (sem a escala `7/cycle_days` da v2).
    frequency_raw: int
    weekly_frequency: float
    # True quando o ciclo nao tem 7 slots (a janela da a volta ou corta).
    frequency_is_normalized: bool
    frequency_bucket: Optional[str]
    # Totais do CICLO (cada slot uma vez, como `sessions`): o card do builder
    # (M43) mostra "series no Modelo". A avaliacao usa a janela.
    direct_sets_cycle_total: float
    direct_exercises_cycle_total: int
    # v3: medias das sessoes com trabalho direto DENTRO da janela.
    direct_sets_per_session: Optional[float]
    direct_exercises_per_session: Optional[float]
    direct_exercises_axis: AxisEvaluation
    direct_sets_axis: AxisEvaluation
    # Fase 6: series diretas da SEMANA contra a meta semanal do macro-grupo,
    # com o mesmo `_compare` seco dos eixos por sessao. v3: soma da janela de
    # 7 dias (antes, `direct_sets_cycle_total x 7/cycle_days`).
    weekly_sets_axis: AxisEvaluation
    # Fase 6: status COMBINADO dos eixos avaliados (ver `_combine`).
    status: MethodologyVolumeStatus
    severity: Optional[MethodologySeverity]
    # Um eixo BELOW e outro ABOVE: o combinado e BELOW e este sinal fica True
    # (metas incoerentes entre si, ou volume mal distribuido no ciclo).
    status_conflict: bool
    # Motivo quando o combinado e NOT_EVALUABLE (o do primeiro eixo com motivo).
    status_reason: Optional[NotEvaluableReason]
    # DEPRECATED (Fase 6): substituido por `weekly_sets_axis`.
    weekly_projection: Optional[WeeklyProjection]
    is_intent_target: bool
    # Sessoes do CICLO com trabalho direto (cada slot uma vez, inclusive os
    # que ficam fora da janela). Numa janela que da a volta, a media pondera
    # cada sessao pelo numero de vezes que ela cai na janela.
    sessions: tuple[SessionDirectWork, ...] = ()
    # v4: parcela de `direct_sets_cycle_total` que vem de tecnica.
    technique_equivalent_cycle_total: float = 0.0
    # v5 (modo de contagem): alguma faixa/meta USADA na avaliacao do bloco e
    # ainda a copia nao calibrada de `primary_only`.
    uncalibrated_ranges: bool = False


@dataclass(frozen=True)
class MethodologyDataQuality:
    """Contadores das falhas SILENCIOSAS mais provaveis (plano, "Riscos")."""

    # Modo B: item com `mapping_id` sem NENHUMA contribuicao PRIMARY ativa -
    # invisivel para a metodologia; o grupo parece subtreinado sem explicacao.
    items_without_primary_contribution: int = 0
    # Item direto sem canonico: contado pelo `mapping_id` (pode inflar
    # `direct_exercises` quando o mesmo exercicio existe em duas academias).
    items_without_canonical: int = 0
    # Modo B: item sem `mapping_id` (cardio, item de grupo fora do modo) -
    # nao ha como ligar a um grupo; ignorado e contado.
    items_without_mapping: int = 0
    # Item direto com `sets_count` ausente: entra no eixo de exercicios com 0
    # series (Modo B) ou nao entra em nada (Modo A).
    items_without_sets_count: int = 0
    # Fatia cujo pai nao veio na lista de grupos: avaliada como bloco proprio.
    groups_with_unknown_parent: int = 0


@dataclass(frozen=True)
class MethodologyVolumeEvaluation:
    metric: str
    metric_version: str
    volume_mode: str
    intent: MethodologyIntent
    cycle_days: int  # len(slots) - so descreve o ciclo
    training_sessions: int  # slots de treino do ciclo (cada um uma vez)
    # v3: sempre 1.0 com slots (a janela ja tem 7 dias; nao ha escala) e 0.0
    # sem slots. Mantido pelo contrato do payload.
    weekly_factor: float
    # True quando o ciclo nao tem 7 slots: a janela da a volta ou corta.
    frequency_is_normalized: bool
    groups: tuple[GroupEvaluation, ...]
    data_quality: MethodologyDataQuality = field(default_factory=MethodologyDataQuality)
    # v3: janela de avaliacao - dias e `sequence` do slot em que ela comeca
    # (o primeiro de treino); None quando o ciclo nao tem slot de treino.
    window_days: int = 7
    window_start_sequence: Optional[int] = None
    # v5: modo de contagem em que o Modelo foi avaliado (valor do enum).
    counting_mode: str = DEFAULT_VOLUME_COUNTING_MODE.value
    # v5: unidade do numero contado neste modo (`COUNTED_SETS_UNIT_LABELS`).
    sets_unit_label: str = COUNTED_SETS_UNIT_LABELS[DEFAULT_VOLUME_COUNTING_MODE]
    # v5 (Lote 3): fatia de cada item por bloco (Modo B, so itens com
    # `item_index`): o card do builder so exibe.
    item_shares: tuple[ItemDirectShare, ...] = ()


# ---------------------------------------------------------------------------
# Janela de avaliacao (v3)
# ---------------------------------------------------------------------------

EVALUATION_WINDOW_DAYS: Final[int] = 7


def evaluation_window(
    slots: tuple[MethodologySlotInput, ...] | list[MethodologySlotInput],
    days: int = EVALUATION_WINDOW_DAYS,
) -> tuple[Optional[int], dict[int, int]]:
    """Janela de `days` dias (um slot = um dia) a partir do PRIMEIRO slot de
    treino, na ordem de `sequence`, dando a volta no ciclo quando ele tem
    menos slots que a janela e cortando o que passa do ultimo dia.

    Devolve `(sequence do slot inicial, sequence -> vezes que o slot de
    TREINO cai na janela)`. Descanso ocupa o dia mas nao entra no mapa. Sem
    slot de treino: `(None, {})`."""
    visited = evaluation_window_days(slots, days)
    if not visited:
        return None, {}
    occurrences: dict[int, int] = {}
    for slot in visited:
        if slot.is_training_session:
            occurrences[slot.sequence] = occurrences.get(slot.sequence, 0) + 1
    return visited[0].sequence, occurrences


def evaluation_window_days(
    slots: Sequence[_WindowSlot],
    days: int = EVALUATION_WINDOW_DAYS,
) -> list[_WindowSlot]:
    """Os slots da janela de avaliacao, dia a dia (descanso incluido): a
    mesma regra de `evaluation_window`, devolvendo o slot de cada dia em vez
    da contagem. Aceita qualquer objeto com `sequence` e `slot_type` (o
    preview de ativacao passa os slots do payload). Sem slot de treino: []."""
    ordered = sorted(slots, key=lambda s: s.sequence)
    start = next((i for i, s in enumerate(ordered) if is_training_slot_type(s.slot_type)), None)
    if start is None:
        return []
    return [ordered[(start + day) % len(ordered)] for day in range(days)]


# ---------------------------------------------------------------------------
# Normalizacao semanal (M76 Lote 4a, E1) - dono unico
# ---------------------------------------------------------------------------
#
# Regra do usuario (Fase 7, 2026-09-29): a semana de um Modelo e SEMPRE a
# janela de `EVALUATION_WINDOW_DAYS` dias a partir do 1o slot de treino - ciclo
# maior corta o excedente, ciclo menor da a volta. Nunca `x 7/ciclo` nem
# `max(7, ciclo)`. Builder, preview de ativacao, dashboard e geracao Modo A
# leem o "planejado da semana" daqui.

_Key = TypeVar("_Key")


def weekly_normalization(
    slots: Sequence[_HasSequenceAndSlotType],
    values_by_sequence: Mapping[int, Mapping[_Key, float]],
    days: int = EVALUATION_WINDOW_DAYS,
) -> dict[_Key, float]:
    """Valores POR SLOT do ciclo -> o PLANEJADO DA SEMANA, por chave (grupo).

    `slots`: todos os slots do ciclo (descanso incluido - ele ocupa o dia).
    `values_by_sequence`: `sequence -> chave -> valor` de UMA ocorrencia de
    cada slot (ex.: ativacao planejada por grupo). Cada slot de treino soma
    tantas vezes quantas cai na janela de `evaluation_window_days`; slot de
    descanso nunca soma. Sem slot de treino: `{}`. Chaves com total zero
    ficam de fora."""
    totals: dict[_Key, float] = {}
    for slot in evaluation_window_days(slots, days):
        if not is_training_slot_type(slot.slot_type):
            continue
        for key, value in (values_by_sequence.get(slot.sequence) or {}).items():
            totals[key] = totals.get(key, 0.0) + float(value or 0.0)
    return {key: value for key, value in totals.items() if value > 0.0}


def weekly_window_days() -> int:
    """Dias da janela semanal de qualquer avaliacao contra meta semanal -
    inclusive a janela movel da geracao Modo A (`activation_window`), que
    antes alargava para `max(7, ciclo)` e escalava a meta por `ciclo/7`."""
    return EVALUATION_WINDOW_DAYS


def calendar_weekly_rate(total: Optional[float], window_days: int) -> Optional[float]:
    """Total acumulado em `window_days` dias de CALENDARIO -> taxa semanal
    equivalente (`total x 7 / dias`). Serve ao REALIZADO numa janela corrida
    (28 dias de series contra uma meta de 7 dias); o planejado de um Modelo
    usa `weekly_normalization`, nunca esta conta. Sem total ou sem dias:
    `None`. Sem arredondamento: cada tela arredonda como sempre fez."""
    if total is None or not window_days or window_days <= 0:
        return None
    return float(total) * EVALUATION_WINDOW_DAYS / float(window_days)


def block_of_group(group_id: int, groups_by_id: Mapping[int, "MethodologyGroupInput"]) -> int:
    """Fatia -> bloco, a MESMA cadeia do motor (`_resolve_block`): sobe pelos
    `methodology_parent_group_id` ate a raiz, com guarda contra ciclo. Aceita
    qualquer objeto com `methodology_parent_group_id` (o relatorio passa as
    linhas de `load_methodology_groups`)."""
    return _resolve_block(group_id, groups_by_id)[0]


def block_volume_class_id(group_id: int, groups_by_id: Mapping[int, "MethodologyGroupInput"]) -> Optional[int]:
    """Macro-grupo (`volume_class_id`) com que o motor avalia o grupo: o do
    BLOCO raiz, nunca o da fatia (a fatia e somada no pai e usa a faixa dele)."""
    if group_id not in groups_by_id:
        return None
    block = groups_by_id[block_of_group(group_id, groups_by_id)]
    class_id = getattr(block, "volume_class_id", None)
    return int(class_id) if class_id is not None else None


# Rotulo do balde de quem nao tem macro-grupo resolvido: bloco sem
# `volume_class_id`, classe fora do cadastro ou grupo fora de `muscle_groups`
# (o "Outros" das leituras de volume). Unico lugar: relatorio e telas so exibem.
VOLUME_CLASS_UNCLASSIFIED_LABEL: Final[str] = "Sem macro-grupo"


@dataclass(frozen=True)
class GroupHierarchy:
    """Onde um grupo muscular cai em cada nivel de agrupamento das telas de
    volume: o proprio grupo, o grupo PAI (o bloco da metodologia: a fatia soma
    no pai; grupo sem pai e o proprio) e o MACRO-GRUPO (classe de volume da 057
    do bloco; a fatia herda a do pai). `volume_class_order` e a posicao da
    classe na ordem do cadastro (`sort_order`), `None` no balde
    `VOLUME_CLASS_UNCLASSIFIED_LABEL`, que fica por ultimo."""

    group_id: Optional[int]
    group_name: str
    parent_group_id: Optional[int]
    parent_group_name: str
    volume_class_id: Optional[int]
    volume_class_name: str
    volume_class_order: Optional[int]


def resolve_group_hierarchy(
    groups: Iterable[Any], volume_class_names: Mapping[int, str]
) -> dict[int, GroupHierarchy]:
    """`muscle_group_id -> GroupHierarchy` de todos os grupos do cadastro.

    Pai = `block_of_group` (a cadeia inteira de `methodology_parent_group_id`,
    a mesma do motor); macro-grupo = `block_volume_class_id` (o do bloco, nunca
    o da fatia). `volume_class_names` e `classe -> nome` NA ORDEM DO CADASTRO
    (o loader le por `sort_order, id`); classe sem nome ou ausente dele = o
    balde `VOLUME_CLASS_UNCLASSIFIED_LABEL`. Aceita qualquer objeto com `id`,
    `methodology_parent_group_id` e `volume_class_id` (`name` opcional: o
    relatorio so le o macro-grupo)."""
    by_id = {int(group.id): group for group in groups}
    order = {int(class_id): index for index, class_id in enumerate(volume_class_names)}
    out: dict[int, GroupHierarchy] = {}
    for group_id, group in by_id.items():
        parent_id = block_of_group(group_id, by_id)
        class_id = block_volume_class_id(group_id, by_id)
        class_name = volume_class_names.get(class_id) if class_id is not None else None
        resolved = bool(class_name)
        out[group_id] = GroupHierarchy(
            group_id=group_id,
            group_name=str(getattr(group, "name", None) or ""),
            parent_group_id=parent_id,
            parent_group_name=str(getattr(by_id[parent_id], "name", None) or ""),
            volume_class_id=class_id if resolved else None,
            volume_class_name=str(class_name) if resolved else VOLUME_CLASS_UNCLASSIFIED_LABEL,
            volume_class_order=order[class_id] if resolved else None,
        )
    return out


def group_name_key(name: Any) -> str:
    """Chave de comparacao do NOME de um grupo muscular: sem espacos nas pontas
    e sem caixa, como a collation `_ai_ci` do banco. Unico lugar: o realizado
    (`methodology_realized`) e a hierarquia das telas de volume usam esta."""
    return str(name or "").strip().casefold()


def index_hierarchy_by_name(hierarchy: Mapping[int, GroupHierarchy]) -> dict[str, GroupHierarchy]:
    """`resolve_group_hierarchy` indexado pelo NOME do grupo
    (`muscle_groups.name` e UNIQUE), para as leituras que entregam o nome."""
    return {group_name_key(item.group_name): item for item in hierarchy.values()}


def hierarchy_of_group_name(group_name: str, by_name: Mapping[str, GroupHierarchy]) -> GroupHierarchy:
    """Hierarquia de um grupo pelo NOME, como as leituras de volume o entregam
    (`by_name` de `index_hierarchy_by_name`). Nome fora do cadastro (o "Outros"
    de exercicio sem contribuicao) e bloco proprio, sem id, no balde
    `VOLUME_CLASS_UNCLASSIFIED_LABEL` - a regra do realizado para grupo fora do
    cadastro."""
    found = by_name.get(group_name_key(group_name))
    if found is not None:
        return found
    return GroupHierarchy(
        group_id=None,
        group_name=group_name,
        parent_group_id=None,
        parent_group_name=group_name,
        volume_class_id=None,
        volume_class_name=VOLUME_CLASS_UNCLASSIFIED_LABEL,
        volume_class_order=None,
    )


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------


def _resolve_block(group_id: int, by_id: Mapping[int, MethodologyGroupInput]) -> tuple[int, bool]:
    """Fatia -> bloco (segue a cadeia de pais com guarda contra ciclo).
    Devolve (id do bloco, pai_desconhecido)."""
    seen: set[int] = set()
    current = group_id
    while True:
        seen.add(current)
        group = by_id[current]
        parent = group.methodology_parent_group_id
        if parent is None or parent == current:
            return current, False
        if parent not in by_id:
            return current, True
        if parent in seen:  # ciclo de cadastro: para no ultimo visitado
            return current, False
        current = parent


def _compare(value: float, low: int, high: int) -> MethodologyVolumeStatus:
    # Sem tolerancia: a faixa ja e o intervalo (ver docstring do modulo).
    if value < low:
        return MethodologyVolumeStatus.BELOW_RANGE
    if value > high:
        return MethodologyVolumeStatus.ABOVE_RANGE
    return MethodologyVolumeStatus.WITHIN_RANGE


def _severity_for(
    status: MethodologyVolumeStatus, intent: MethodologyIntent, is_target: bool
) -> Optional[MethodologySeverity]:
    """Intencao troca a severidade, nunca o status.

    - specialization: alvo ACIMA e o esperado (EXG-022/024) -> informational;
      alvo ABAIXO continua warning; os demais estao em manutencao, entao
      ABAIXO neles e o esperado (EXG-022 "demais em manutencao").
    - maintenance / cutting: ABAIXO e o esperado (EXG-022; EXG-030 reduz
      series no cutting) para os alvos - ou para todos, sem alvo declarado.
      Sem piso novo (D5): cutting nao ganha numero, so severidade.
    - standard: tudo warning."""
    if status not in (MethodologyVolumeStatus.BELOW_RANGE, MethodologyVolumeStatus.ABOVE_RANGE):
        return None
    below = status is MethodologyVolumeStatus.BELOW_RANGE
    kind = intent.kind
    if kind == "specialization":
        if is_target:
            expected = not below
        else:
            expected = below
    elif kind in ("maintenance", "cutting"):
        applies = is_target or not intent.target_group_ids
        expected = below and applies
    else:
        expected = False
    return MethodologySeverity.INFORMATIONAL if expected else MethodologySeverity.WARNING


def _not_evaluable(reason: NotEvaluableReason, value: Optional[float] = None) -> AxisEvaluation:
    return AxisEvaluation(status=MethodologyVolumeStatus.NOT_EVALUABLE, reason=reason, value_per_session=value)


def _not_evaluable_weekly(reason: NotEvaluableReason, value: Optional[float] = None) -> AxisEvaluation:
    return AxisEvaluation(status=MethodologyVolumeStatus.NOT_EVALUABLE, reason=reason, value_weekly=value)


_NOT_TRAINED = AxisEvaluation(status=MethodologyVolumeStatus.NOT_DIRECTLY_TRAINED)


def _axis(
    value: float,
    low: float,
    high: float,
    source: Optional[RangeSource],
    evidence: Optional[str],
    intent: MethodologyIntent,
    is_target: bool,
    *,
    weekly: bool = False,
) -> AxisEvaluation:
    status = _compare(value, low, high)
    return AxisEvaluation(
        status=status,
        severity=_severity_for(status, intent, is_target),
        value_per_session=None if weekly else value,
        value_weekly=value if weekly else None,
        range_min=low,
        range_max=high,
        range_source=source,
        range_evidence=evidence,
    )


_COMPARED: Final[frozenset[MethodologyVolumeStatus]] = frozenset(
    {
        MethodologyVolumeStatus.WITHIN_RANGE,
        MethodologyVolumeStatus.BELOW_RANGE,
        MethodologyVolumeStatus.ABOVE_RANGE,
    }
)


def _combine(
    axes: tuple[AxisEvaluation, ...],
) -> tuple[MethodologyVolumeStatus, Optional[MethodologySeverity], bool, Optional[NotEvaluableReason]]:
    """Status COMBINADO do grupo a partir dos eixos (exercicios, series,
    semanal). Devolve (status, severidade, conflito, motivo).

    - WITHIN so quando TODOS os eixos avaliados estao WITHIN;
    - algum BELOW ou ABOVE: vale esse; BELOW num eixo e ABOVE em outro ->
      BELOW com `status_conflict=True` (o sinal de metas incoerentes);
    - nenhum eixo avaliado: NOT_DIRECTLY_TRAINED quando algum eixo diz que o
      grupo nao foi treinado (EXG-025, neutro); senao NOT_EVALUABLE, com o
      motivo do primeiro eixo que tem motivo.

    A severidade e a dos eixos com o status escolhido: `warning` se algum
    deles for warning (a intencao so rebaixa quando todos os desvios daquele
    lado sao os esperados)."""
    compared = [a for a in axes if a.status in _COMPARED]
    if not compared:
        if any(a.status is MethodologyVolumeStatus.NOT_DIRECTLY_TRAINED for a in axes):
            return MethodologyVolumeStatus.NOT_DIRECTLY_TRAINED, None, False, None
        reason = next((a.reason for a in axes if a.reason is not None), None)
        return MethodologyVolumeStatus.NOT_EVALUABLE, None, False, reason
    below = [a for a in compared if a.status is MethodologyVolumeStatus.BELOW_RANGE]
    above = [a for a in compared if a.status is MethodologyVolumeStatus.ABOVE_RANGE]
    if below:
        chosen, status = below, MethodologyVolumeStatus.BELOW_RANGE
    elif above:
        chosen, status = above, MethodologyVolumeStatus.ABOVE_RANGE
    else:
        return MethodologyVolumeStatus.WITHIN_RANGE, None, False, None
    severity = (
        MethodologySeverity.WARNING
        if any(a.severity is MethodologySeverity.WARNING for a in chosen)
        else MethodologySeverity.INFORMATIONAL
    )
    return status, severity, bool(below and above), None


def _technique_of(item: MethodologyItemInput) -> float:
    return max(0.0, float(item.technique_equivalent or 0.0))


def _num(value: float) -> float | int:
    """Soma de series (v4 pode ser fracionaria): 2 casas, e inteiro quando
    exato - Modelo sem tecnica serializa `12`, como na v3."""
    rounded = round(float(value), SETS_DECIMAL_PLACES)
    return int(rounded) if rounded == int(rounded) else rounded


def _item_group_share(
    block: int,
    weight: float,
    primary: bool,
    sets_count: int,
    technique: float,
    source_group: int,
    by_id: Mapping[int, MethodologyGroupInput],
) -> ItemGroupShare:
    """A mesma conta do `_add` do bloco, para UM item (o card nunca a refaz)."""
    is_slice = source_group != block
    return ItemGroupShare(
        muscle_group_id=block,
        muscle_group_name=by_id[block].name,
        direct_sets=_num((sets_count + technique) * weight),
        technique_equivalent=_num(technique * weight),
        weight=_num(weight),
        is_primary=primary,
        slice_group_id=source_group if is_slice else None,
        slice_group_name=by_id[source_group].name if is_slice and source_group in by_id else None,
    )


def evaluate_methodology_volume(
    slots: tuple[MethodologySlotInput, ...] | list[MethodologySlotInput],
    contributions: VolumeContributions,
    groups: tuple[MethodologyGroupInput, ...] | list[MethodologyGroupInput],
    volume_mode: str,
    intent: Optional[MethodologyIntent] = None,
    volume_classes: Optional[Mapping[int, VolumeClassSpec]] = None,
    counting_mode: VolumeCountingMode | str = DEFAULT_VOLUME_COUNTING_MODE,
) -> MethodologyVolumeEvaluation:
    """Avalia um Modelo pela metodologia. Puro: mesma entrada, mesma saida.

    `contributions`: `mapping_id -> {grupo: GroupContribution}` das
    contribuicoes ATIVAS dos papeis do modo (`COUNTED_ROLES_BY_MODE`; o
    chamador ja filtrou, ver `methodology_volume_service.load_volume_contributions`).
    O peso sai de `counted_contributions`: 1 em `PRIMARY_ONLY`, o
    `stimulus_weight` cadastrado em `PRIMARY_SECONDARY` (primario inclusive).
    Um bloco por grupo sem pai; fatias somadas no pai; grupos recebidos e
    nunca tocados aparecem como NOT_DIRECTLY_TRAINED (ou NOT_EVALUABLE quando
    sem macro-grupo).

    `counting_mode`: em `PRIMARY_SECONDARY` as series e o equivalente de
    tecnica entram x peso, primario e secundario; as fracoes se somam entre
    exercicios e contam como estao, sem virar serie inteira (`_num` so corta
    a 2 casas, como na v4). Regra do usuario (2026-10-16): a sessao conta na
    frequencia (bucket 1x/2x/3x+) do grupo se tem trabalho PRIMARY nele OU se
    as fracoes de SECONDARY do grupo naquela sessao somam ao menos
    `SECONDARY_SESSION_MIN_SETS`; `direct_exercises` so conta PRIMARY. A
    media por sessao divide as series da janela (fracoes inclusive) pelas
    sessoes que contam. Grupo sem nenhuma sessao que conte fica
    NOT_DIRECTLY_TRAINED, com as fracoes nas sessoes do payload. Em
    `PRIMARY_ONLY` a saida e a da v4, bit a bit.

    `volume_classes`: `id do macro-grupo -> faixas` (cadastro da 057). O bloco
    usa o macro-grupo do grupo PAI; `volume_class_id` que nao esta no mapa e
    tratado como grupo sem macro-grupo. Sem o mapa, nenhum grupo e avaliado.
    Desde o Lote 2 (073) cada modo tem linhas PROPRIAS no cadastro; o motor
    recebe as `VolumeClassSpec` JA DO MODO pedido - quem escolhe as faixas
    pelo modo e o loader (`load_volume_classes`), nunca esta funcao."""
    if volume_mode not in ("equipment_series", "percent_budget"):
        raise ValueError(f"volume_mode desconhecido: {volume_mode!r}")
    counting_mode = VolumeCountingMode(counting_mode)
    intent = intent or MethodologyIntent()
    slots = tuple(slots)
    by_id: dict[int, MethodologyGroupInput] = {g.id: g for g in groups}

    cycle_days = len(slots)
    training = [s for s in slots if s.is_training_session]
    # v3: sem escala - a janela de 7 dias ja e a semana (1.0 so por contrato).
    weekly_factor = 1.0 if cycle_days else 0.0
    normalized = cycle_days != EVALUATION_WINDOW_DAYS
    window_start, window_hits = evaluation_window(slots)

    # --- bloco de cada grupo -------------------------------------------------
    block_of: dict[int, int] = {}
    unknown_parent = 0
    for gid in by_id:
        block, missing = _resolve_block(gid, by_id)
        block_of[gid] = block
        unknown_parent += int(missing)
    members: dict[int, list[int]] = {}
    for gid, block in block_of.items():
        members.setdefault(block, []).append(gid)
    target_blocks = {block_of[t] for t in intent.target_group_ids if t in block_of}

    # --- trabalho direto por sessao e por bloco ------------------------------
    is_mode_a = volume_mode == "percent_budget"
    dq_no_primary = dq_no_canonical = dq_no_mapping = dq_no_sets = 0
    mode_a_has_sets = False
    # block -> sequence -> (sets, {exercise keys}, equivalente de tecnica)
    per_session: dict[int, dict[int, tuple[float, set[tuple[str, int]], float]]] = {}
    # block -> sequence -> series (com tecnica) vindas SO de SECONDARY
    # (`PRIMARY_SECONDARY`); decide se a sessao conta na frequencia.
    secondary_sets: dict[int, dict[int, float]] = {}
    item_shares: list[ItemDirectShare] = []
    # M101c: chave de exercicio de cada item livre (um exercicio distinto por
    # item: a geracao nunca repete exercicio no slot).
    free_exercise_keys = 0

    def _add(
        block: int,
        seq: int,
        sets_count: float,
        exercise_key: Optional[tuple[str, int]],
        technique: float = 0.0,
    ) -> None:
        current_sets, keys, current_technique = per_session.setdefault(block, {}).get(seq, (0, set(), 0.0))
        if exercise_key is not None:
            keys = set(keys)
            keys.add(exercise_key)
        per_session[block][seq] = (current_sets + sets_count + technique, keys, current_technique + technique)

    for slot in training:
        for item in slot.items:
            if is_mode_a:
                if item.muscle_group_id is None or item.muscle_group_id not in block_of:
                    continue
                if item.sets_count is None:
                    dq_no_sets += 1
                    _add(block_of[item.muscle_group_id], slot.sequence, 0, None)
                    continue
                mode_a_has_sets = True
                goal_weight = hypertrophy_volume_weight(item.training_goal)
                _add(
                    block_of[item.muscle_group_id],
                    slot.sequence,
                    int(item.sets_count) if goal_weight == 1.0 else int(item.sets_count) * goal_weight,
                    None,
                    _technique_of(item) if goal_weight == 1.0 else _technique_of(item) * goal_weight,
                )
                continue
            if item.free_exercise:
                if item.muscle_group_id is None or item.muscle_group_id not in block_of:
                    continue
                block = block_of[item.muscle_group_id]
                weight = FREE_EXERCISE_PRIMARY_WEIGHT * hypertrophy_volume_weight(item.training_goal)
                if item.sets_count is None:
                    dq_no_sets += 1
                sets_count = int(item.sets_count or 0)
                technique = _technique_of(item) if sets_count > 0 else 0.0
                free_exercise_keys += 1
                _add(block, slot.sequence, sets_count * weight, ("free_exercise", free_exercise_keys), technique * weight)
                if item.item_index is not None:
                    item_shares.append(
                        ItemDirectShare(
                            slot_sequence=slot.sequence,
                            item_index=int(item.item_index),
                            mapping_id=None,
                            muscle_group_id=item.muscle_group_id,
                            groups=(
                                _item_group_share(
                                    block, weight, True, sets_count, technique, int(item.muscle_group_id), by_id
                                ),
                            ),
                        )
                    )
                continue
            if item.mapping_id is None:
                dq_no_mapping += 1
                continue
            # Bloco -> (peso do item nele, se ha PRIMARY nele): `block_contributions`.
            blocks: dict[int, tuple[float | int, bool]] = {
                block: (c.weight, c.is_primary)
                for block, c in block_contributions(contributions.get(item.mapping_id), counting_mode, block_of).items()
            }
            # Lote 3 (eco por item): o grupo do item que deu o peso do bloco -
            # a fatia, quando nao e o proprio bloco. So identifica; o peso e
            # o de `block_contributions`.
            source_group: dict[int, int] = {}
            for group_id, contribution in counted_contributions(contributions.get(item.mapping_id), counting_mode).items():
                block = block_of.get(group_id)
                if block is None or block not in blocks or contribution.weight != blocks[block][0]:
                    continue
                if group_id == block or block not in source_group:
                    source_group[block] = group_id
            # M97 F2: serie de forca no peso do volume hipertrofico (o peso do
            # bloco ja escalado: soma, sessao e fatia do item saem da MESMA
            # conta). Hipertrofia: peso 1, `blocks` intocado.
            goal_weight = hypertrophy_volume_weight(item.training_goal)
            if goal_weight != 1.0:
                blocks = {block: (weight * goal_weight, primary) for block, (weight, primary) in blocks.items()}
            has_primary = any(primary for _w, primary in blocks.values())
            if not has_primary:
                dq_no_primary += 1
            if not blocks:
                continue
            key: Optional[tuple[str, int]] = None
            if has_primary:
                if item.canonical_exercise_id is not None:
                    key = ("canonical", int(item.canonical_exercise_id))
                else:
                    key = ("mapping", int(item.mapping_id))
                    dq_no_canonical += 1
            if item.sets_count is None:
                dq_no_sets += 1
            sets_count = int(item.sets_count or 0)
            technique = _technique_of(item) if sets_count > 0 else 0.0
            for block, (weight, primary) in blocks.items():
                # SECONDARY soma fracao de serie sem chave de exercicio (nunca
                # conta em `direct_exercises`); a fracao vai tambem para o
                # acumulador da sessao que decide a frequencia.
                _add(block, slot.sequence, sets_count * weight, key if primary else None, technique * weight)
                if not primary:
                    by_seq = secondary_sets.setdefault(block, {})
                    by_seq[slot.sequence] = by_seq.get(slot.sequence, 0.0) + (sets_count + technique) * weight
            if item.item_index is not None:
                item_shares.append(
                    ItemDirectShare(
                        slot_sequence=slot.sequence,
                        item_index=int(item.item_index),
                        mapping_id=int(item.mapping_id),
                        muscle_group_id=item.muscle_group_id,
                        groups=tuple(
                            _item_group_share(block, weight, primary, sets_count, technique, source_group[block], by_id)
                            for block, (weight, primary) in sorted(blocks.items())
                        ),
                    )
                )

    slot_name = {s.sequence: s.name for s in slots}
    no_sessions = not training

    # --- avaliacao por bloco --------------------------------------------------
    evaluations: list[GroupEvaluation] = []
    for block_id in sorted(members):
        group = by_id[block_id]
        is_target = block_id in target_blocks
        sessions_raw = per_session.get(block_id, {})
        sessions = tuple(
            SessionDirectWork(seq, slot_name.get(seq, ""), _num(sets_count), len(keys), _num(technique))
            for seq, (sets_count, keys, technique) in sorted(sessions_raw.items())
            if sets_count > 0 or keys
        )
        # Totais do ciclo (payload/M43) e da janela de 7 dias (avaliacao, v3).
        sets_total = _num(sum(s.direct_sets for s in sessions))
        technique_total = _num(sum(s.technique_equivalent for s in sessions))
        exercises_total = sum(s.direct_exercises for s in sessions)
        # Frequencia: a sessao conta para o grupo se tem trabalho PRIMARY nele
        # (Modo B: exercicio direto; Modo A nao tem exercicio nem SECONDARY)
        # ou se as fracoes de SECONDARY do grupo NAQUELA sessao somam ao menos
        # `SECONDARY_SESSION_MIN_SETS`. Fracoes de sessoes diferentes somam
        # series na semana, mas nao viram sessao.
        block_secondary = secondary_sets.get(block_id, {})
        frequency_raw = sum(
            window_hits.get(s.slot_sequence, 0)
            for s in sessions
            if is_mode_a or counts_as_frequency_session(s.direct_exercises > 0, block_secondary.get(s.slot_sequence, 0.0))
        )
        weekly_frequency = float(frequency_raw)
        bucket = frequency_bucket_for(weekly_frequency)
        window_sets = _num(sum(s.direct_sets * window_hits.get(s.slot_sequence, 0) for s in sessions))
        window_exercises = sum(s.direct_exercises * window_hits.get(s.slot_sequence, 0) for s in sessions)
        sets_avg = round(window_sets / frequency_raw, 2) if frequency_raw else None
        exercises_avg = round(window_exercises / frequency_raw, 2) if frequency_raw else None

        spec = (volume_classes or {}).get(group.volume_class_id) if group.volume_class_id is not None else None
        rng = spec.range_for(bucket) if spec is not None else None
        sets_weekly = float(window_sets) if frequency_raw else None
        weekly: Optional[WeeklyProjection] = None
        if frequency_raw:
            weekly = WeeklyProjection(
                direct_sets_weekly=float(window_sets),
                direct_exercises_weekly=float(window_exercises),
                range_sets_weekly_min=round(rng.sets_min * weekly_frequency, 2) if rng else None,
                range_sets_weekly_max=round(rng.sets_max * weekly_frequency, 2) if rng else None,
            )

        # Precedencia: motivos do MODELO inteiro (sem sessao; Modo A so com
        # percentuais) antes dos motivos do GRUPO (sem macro-grupo), e so
        # depois a comparacao numerica.
        if no_sessions:
            ex_axis = _not_evaluable(NotEvaluableReason.NO_TRAINING_SESSIONS)
            sets_axis = _not_evaluable(NotEvaluableReason.NO_TRAINING_SESSIONS)
            weekly_axis = _not_evaluable_weekly(NotEvaluableReason.NO_TRAINING_SESSIONS)
        elif is_mode_a and not mode_a_has_sets:
            ex_axis = _not_evaluable(NotEvaluableReason.MODE_A_PERCENT_ONLY)
            sets_axis = _not_evaluable(NotEvaluableReason.MODE_A_PERCENT_ONLY)
            weekly_axis = _not_evaluable_weekly(NotEvaluableReason.MODE_A_PERCENT_ONLY)
        elif spec is None:
            ex_axis = _not_evaluable(NotEvaluableReason.UNCLASSIFIED_GROUP, exercises_avg)
            sets_axis = _not_evaluable(NotEvaluableReason.UNCLASSIFIED_GROUP, sets_avg)
            weekly_axis = _not_evaluable_weekly(NotEvaluableReason.UNCLASSIFIED_GROUP, sets_weekly)
        elif not frequency_raw:
            ex_axis = (
                _not_evaluable(NotEvaluableReason.MODE_A_NO_EXERCISES) if is_mode_a else _NOT_TRAINED
            )
            sets_axis = _NOT_TRAINED
            weekly_axis = _NOT_TRAINED
        else:
            # Eixos POR SESSAO: linha do bucket no cadastro. Sem a linha, os
            # dois ficam de fora (o semanal continua); sem a faixa de
            # exercicios na linha, so o eixo de exercicios fica de fora.
            if rng is None:
                sets_axis = _not_evaluable(NotEvaluableReason.NO_RANGE_FOR_FREQUENCY, sets_avg)
            else:
                sets_axis = _axis(
                    sets_avg or 0.0, rng.sets_min, rng.sets_max, rng.sets_source, rng.sets_evidence, intent, is_target
                )
            if is_mode_a:
                ex_axis = _not_evaluable(NotEvaluableReason.MODE_A_NO_EXERCISES)
            elif rng is None:
                ex_axis = _not_evaluable(NotEvaluableReason.NO_RANGE_FOR_FREQUENCY, exercises_avg)
            elif not rng.has_exercises_range:
                ex_axis = _not_evaluable(NotEvaluableReason.NO_EXERCISES_RANGE, exercises_avg)
            else:
                ex_axis = _axis(
                    exercises_avg or 0.0,
                    rng.exercises_min,  # type: ignore[arg-type]
                    rng.exercises_max,  # type: ignore[arg-type]
                    rng.exercises_source,
                    rng.exercises_evidence,
                    intent,
                    is_target,
                )
            # Eixo SEMANAL: meta do macro-grupo, independente do bucket.
            if not spec.has_weekly_target:
                weekly_axis = _not_evaluable_weekly(NotEvaluableReason.NO_WEEKLY_TARGET, sets_weekly)
            else:
                weekly_axis = _axis(
                    sets_weekly or 0.0,
                    spec.weekly_sets_min,  # type: ignore[arg-type]
                    spec.weekly_sets_max,  # type: ignore[arg-type]
                    spec.weekly_source,
                    spec.weekly_evidence,
                    intent,
                    is_target,
                    weekly=True,
                )

        status, severity, conflict, status_reason = _combine((ex_axis, sets_axis, weekly_axis))
        uncalibrated = bool(
            spec is not None
            and frequency_raw
            and ((rng is not None and rng.uncalibrated) or (spec.has_weekly_target and spec.weekly_uncalibrated))
        )

        evaluations.append(
            GroupEvaluation(
                muscle_group_id=block_id,
                muscle_group_name=group.name,
                volume_class_id=spec.id if spec is not None else None,
                volume_class_name=spec.name if spec is not None else None,
                size_class=group.size_class,
                member_group_ids=tuple(sorted(members[block_id])),
                frequency_raw=frequency_raw,
                weekly_frequency=weekly_frequency,
                frequency_is_normalized=normalized,
                frequency_bucket=bucket,
                direct_sets_cycle_total=sets_total,
                direct_exercises_cycle_total=exercises_total,
                direct_sets_per_session=sets_avg,
                direct_exercises_per_session=exercises_avg,
                direct_exercises_axis=ex_axis,
                direct_sets_axis=sets_axis,
                weekly_sets_axis=weekly_axis,
                status=status,
                severity=severity,
                status_conflict=conflict,
                status_reason=status_reason,
                weekly_projection=weekly,
                is_intent_target=is_target,
                sessions=sessions,
                technique_equivalent_cycle_total=technique_total,
                uncalibrated_ranges=uncalibrated,
            )
        )

    return MethodologyVolumeEvaluation(
        metric=METRIC_NAME,
        metric_version=metric_version(METRIC_NAME),
        volume_mode=volume_mode,
        intent=intent,
        cycle_days=cycle_days,
        training_sessions=len(training),
        weekly_factor=round(weekly_factor, 4),
        frequency_is_normalized=normalized,
        groups=tuple(evaluations),
        window_days=EVALUATION_WINDOW_DAYS,
        window_start_sequence=window_start,
        counting_mode=counting_mode.value,
        sets_unit_label=COUNTED_SETS_UNIT_LABELS[counting_mode],
        item_shares=tuple(item_shares),
        data_quality=MethodologyDataQuality(
            items_without_primary_contribution=dq_no_primary,
            items_without_canonical=dq_no_canonical,
            items_without_mapping=dq_no_mapping,
            items_without_sets_count=dq_no_sets,
            groups_with_unknown_parent=unknown_parent,
        ),
    )


# ---------------------------------------------------------------------------
# Comparacao entre modos de contagem (diagnostico de volume, Lote 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CountingModeGroupSnapshot:
    """O bloco num modo: o que a tela poe lado a lado (numeros do motor)."""

    counting_mode: str
    sets_unit_label: str
    weekly_frequency: float
    frequency_bucket: Optional[str]
    # Series da janela de 7 dias (None sem sessao que conte).
    direct_sets_weekly: Optional[float]
    direct_sets_per_session: Optional[float]
    status: MethodologyVolumeStatus
    severity: Optional[MethodologySeverity]
    status_reason: Optional[NotEvaluableReason]
    session_range_min: Optional[float]
    session_range_max: Optional[float]
    weekly_range_min: Optional[float]
    weekly_range_max: Optional[float]
    uncalibrated_ranges: bool


@dataclass(frozen=True)
class CountingModeGroupComparison:
    muscle_group_id: int
    muscle_group_name: str
    volume_class_name: Optional[str]
    modes: tuple[CountingModeGroupSnapshot, ...]
    # O status combinado / o bucket de frequencia difere entre os modos.
    status_changed: bool
    bucket_changed: bool


@dataclass(frozen=True)
class CountingModeComparison:
    metric: str
    metric_version: str
    current_counting_mode: str
    counting_modes: tuple[str, ...]
    groups: tuple[CountingModeGroupComparison, ...]
    # Modos em que algum bloco foi avaliado contra faixa/meta ainda copiada.
    uncalibrated_modes: tuple[str, ...]


def _snapshot(group: GroupEvaluation, evaluation: MethodologyVolumeEvaluation) -> CountingModeGroupSnapshot:
    return CountingModeGroupSnapshot(
        counting_mode=evaluation.counting_mode,
        sets_unit_label=evaluation.sets_unit_label,
        weekly_frequency=group.weekly_frequency,
        frequency_bucket=group.frequency_bucket,
        direct_sets_weekly=group.weekly_projection.direct_sets_weekly if group.weekly_projection else None,
        direct_sets_per_session=group.direct_sets_per_session,
        status=group.status,
        severity=group.severity,
        status_reason=group.status_reason,
        session_range_min=group.direct_sets_axis.range_min,
        session_range_max=group.direct_sets_axis.range_max,
        weekly_range_min=group.weekly_sets_axis.range_min,
        weekly_range_max=group.weekly_sets_axis.range_max,
        uncalibrated_ranges=group.uncalibrated_ranges,
    )


def compare_counting_modes(
    evaluations: Sequence[MethodologyVolumeEvaluation],
    current_counting_mode: VolumeCountingMode | str = DEFAULT_VOLUME_COUNTING_MODE,
) -> CountingModeComparison:
    """O MESMO Modelo avaliado em cada modo, lado a lado por bloco, com as
    transicoes (status combinado ou bucket diferentes) ja marcadas. Puro: so
    rearruma as avaliacoes que o motor ja fez - nenhuma conta nova. Entram os
    blocos com trabalho contado em ALGUM modo (sessao ou fracao); a ordem e a
    da primeira avaliacao."""
    current = VolumeCountingMode(current_counting_mode).value
    ordered = sorted(evaluations, key=lambda e: list(VolumeCountingMode).index(VolumeCountingMode(e.counting_mode)))
    by_mode = [{g.muscle_group_id: g for g in e.groups} for e in ordered]
    rows: list[CountingModeGroupComparison] = []
    seen: set[int] = set()
    for evaluation in ordered:
        for group in evaluation.groups:
            if group.muscle_group_id in seen:
                continue
            seen.add(group.muscle_group_id)
            present = [(lookup[group.muscle_group_id], ev) for lookup, ev in zip(by_mode, ordered)
                       if group.muscle_group_id in lookup]
            if not any(g.frequency_raw or g.sessions for g, _ev in present):
                continue
            snapshots = tuple(_snapshot(g, ev) for g, ev in present)
            rows.append(
                CountingModeGroupComparison(
                    muscle_group_id=group.muscle_group_id,
                    muscle_group_name=group.muscle_group_name,
                    volume_class_name=group.volume_class_name,
                    modes=snapshots,
                    status_changed=len({s.status for s in snapshots}) > 1,
                    bucket_changed=len({s.frequency_bucket for s in snapshots}) > 1,
                )
            )
    return CountingModeComparison(
        metric=METRIC_NAME,
        metric_version=metric_version(METRIC_NAME),
        current_counting_mode=current,
        counting_modes=tuple(e.counting_mode for e in ordered),
        groups=tuple(rows),
        uncalibrated_modes=tuple(
            e.counting_mode for e in ordered if any(g.uncalibrated_ranges for g in e.groups)
        ),
    )


__all__ = [
    "AxisEvaluation",
    "COUNTED_ROLES_BY_MODE",
    "COUNTED_SETS_UNIT_LABELS",
    "COUNTED_SETS_UNIT_SINGULAR_LABELS",
    "CountingModeComparison",
    "CountingModeGroupComparison",
    "CountingModeGroupSnapshot",
    "ItemDirectShare",
    "ItemGroupShare",
    "VOLUME_COUNTING_MODE_LABELS",
    "compare_counting_modes",
    "counting_mode_description",
    "export_for_frontend",
    "ContributionRow",
    "DEFAULT_VOLUME_COUNTING_MODE",
    "EVALUATION_WINDOW_DAYS",
    "ExerciseContribution",
    "GroupContribution",
    "FREQUENCY_BUCKETS",
    "GroupEvaluation",
    "INTENT_KINDS",
    "METHODOLOGY_VOLUME_STATUS_CODES",
    "METRIC_NAME",
    "MethodologyDataQuality",
    "MethodologyGroupInput",
    "MethodologyIntent",
    "MethodologyItemInput",
    "MethodologyRange",
    "MethodologySeverity",
    "MethodologySlotInput",
    "MethodologyVolumeEvaluation",
    "MethodologyVolumeStatus",
    "NotEvaluableReason",
    "REST_SLOT_TYPE",
    "RangeSource",
    "SIZE_CLASSES",
    "SessionDirectWork",
    "PRIMARY_ONLY_WEIGHT",
    "SECONDARY_SESSION_MIN_SETS",
    "VolumeClassSpec",
    "VolumeContributions",
    "VolumeCountingMode",
    "WeeklyProjection",
    "evaluate_methodology_volume",
    "block_of_group",
    "block_volume_class_id",
    "GroupHierarchy",
    "VOLUME_CLASS_UNCLASSIFIED_LABEL",
    "hierarchy_of_group_name",
    "index_hierarchy_by_name",
    "group_name_key",
    "resolve_group_hierarchy",
    "calendar_weekly_rate",
    "block_contributions",
    "block_contributions_by_member",
    "SETS_DECIMAL_PLACES",
    "counted_contributions",
    "counted_roles",
    "counted_roles_for_modes",
    "counts_secondary",
    "counting_mode_for_window",
    "counting_mode_or_default",
    "counts_as_frequency_session",
    "evaluation_window",
    "evaluation_window_days",
    "frequency_bucket_for",
    "is_training_slot_type",
    "weekly_normalization",
    "weekly_window_days",
]
