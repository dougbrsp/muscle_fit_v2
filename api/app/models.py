from datetime import date, time, datetime
from decimal import Decimal
from typing import Any, ClassVar, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from .domain.training_goal import optional_goal_value
from .domain.prescription_defaults import DEFAULT_MAX_REST_SECONDS, DEFAULT_MIN_REST_SECONDS
from .domain.set_measures import MEASURE_UNKNOWN, item_validation_message, parse_measure
from .domain.set_sides import apply_canonical_to_model
from .request_models import NOTES_MAX
from .set_measure_input import ensure_valid_set_measure


def _known_measure(value: Optional[str]) -> Optional[str]:
    """Medida enviada -> o valor canônico do dono; vazio = None; desconhecida
    -> erro de validação (422), nunca `reps` em silêncio."""
    if value is None or not str(value).strip():
        return None
    measure = parse_measure(value)
    if measure is None:
        raise ValueError(item_validation_message(MEASURE_UNKNOWN))
    return measure.value


class ExerciseSetIn(BaseModel):
    set_number: int
    set_type: str = "working"
    reps: Optional[int] = None
    weight_kg: Optional[Decimal] = None
    rpe: Optional[Decimal] = None
    rest_seconds: Optional[int] = None
    # Frequência cardíaca da série, capturada pelo relógio (app Zepp OS).
    hr_avg_bpm: Optional[Decimal] = None
    hr_max_bpm: Optional[Decimal] = None
    notes: Optional[str] = None
    # Medidas de série (Lote T3, colunas da 089): `reps` (ausente = reps, como
    # todo cliente antigo), `duration` (segundos em `duration_seconds`, reps
    # nulas) ou `max_reps` (reps obrigatórias; `duration_seconds` = janela).
    # Forma validada pelo dono `domain/set_measures.validate_set_measure` -> 422.
    measure: Optional[str] = Field(default=None, max_length=16)
    duration_seconds: Optional[int] = None
    # M102 (colunas da 100): reps/RPE por lado em exercício unilateral. Com
    # algum lado, `reps`/`rpe` oficiais saem do dono (`domain/set_sides`, pior
    # lado); ausentes (relógio, clientes antigos) nada muda.
    reps_left: Optional[int] = Field(default=None, ge=0)
    reps_right: Optional[int] = Field(default=None, ge=0)
    rpe_left: Optional[Decimal] = Field(default=None, ge=0, le=10)
    rpe_right: Optional[Decimal] = Field(default=None, ge=0, le=10)

    # A edição (`ExerciseSetUpdateIn`) valida DEPOIS da mescla com a série
    # gravada (campo omitido preserva a medida), não no payload parcial.
    _validates_measure: ClassVar[bool] = True

    @model_validator(mode="after")
    def _apply_sides(self):
        # Antes da validação da medida: o canônico vem dos lados (M102). Na
        # edição a mescla recalcula de novo sobre os lados já mesclados.
        apply_canonical_to_model(self)
        return self

    @model_validator(mode="after")
    def _check_measure(self):
        if self._validates_measure:
            # Cliente antigo que mande um `duration_seconds` solto (sem medida)
            # continua gravando reps: a chave era ignorada antes do T3.
            if self.measure is None:
                self.duration_seconds = None
            ensure_valid_set_measure(self.measure, self.reps, self.duration_seconds, set_number=self.set_number)
        return self


class ExerciseIn(BaseModel):
    sequence: int
    original_name: str
    mapping_id: Optional[int] = None
    # mapped_name/muscle_primary/muscle_secondary não são mais persistidos em
    # exercises. São aceitos apenas como hints para resolver mapping_id via
    # exercise_mapping_canonical.match_mapping quando mapping_id não é enviado,
    # preservando compatibilidade com payloads existentes do MCP/OpenClaw.
    mapped_name: Optional[str] = None
    muscle_primary: Optional[str] = None
    muscle_secondary: Optional[str] = None
    # equipment_id explícito (id da associação gym_equipment, não do catálogo
    # equipment) tem prioridade; equipment_name é hint de busca pelo nome do
    # equipamento cadastrado no catálogo para a academia do treino. Sem
    # nenhum dos dois, o equipamento é resolvido automaticamente (menos usado
    # recentemente para
    # o grupo muscular) quando a academia (activities.gym_id) é conhecida.
    equipment_id: Optional[int] = None
    equipment_name: Optional[str] = None
    notes: Optional[str] = None
    # Sinais subjetivos opcionais de dor por exercicio, usados pelo modelo de
    # recuperacao muscular v2 (fullbody-no-reps-v1) para classificar
    # NO_DIRECT_WORK. muscle_pain: 0-10 (dor muscular residual). joint_pain:
    # dor articular (bloqueia trabalho direto quando true).
    muscle_pain: Optional[int] = Field(default=None, ge=0, le=10)
    joint_pain: Optional[bool] = None
    sets: list[ExerciseSetIn] = Field(default_factory=list)


class HrZoneIn(BaseModel):
    zone_number: int
    zone_name: Optional[str] = None
    min_bpm: Optional[int] = None
    max_bpm: Optional[int] = None
    duration_seconds: Optional[int] = None
    percent_time: Optional[Decimal] = None


class ActivityIn(BaseModel):
    source_name: str = "openclaw"
    external_id: Optional[str] = None
    local_date: date
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    activity_type: str = "musculacao"
    name: str
    description: Optional[str] = None
    duration_seconds: Optional[int] = None
    calories_kcal: Optional[Decimal] = None
    avg_hr_bpm: Optional[Decimal] = None
    max_hr_bpm: Optional[Decimal] = None
    notes: Optional[str] = None
    # gym_id explícito tem prioridade; gym_name resolve por nome (ex.:
    # "Gaviões", "SmartFit"). Sem nenhum dos dois, activities.gym_id fica NULL
    # e o equipamento dos exercícios não é auto-resolvido.
    gym_id: Optional[int] = None
    gym_name: Optional[str] = None
    # Tipo de Treinamento (training_types) que originou este registro,
    # motor de geracao de ficha v2. Opcional: activities sem vinculo (ex.:
    # lancadas via MCP/OpenClaw ou treinos livres) ficam com training_type_id
    # NULL e nao exibem metas semanais por grupo muscular em
    # GET /analytics/muscle-activation-by-activity.
    training_type_id: Optional[int] = None
    # Quem conduziu o treino: convenção "personal" (personal trainer) ou
    # "dougfitness" (seguiu a ficha gerada pelo agente de IA). Texto livre.
    conducted_by: Optional[str] = None
    exercises: list[ExerciseIn] = Field(default_factory=list)
    hr_zones: list[HrZoneIn] = Field(default_factory=list)
    raw_payload: Optional[dict[str, Any]] = None


class ExerciseSetUpdateIn(ExerciseSetIn):
    """Série na edição (`PUT /exercises/logs/{id}`). Campo omitido preserva o
    valor gravado da série de mesmo `set_number` (ver
    app/exercise_set_update_merge.py); `null` explícito limpa."""

    # Medida/duração omitidas preservam as gravadas: a forma é validada em
    # `update_exercise_log`, sobre a série já mesclada.
    _validates_measure: ClassVar[bool] = False

    # Dor por série (mesma semântica de ExerciseIn.muscle_pain/joint_pain),
    # gravada pelo Modo Treino; só faz round-trip na edição.
    muscle_pain: Optional[int] = Field(default=None, ge=0, le=10)
    joint_pain: Optional[bool] = None
    # Fase 5: vínculo da aplicação de técnica (colunas da 059). Omitido
    # preserva; aplicação sem vínculo recebe o derivado (ver
    # exercise_set_update_merge.link_technique_applications).
    technique_code: Optional[str] = Field(default=None, max_length=20)
    anchor_set_number: Optional[int] = Field(default=None, ge=1)


class ExerciseLogUpdateIn(BaseModel):
    local_date: date
    original_name: str
    started_at: Optional[datetime] = None
    mapping_id: Optional[int] = None
    # Hints de compatibilidade; ver ExerciseIn.
    mapped_name: Optional[str] = None
    muscle_primary: Optional[str] = None
    muscle_secondary: Optional[str] = None
    gym_id: Optional[int] = None
    gym_name: Optional[str] = None
    conducted_by: Optional[str] = None
    equipment_id: Optional[int] = None
    equipment_name: Optional[str] = None
    # Vincula este exercício lançado a um item específico da ficha planejada
    # (workout_plan_exercises.id). Diferente dos demais campos opcionais desta
    # classe, aqui distinguimos "campo não enviado" (preserva vínculo atual)
    # de "campo enviado como null" (desvincula explicitamente) — ver
    # update_exercise_log_by_id() em main.py, que usa model_fields_set.
    workout_plan_exercise_id: Optional[int] = None
    # Ver ExerciseIn.muscle_pain/joint_pain (mesma semantica, opcional aqui).
    muscle_pain: Optional[int] = Field(default=None, ge=0, le=10)
    joint_pain: Optional[bool] = None
    sets: list[ExerciseSetUpdateIn] = Field(default_factory=list)


class BodyCompositionIn(BaseModel):
    local_date: date
    measured_at: Optional[datetime] = None
    weight_kg: Optional[Decimal] = None
    bmi: Optional[Decimal] = None
    body_fat_percent: Optional[Decimal] = None
    fat_mass_kg: Optional[Decimal] = None
    fat_free_mass_kg: Optional[Decimal] = None
    muscle_mass_kg: Optional[Decimal] = None
    skeletal_muscle_percent: Optional[Decimal] = None
    body_water_percent: Optional[Decimal] = None
    visceral_fat_rating: Optional[Decimal] = None
    bone_mass_kg: Optional[Decimal] = None
    bmr_kcal: Optional[Decimal] = None
    subcutaneous_fat_percent: Optional[Decimal] = None
    protein_percent: Optional[Decimal] = None
    metabolic_age: Optional[int] = None
    source: str = "manual"
    notes: Optional[str] = None
    raw_payload: Optional[dict[str, Any]] = None


class ExerciseMappingIn(BaseModel):
    original_name: str
    amazfit_name: str
    mapped_name: Optional[str] = None
    muscle_primary: Optional[str] = None
    muscle_secondary: Optional[str] = None
    confidence: Optional[Decimal] = None
    is_user_confirmed: bool = False
    notes: Optional[str] = None


class WorkoutPlanExerciseIn(BaseModel):
    sequence: int
    exercise_name: str
    # Id da linha JA SALVA (`workout_plan_exercises.id`), ecoado pela tela da
    # ficha. So identifica a linha para a trava do grupamento muscular na
    # edicao (`workout_plan_edit_rules`); ausente = linha nova. O UPDATE
    # continua casando por `sequence`.
    workout_plan_exercise_id: Optional[int] = None
    amazfit_name: Optional[str] = None
    # Vínculo resolvido com o catálogo canônico (exercise_zepp_mappings.id) e
    # com o equipamento da academia (gym_equipment.id). Quando ausentes, são
    # auto-resolvidos a partir de exercise_name/amazfit_name/equipment_name ao
    # salvar o plano — necessários ao app do relógio (fluxo "Ocupado" via
    # /gym-equipment/recommend e log de equipment_id nas séries).
    mapping_id: Optional[int] = None
    preferred_equipment_id: Optional[int] = None
    muscle_primary: Optional[str] = None
    muscle_secondary: Optional[str] = None
    equipment_name: Optional[str] = None
    sets_count: Optional[int] = None
    reps_target: Optional[str] = None
    load_target: Optional[str] = None
    rpe_target: Optional[str] = None
    rest_seconds: Optional[int] = None
    warmup: bool = False
    substitution_options: Optional[list[str]] = None
    notes: Optional[str] = None
    # Espelho informativo dos 10 campos de técnica avançada/aquecimento/
    # ajuste/pico de contração do card do modo 'equipment_series' (ver
    # WorkoutPlanTemplateSlotItemIn abaixo para a definição normativa e regras
    # de negócio). Aqui mais permissivo (Optional[str]/Optional[int], sem
    # Literal) porque este payload também é aceito na edição manual da ficha
    # em /planos-treino, onde não há a mesma validação de origem do builder.
    # NÃO relacionados ao campo `warmup: bool` acima (heurística automática de
    # workout_plan_ai.py::assign_warmup_flags, conceito diferente).
    advanced_technique: Optional[str] = None
    advanced_technique_applications: Optional[int] = None
    advanced_technique_duration_seconds: Optional[int] = None
    warmup_sets_count: Optional[int] = None
    warmup_reps: Optional[int] = None
    warmup_rest_seconds: Optional[int] = None
    feeler_set_sets_count: Optional[int] = None
    feeler_set_reps: Optional[int] = None
    feeler_set_rest_seconds: Optional[int] = None
    # Pico de contração: segurar a contração na última repetição da série de
    # TRABALHO por N segundos (default de UI = 2). Presença = checkbox
    # marcado no card do modo 'equipment_series'; None = desmarcado. Ver
    # WorkoutPlanTemplateSlotItemIn abaixo para a definição normativa.
    peak_contraction_seconds: Optional[int] = None
    # Medida da série (Lote T2; colunas da 089; dono `domain/set_measures.py`):
    # `reps` | `duration` | `max_reps`. Ausente = não enviado (o save preserva
    # o gravado, `workout_plan_exercise_merge`); None/`reps` = repetições.
    # Aqui só o vocabulário é recusado (medida desconhecida -> 422); a forma
    # completa do item é validada no Modelo, de onde a ficha copia.
    measure: Optional[str] = None
    duration_seconds_target: Optional[int] = None
    time_window_seconds: Optional[int] = None
    # Objetivo de treino da linha (M97 F0; coluna da 093; dono
    # `domain/training_goal.py`): o resolvido na geração (item > Modelo >
    # default), ecoado do rascunho. Ausente = não enviado (o save preserva o
    # gravado, `workout_plan_exercise_merge`); linha nova sem ele = default.
    # Valor fora do vocabulário -> 422.
    training_goal: Optional[str] = None

    @field_validator("measure")
    @classmethod
    def _measure_known(cls, value: Optional[str]) -> Optional[str]:
        return _known_measure(value)

    @field_validator("training_goal")
    @classmethod
    def _training_goal_known(cls, value: Optional[str]) -> Optional[str]:
        return optional_goal_value(value)


class WorkoutPlanIn(BaseModel):
    plan_date: Optional[date] = None
    plan_time: Optional[time] = None
    name: str
    plan_type: str = "full_body"
    # Letra do ciclo ABC (A/B/C) por alças musculares, quando plan_type="abc";
    # None para os demais plan_type (full_body, upper_lower, etc.).
    split_day: Optional[str] = None
    goal: Optional[str] = None
    status: str = "planned"
    gym_id: Optional[int] = None
    gym_name: Optional[str] = None
    raw_payload: Optional[dict[str, Any]] = None
    # Modelos de Ficha de Treino Personalizados: de qual modelo/slot esta
    # ficha SALVA veio (None quando não veio de nenhum modelo) — persistidos
    # em workout_plans.template_id/template_slot_sequence (migração 007),
    # usados por resolve_next_slot para calcular o próximo slot do ciclo em
    # futuras gerações automáticas a partir do mesmo modelo.
    template_id: Optional[int] = None
    template_slot_sequence: Optional[int] = None
    # Versao do Modelo de que o rascunho nasceu (migracao 066). Validada no
    # SERVIDOR contra `template_id` (`services._validated_template_version_id`):
    # versao de outro Modelo vira NULL, nunca e gravada.
    template_version_id: Optional[int] = None
    exercises: list[WorkoutPlanExerciseIn] = Field(default_factory=list)


class PlanExerciseEquipmentSubstituteIn(BaseModel):
    """Body de `POST /workout-plans/{plan_id}/exercises/{exercise_id}/substitute-equipment`
    (M8). `gym_equipment_id` = `gym_equipment.id` (a ASSOCIACAO exercicio x
    equipamento da academia da ficha), o mesmo valor de
    `preferred_equipment_id` - nunca o `equipment.id` do catalogo."""

    gym_equipment_id: int = Field(gt=0)


class MuscleActivationExerciseIn(BaseModel):
    """Payload ÚNICO de ativação planejada (M76 E3): o MESMO shape do item do
    Modelo (`mapping_id`, `sets_count`, `advanced_technique`,
    `advanced_technique_applications`). O backend soma o equivalente da
    técnica pelo dono da Fase 5 (`technique_volume.planned_item_sets`); o
    frontend só envia os dados da linha."""

    mapping_id: Optional[int] = None
    sets_count: Optional[int] = None
    advanced_technique: Optional[str] = None
    advanced_technique_applications: Optional[int] = None
    # M100: linha de exercicio laboral (a ficha expoe `is_laboral`): 0 series
    # planejadas no preview (`technique_volume.planned_item_sets`).
    is_laboral: Optional[bool] = None


class MuscleActivationPreviewIn(BaseModel):
    exercises: list[MuscleActivationExerciseIn] = Field(default_factory=list)


class WorkoutPlanGenerateIn(BaseModel):
    """Entrada de `POST /workout-plans/generate`.

    PR-18 (PLANO_20260912.md, seções 6 e 9): **nenhum caminho gera ficha sem
    um Modelo de Treino**. `template_id` é obrigatório — faltando, o FastAPI
    já responde 422 no request síncrono, antes de qualquer job.

    Os campos `plan_type`/`split_day` saíram DESTE input (eram a porta de
    entrada dos caminhos ABC/Full Body). Eles continuam existindo como
    colunas e nos modelos de LEITURA/save de fichas legadas (`WorkoutPlanIn`,
    `WorkoutPlanOut`) — §12 do plano: planos antigos continuam legíveis.
    """

    gym_id: Optional[int] = None
    gym_name: Optional[str] = None
    plan_date: Optional[date] = None
    plan_time: Optional[time] = None
    goal: Optional[str] = None
    # Faixa de descanso (segundos) desejada pelo usuario entre series, usada
    # como clamp/fallback tanto na geracao via IA quanto no reforco de volume
    # deterministico; limites absolutos de sanidade continuam 15-240.
    min_rest_seconds: int = DEFAULT_MIN_REST_SECONDS
    max_rest_seconds: int = DEFAULT_MAX_REST_SECONDS
    # Modelo de Treino (obrigatório desde o PR-18): a ficha é gerada 100% a
    # partir deste modelo, que precisa estar ativo e atribuído a este usuário
    # (ver workout_plan_templates_engine.get_template_if_assigned). O
    # `volume_mode` do modelo é quem decide o motor: 'percent_budget' = Modo
    # A, 'equipment_series' = Modo B.
    template_id: int
    # Escolha de slot dentro do modelo (ver resolve_next_slot):
    # informado = usa exatamente esse `sequence` (escolha manual do usuário
    # no popup "Gerar novo plano"); None (padrão) = continua automaticamente
    # a partir da sequência do último plano salvo com este template_id.
    template_slot_sequence: Optional[int] = None


class WorkoutPlanTemplateSlotItemIn(BaseModel):
    item_order: int
    # 'muscle_group' | 'exercise' | 'cardio' | 'equipment' — livre (mesma
    # razão de template_type/plan_type), validado na API
    # (_validate_template_payload). 'equipment' é o item do modo
    # volume_mode='equipment_series' (LOTE 1): grupo muscular + equipamento +
    # exercício + séries fixas, sem % de orçamento.
    item_kind: str
    muscle_group_id: Optional[int] = None
    # FK para exercise_catalog(id) — NUNCA exercise_zepp_mappings (legado).
    mapping_id: Optional[int] = None
    # FK para equipment(id) — só usado por item_kind='equipment' (modo
    # volume_mode='equipment_series').
    equipment_id: Optional[int] = None
    sets_count: Optional[int] = 3
    reps_target: Optional[str] = None
    rpe_target: Optional[str] = None
    prioritize: bool = False
    # Prescrição de cardio (M98 C0; colunas da 095 + a duração da 007; dono
    # `domain/cardio_prescription.py`). Só em item_kind='cardio'. Modo
    # ausente = 'continuous' (Normal; item antigo só com a duração). Normal:
    # duração (min), aquecimento/recuperação (min), zona-alvo. HIIT:
    # aquecimento/recuperação (min), zona baixa/alta e seus tempos (SEGUNDOS),
    # nº de séries. Forma, mínimos e tetos são do dono (`validate`), chamado
    # em _validate_template_payload -> 422 com a mensagem do dono; os campos
    # do outro modo são descartados na gravação.
    cardio_duration_minutes: Optional[int] = None
    cardio_mode: Optional[str] = None
    cardio_warmup_minutes: Optional[int] = None
    cardio_cooldown_minutes: Optional[int] = None
    cardio_target_zone: Optional[int] = None
    hiit_low_seconds: Optional[int] = None
    hiit_high_seconds: Optional[int] = None
    hiit_low_zone: Optional[int] = None
    hiit_high_zone: Optional[int] = None
    hiit_rounds: Optional[int] = None
    # Motor de ficha v2 (USE_TEMPLATE_VOLUME_ENGINE_V2): faixa percentual
    # (0-100) da meta semanal (training_type_muscle_group_targets.target_weekly_sets)
    # do grupo muscular deste item que deve ser entregue NESTE slot. Só faz
    # sentido para item_kind='muscle_group'; ortogonal a `prioritize`, que
    # nunca deve influenciar volume/orçamento (ver MELHORIA_MOTOR_FICHA.md,
    # seção 36). Ambos None = item ainda não configurado para o motor v2
    # (tratado como UNDER_ALLOCATED por validate_template_volume_coverage).
    priority_min_pct: Optional[int] = None
    priority_max_pct: Optional[int] = None
    # Técnica avançada aplicada a UM único exercício/série (família
    # rest_pause | drop_set | fst7 | gvt | forced_reps | negative_reps |
    # partial_reps | cheat_reps). Bi-set/tri-set/giant set/pré-exaustão/
    # pós-exaustão/super-série ficam fora de escopo (são combinações de
    # múltiplos exercícios, não cabem no modelo "1 card = 1 técnica"). Só faz
    # sentido para item_kind='equipment' — validado em
    # _validate_template_payload (workout_plan_templates.py), sem
    # CHECK/trigger no banco.
    # O código é validado contra o catálogo `advanced_techniques` (migração
    # 031) em workout_plan_templates.py via
    # advanced_techniques.ensure_valid_codes — 422 para código inexistente ou
    # inativo. Aqui só o formato (a coluna é VARCHAR(20)).
    advanced_technique: Optional[str] = Field(default=None, min_length=1, max_length=20)
    # Nº de mini-blocos/drops/reps extras/séries — companion tolerante de
    # advanced_technique: pode vir preenchido mesmo sem a técnica selecionada
    # (fluxo de UI onde o usuário digita e depois desmarca), sem gerar erro —
    # apenas ignorado na persistência quando advanced_technique é None.
    advanced_technique_applications: Optional[int] = Field(default=None, ge=1, le=20)
    # Pausa/intervalo em segundos — só relevante para rest_pause/fst7/gvt.
    advanced_technique_duration_seconds: Optional[int] = Field(default=None, ge=1, le=600)
    # Série de aquecimento CONFIGURADA — campos NOVOS, distintos do `warmup
    # BOOLEAN` legado de workout_plan_exercises (heurística automática de
    # workout_plan_ai.py::assign_warmup_flags). warmup_rest_seconds é
    # companion tolerante de warmup_reps (mesmo espírito acima).
    # warmup_sets_count: nº de séries de aquecimento (default de UI = 2).
    warmup_sets_count: Optional[int] = Field(default=None, ge=1, le=10)
    warmup_reps: Optional[int] = Field(default=None, ge=1, le=100)
    warmup_rest_seconds: Optional[int] = Field(default=None, ge=1, le=600)
    # Série de ajuste — feeler_set_rest_seconds é companion tolerante de
    # feeler_set_reps. feeler_set_sets_count: nº de séries de ajuste
    # (default de UI = 2).
    feeler_set_sets_count: Optional[int] = Field(default=None, ge=1, le=10)
    feeler_set_reps: Optional[int] = Field(default=None, ge=1, le=100)
    feeler_set_rest_seconds: Optional[int] = Field(default=None, ge=1, le=600)
    # Pico de contração: segurar a contração na última repetição da série de
    # TRABALHO por N segundos (checkbox "Pico de contração" no card do modo
    # 'equipment_series', default de UI = 2s). Presença (não-None) = checkbox
    # marcado; None = desmarcado — mesmo padrão de warmup_reps/feeler_set_reps,
    # sem BOOLEAN redundante. Sanidade: não deve passar de 60s.
    peak_contraction_seconds: Optional[int] = Field(default=None, ge=1, le=60)
    # Descanso entre as séries de TRABALHO deste exercício (Fase 7, Frente B;
    # coluna da migração 058). None = "da geração": a ficha usa o intervalo
    # mín/máx escolhido ao gerar (ponto médio). Preenchido, SOBREPÕE esse
    # intervalo sem ser limitado por ele. Mesmos limites de
    # warmup_rest_seconds; só faz sentido para item_kind='equipment'
    # (validado em _validate_template_payload).
    rest_seconds: Optional[int] = Field(default=None, ge=1, le=600)
    # Medida da série (Lote T2; colunas da 089; dono `domain/set_measures.py`):
    # `reps` (default; None = reps) | `duration` (exige
    # `duration_seconds_target`) | `max_reps` (`time_window_seconds`
    # opcional). Só em item_kind='equipment'; fora de `reps`, sem
    # `reps_target` e sem técnica/aquecimento/ajuste/pico. A forma (limites,
    # combinações) é do dono (`validate_item_measure`), chamada em
    # _validate_template_payload -> 422 com a mensagem do dono.
    measure: Optional[str] = None
    duration_seconds_target: Optional[int] = None
    time_window_seconds: Optional[int] = None
    # Objetivo de treino do item (M97 F0; coluna NULL da 093; dono
    # `domain/training_goal.py`): override do objetivo do Modelo. None = herda
    # (`training_goal.resolve_goal`). Valor fora do vocabulário -> 422.
    training_goal: Optional[str] = None

    @field_validator("training_goal")
    @classmethod
    def _training_goal_known(cls, value: Optional[str]) -> Optional[str]:
        return optional_goal_value(value)


class CardioPrescriptionCheckIn(BaseModel):
    """Prescrição de UM item de cardio do editor do Modelo (M98 C1), para
    `POST /workout-plan-templates/cardio-prescription-check`: o card mostra a
    mensagem do dono (`cardio_prescription.validation_error`) sem salvar. Os
    mesmos campos de `WorkoutPlanTemplateSlotItemIn`; nada é gravado."""

    cardio_duration_minutes: Optional[float] = None
    cardio_mode: Optional[str] = None
    cardio_warmup_minutes: Optional[float] = None
    cardio_cooldown_minutes: Optional[float] = None
    cardio_target_zone: Optional[float] = None
    hiit_low_seconds: Optional[float] = None
    hiit_high_seconds: Optional[float] = None
    hiit_low_zone: Optional[float] = None
    hiit_high_zone: Optional[float] = None
    hiit_rounds: Optional[float] = None


class WorkoutPlanTemplateSlotIn(BaseModel):
    sequence: int
    name: str
    # 1=segunda..7=domingo; metadado informativo nesta fase (ver seção 4 do
    # plano) — não usado no cálculo do ciclo.
    weekday: Optional[int] = None
    slot_type: str = "normal"  # 'normal' | 'descanso'
    # Faixa de prioridade percentual do slot (0-100), ex.: 60%-70%. Opcionais
    # e sempre juntos (ambos ou nenhum) — validado em _validate_template_payload.
    # Apenas captura nesta fase: NÃO consumido pelo motor de geração de ficha.
    priority_min_pct: Optional[int] = None
    priority_max_pct: Optional[int] = None
    items: list[WorkoutPlanTemplateSlotItemIn] = Field(default_factory=list)


class WorkoutPlanTemplateIn(BaseModel):
    name: str
    description: Optional[str] = None
    # Legado (M24): o campo "Tipo de modelo" saiu da tela de Modelos de Treino
    # e o cliente novo NÃO o envia mais. `None` = "o cliente não disse nada
    # sobre o campo" — a API resolve em `workout_plan_templates.
    # _resolve_template_type` (edição preserva o valor gravado, criação usa o
    # default histórico 'full_body'). Cliente antigo que ainda manda o campo
    # continua mandando nele. Livre (mesma razão de plan_type em
    # WorkoutPlanIn) — não travar em Literal/ENUM. A COLUNA continua: ela sai
    # na F2.4, junto das rotas legadas que leem `plan_type`.
    template_type: Optional[str] = None
    # Associação OPCIONAL a um "Tipo de Treinamento" (training_types.py,
    # ver melhoria-eager-origami.md) — associação opcional a uma meta semanal
    # de volume por grupo muscular. Ortogonal: um modelo "abc" pode usar
    # qualquer Tipo de Treinamento (ou nenhum).
    training_type_id: Optional[int] = None
    # Modo de operação do template (LOTE 1, ortogonal ao training_type_id
    # acima): 'percent_budget' (default, motor v2 atual — itens
    # 'muscle_group' + % de orçamento sobre a meta semanal do training_type)
    # ou 'equipment_series' (itens 'equipment' com grupo muscular + equipamento
    # + exercício + séries fixas, sem % de orçamento, sem IA escolhendo nada).
    volume_mode: Literal["percent_budget", "equipment_series"] = "percent_budget"
    is_active: bool = True
    # Procedencia do modelo (migracao 038). O cliente NAO informa `origin`: ele
    # informa de qual geracao automatica este modelo saiu, e o servidor decide a
    # origem a partir disso (`workout_plan_templates._resolve_origin`) - depois
    # de conferir que a geracao e do proprio usuario. Ausente = modelo manual,
    # que e o que todo modelo era antes do Motor de Periodizacao.
    generation_log_id: Optional[int] = None
    # Grade do editor (migracao 062): linhas x colunas de slots, lida linha por
    # linha na ordem da `sequence`. `None` = automatica (o cliente calcula a
    # partir da quantidade de slots). Preenchidas juntas, a grade precisa
    # comportar todos os slots - validado em `_validate_template_payload`.
    grid_rows: Optional[int] = Field(None, ge=1, le=2)
    grid_cols: Optional[int] = Field(None, ge=1, le=4)
    # Modo de contagem do volume da metodologia (migração 073): um VALOR de
    # `intelligence.methodology_volume.VolumeCountingMode` (o dono do domínio;
    # validado por ele em `_counting_mode_valido`). `None` = "o cliente não
    # disse nada" — mesma semântica de `template_type`: a criação grava o
    # default do motor (`primary_only`, o comportamento de antes) e a edição
    # PRESERVA o modo gravado (`workout_plan_templates._resolve_counting_mode`),
    # para o editor que ainda não conhece o campo não zerar a escolha.
    volume_counting_mode: Optional[str] = None
    # Objetivo de treino do Modelo (M97 F0; coluna da 093; dono
    # `domain/training_goal.py`). Mesma semântica de `volume_counting_mode`:
    # `None` = "o cliente não disse nada" — a criação grava o default do dono
    # e a edição PRESERVA o gravado (`workout_plan_templates.
    # _resolve_training_goal`). Valor fora do vocabulário -> 422.
    training_goal: Optional[str] = None
    slots: list[WorkoutPlanTemplateSlotIn] = Field(default_factory=list)

    @field_validator("volume_counting_mode")
    @classmethod
    def _counting_mode_valido(cls, value: Optional[str]) -> Optional[str]:
        return counting_mode_value(value)

    @field_validator("training_goal")
    @classmethod
    def _training_goal_valido(cls, value: Optional[str]) -> Optional[str]:
        return optional_goal_value(value)


def counting_mode_value(value: Optional[str]) -> Optional[str]:
    """Valida um modo de contagem contra o enum do motor e devolve o VALOR
    (`None` passa). Import tardio: o pacote `intelligence` importa módulos que
    importam este (ciclo no import de `models`)."""
    if value is None:
        return None
    from .intelligence.methodology_volume import VolumeCountingMode

    try:
        return VolumeCountingMode(value).value
    except ValueError as exc:
        aceitos = ", ".join(mode.value for mode in VolumeCountingMode)
        raise ValueError(f"volume_counting_mode inválido: {value!r}. Valores aceitos: {aceitos}.") from exc


class WorkoutPlanTemplateAssignIn(BaseModel):
    user_id: int


class PeriodizationDraftIn(BaseModel):
    """Pedido de `POST /workout-plan-templates/periodization-drafts` (Motor de
    Periodizacao, PLANO_20260912.md secao 7.2).

    Nada aqui e escrito em banco como modelo: o job devolve um RASCUNHO para o
    usuario editar e salvar depois pelo `POST /workout-plan-templates`.

    `instructions` sao as instrucoes livres do usuario. Entram no prompt como
    DADO delimitado, nunca como instrucao (`periodization/context.py::
    sanitize_instructions`); o teto de 2.000 caracteres e o da secao 7.1.
    """

    # Nomes de subpastas de rag/markdown/TIPOS_TREINO, validados contra
    # `list_training_type_rag_folders()` no endpoint (422 para inexistente) -
    # nunca concatenados num Path sem essa checagem.
    rag_sources: list[str] = Field(default_factory=list, max_length=10)
    # Perfil de Metas de Ativacao (training_types). Opcional: sem ele nao ha
    # projecao de ativacao a comparar, so as regras de modelo.
    training_type_id: Optional[int] = None
    volume_mode: Literal["percent_budget", "equipment_series"] = "percent_budget"
    # Obrigatoria no Modo B (equipment_series) - validado no endpoint.
    gym_id: Optional[int] = None
    # Teto = MAX_TEMPLATE_SLOTS (8): o rascunho vira um Modelo, e um Modelo
    # cabe numa grade de no maximo 2 x 4 slots.
    days_per_cycle: int = Field(default=4, ge=1, le=8)
    instructions: Optional[str] = Field(default=None, max_length=2000)
    # Modo de contagem do volume do Modelo a gerar (`VolumeCountingMode`, Lote 5
    # do modo de contagem). Opcional: ausente = o default do motor
    # (`primary_only`), o comportamento de antes. Decide a TABELA da
    # metodologia do prompt (faixas do modo), a regra de contagem do texto, o
    # validador/complemento e o modo do rascunho devolvido.
    volume_counting_mode: Optional[str] = None
    # M97 F5: objetivo do Modelo a gerar (`domain/training_goal.py`; ausente =
    # default, o rascunho de sempre) e o objetivo de CADA dia do ciclo (lista com
    # `days_per_cycle` valores, na ordem de `sequence`; ausente = todos os dias
    # no objetivo do Modelo). Dia com objetivo diferente do Modelo vira override
    # nos itens dele (Modelo hibrido, ex.: 2 dias de forca em 5); o validador
    # aplica o objetivo item a item (`periodization/goal_plan.py`).
    training_goal: Optional[str] = None
    day_goals: Optional[list[str]] = Field(default=None, max_length=8)
    # M98 C5: o Modelo leva cardio? True = sim, False = nunca; ausente = so se
    # as `instructions` falarem de cardio (`periodization/cardio_plan.py`).
    include_cardio: Optional[bool] = None

    @field_validator("volume_counting_mode")
    @classmethod
    def _counting_mode_valido(cls, value: Optional[str]) -> Optional[str]:
        return counting_mode_value(value)

    @field_validator("training_goal")
    @classmethod
    def _training_goal_known(cls, value: Optional[str]) -> Optional[str]:
        return optional_goal_value(value)

    @field_validator("day_goals")
    @classmethod
    def _day_goals_known(cls, values: Optional[list[str]]) -> Optional[list[str]]:
        if values is None:
            return None
        parsed = [optional_goal_value(value) for value in values]
        if any(value is None for value in parsed):
            raise ValueError("day_goals: todo dia precisa de um objetivo.")
        return parsed

    @model_validator(mode="after")
    def _day_goals_cover_cycle(self) -> "PeriodizationDraftIn":
        if self.day_goals is not None and len(self.day_goals) != self.days_per_cycle:
            raise ValueError(
                f"day_goals precisa ter um objetivo por dia do ciclo ({self.days_per_cycle}); veio {len(self.day_goals)}."
            )
        return self


class TrainingTypeMuscleGroupTargetIn(BaseModel):
    muscle_group_id: int
    min_weekly_sets: float
    target_weekly_sets: float
    max_weekly_sets: float


class TrainingTypeIn(BaseModel):
    name: str
    description: Optional[str] = None
    rag_folder: Optional[str] = None
    is_active: bool = True
    targets: list[TrainingTypeMuscleGroupTargetIn] = Field(default_factory=list)


class InsightsGenerateIn(BaseModel):
    """Entrada de `POST /insights/generate`.

    F3.2: o contexto passou a ser montado no SERVIDOR
    (`insights_core.build_package_for_question` -> `intelligence_package`), e o
    cliente manda apenas a pergunta e a janela.

    `context` continua aceito e e IGNORADO (com `log_event
    insights.legacy_context_ignored`) durante a janela de compatibilidade do
    deploy - frontend e API nao sobem no mesmo instante, e um 422 aqui
    transformaria uma defasagem de minutos numa tela quebrada. O campo sai num
    PR posterior, depois de o log mostrar zero ocorrencia."""

    question: str = Field(max_length=2000)
    window: str = "28d"
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    context: str = Field(default="", max_length=8000)


class WorkoutsAnalysisGenerateIn(BaseModel):
    days: int = Field(default=90, ge=7, le=365)


# Limites de entrada da avaliacao de serie (IA-10). Nao sao regra de treino:
# so impedem que um payload arbitrario vire um prompt arbitrariamente grande
# (custo). Generosos de proposito - o maximo real de producao nao foi lido
# para estes campos: `exercise_name` = a coluna `VARCHAR(255)`; `notes` = o
# mesmo teto das demais anotacoes (`request_models.NOTES_MAX`, PR #434);
# series anteriores = muito acima de qualquer exercicio real.
SET_EVAL_EXERCISE_NAME_MAX_CHARS = 255
SET_EVAL_NOTES_MAX_CHARS = NOTES_MAX
SET_EVAL_PREVIOUS_SETS_MAX = 50


class PreviousSetIn(BaseModel):
    set_number: Optional[int] = None
    # Opcional/aditivo: ausente = working (clientes antigos).
    set_type: Optional[str] = None
    reps: Optional[int] = None
    weight_kg: Optional[float] = None
    rpe: Optional[float] = None


class SetEvaluationIn(BaseModel):
    exercise_name: str = Field(max_length=SET_EVAL_EXERCISE_NAME_MAX_CHARS)
    reps_target: Optional[str] = None
    load_target: Optional[str] = None
    rpe_target: Optional[str] = None
    reps: Optional[int] = None
    weight_kg: Optional[float] = None
    rpe: Optional[float] = None
    # M102: lados da série unilateral (só para o texto do prompt; `reps`/`rpe`
    # são os oficiais devolvidos pela gravação, pior lado).
    reps_left: Optional[int] = Field(default=None, ge=0)
    reps_right: Optional[int] = Field(default=None, ge=0)
    rpe_left: Optional[float] = Field(default=None, ge=0, le=10)
    rpe_right: Optional[float] = Field(default=None, ge=0, le=10)
    set_number: Optional[int] = None
    sets_count: Optional[int] = None
    previous_sets_this_session: list[PreviousSetIn] = Field(
        default_factory=list, max_length=SET_EVAL_PREVIOUS_SETS_MAX
    )
    # Dor/anotação da série recém-executada (tela /modo-treino) — usados pela
    # IA tanto para avaliar a série quanto para sugerir reps/carga/RPE da
    # próxima série do mesmo exercício.
    muscle_pain: Optional[int] = Field(default=None, ge=0, le=10)
    joint_pain: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=SET_EVAL_NOTES_MAX_CHARS)
    # Lote T3 (medidas de serie): medida da serie avaliada (ausente = reps).
    # Fora de reps, o motor nao da posicao na faixa nem carga da proxima serie.
    measure: Optional[str] = Field(default=None, max_length=16)
    # PR-14 (secao 5.7, furo G6): par exercicio x equipamento desta serie, para
    # o SERVIDOR resolver o teto seguro e clampar a sugestao da IA antes de
    # qualquer coisa chegar ao cliente. `mapping_id` = exercicio do catalogo;
    # `equipment_id` = `gym_equipment.id` (mesma convencao de
    # GET /exercises/last-session-sets e de SessionExerciseIn). Ausentes = par
    # desconhecido -> default conservador D3 (teto 9).
    mapping_id: Optional[int] = Field(default=None, ge=1)
    equipment_id: Optional[int] = Field(default=None, ge=1)
    # Teto ja composto na prescricao congelada da ficha (inclui a recuperacao
    # projetada para o dia). So RESTRINGE o teto do servidor; um valor maior e
    # ignorado (secao 4: o clamp nunca amplia).
    effective_rpe_ceiling: Optional[int] = Field(default=None, ge=1, le=10)
    # RPE 10 Lote 6/P9: tipo da serie recem-salva (`working` por padrao). So
    # serie de trabalho tem status do motor a explicar; aplicacao de tecnica
    # segue avaliada, sem status.
    set_type: Optional[str] = Field(default=None, max_length=20)
    # R1 (2026-10-20, regras de carga): sessao em andamento e `exercises.id`
    # da serie, para o SERVIDOR ler a faixa de fadiga previa do exercicio (dono
    # `domain/prior_fatigue.py`, leitor `session_fatigue_reader`) com
    # ownership (`activities.user_id`). Ausentes: faixa desconhecida.
    session_id: Optional[int] = Field(default=None, ge=1)
    exercise_id: Optional[int] = Field(default=None, ge=1)
