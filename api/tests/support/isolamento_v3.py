"""Isolamento dos testes de integracao do Novo Dashboard (v3).

Duas armadilhas mordem esta suite, e as duas vivem no mesmo fato: **o schema
de teste e UM SO para a execucao inteira** (`tests/conftest.py` cria
`<stem>_run<token>_test` por EXECUCAO, nao por arquivo), e todo arquivo
trunca `users` no seed - o que faz o `AUTO_INCREMENT` voltar a 1 e o
"usuario 1" de um arquivo herdar o que outro arquivo deixou para o dele.

**1) Tabela nao truncada = dado de outro arquivo no seu payload.**
Ja aconteceu duas vezes no PR #199: `test_dashboard_overview.py` nao truncava
`body_measurements` e passou a ler a cintura deixada pela fixture do arquivo
de performance (88,0 cm) e pelo `test_dashboard_body.py` (90,0 cm). A regra
que evita a terceira vez: **todo arquivo trunca tudo o que le ou escreve** -
e "o que le" inclui as tabelas lidas pelos servicos que o endpoint chama, nao
so as que o seed insere. `TABELAS_V3` e essa lista, em um lugar so, para o
proximo arquivo nascer isolado sem ter que redescobrir a lista.

**2) Relogio vivo dentro de um payload comparado duas vezes.**
`hours_since_last_stimulus`, `remaining_hours`, `recovery_pct` e
`reassess_after_hours` saem de `now - last_trained_at` e sao arredondados a
2 casas de HORA (degrau de 36 s). Um teste que faz a MESMA requisicao nos
dois backends e compara os payloads falha sempre que as duas chamadas caem em
lados diferentes de um degrau - flake real, medido: duas chamadas a 40 s de
distancia divergem em 5 campos. Quem compara payload usa `congelar_relogio`.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable, Sequence

from zoneinfo import ZoneInfo

from app.intelligence import muscle_recovery_service, volume_service
from app.intelligence.dashboard import time_windows
from app.timezones import DEFAULT_TIMEZONE

# ---------------------------------------------------------------------------
# Tabelas
# ---------------------------------------------------------------------------

# Tudo que a suite v3 le OU escreve, na ordem filho -> pai (as FKs ficam
# desligadas durante o TRUNCATE, mas a ordem mantem o `DELETE` manual viavel).
#
# Vem do que os SERVICOS leem, nao so do que os seeds inserem:
# - `exposure_service`: exercise_sets, exercises, activities, equipment*,
#   gym_equipment*, exercise_external_mappings, progression_*;
# - `adherence_service`: workout_plans, workout_plan_exercises,
#   workout_plan_template_slots;
# - `volume_service`/`recovery_read_model`: contribuicoes, catalogo,
#   muscle_recovery_coefficients, workout_plans;
# - `recovery_signals_service`: sleep_metrics, intervals_wellness, context_events,
#   daily_checkins (F2.2: os quatro escores subjetivos viram sinais, e um
#   check-in deixado por outro arquivo entra na baseline do seguinte);
# - `overview_service`/`insights` (F2.2): dashboard_alert_states - o cooldown
#   GRAVA na leitura, entao um estado deixado por outro arquivo SILENCIA
#   insight que o teste seguinte espera ver (o erro mais traicoeiro da lista:
#   a resposta fica valida, so falta um item);
# - `context_service` (F2.1): context_events + as fontes dos DERIVADOS
#   (workout_plan_template_assignments, activities/gyms) - um evento de
#   contexto deixado por outro arquivo entra na timeline e nos insights do
#   seguinte;
# - `body_service`: body_measurements, body_composition, daily_biometrics,
#   intervals_wellness, workout_plan_template_assignments (marcos);
# - `muscle_summary_service`: training_types, training_type_muscle_group_targets;
# - `cardio_service` (F2.3): intervals_activities (o cache do Intervals.icu) e
#   activity_intervals_link (o casamento treino <-> atividade WeightTraining
#   que o job grava). O link cita `activities` E `intervals_activities`, entao
#   ele vem ANTES das duas na ordem filho -> pai; uma linha de link deixada
#   por outro arquivo apontaria para uma `activities.id` reaproveitada pelo
#   AUTO_INCREMENT e daria FC a um treino que nunca a teve.
TABELAS_V3: tuple[str, ...] = (
    "activity_intervals_link",
    "exercise_sets",
    "exercise_external_mappings",
    "exercises",
    "progression_decisions",
    "progression_tracks",
    # M16: auditoria de geracao de ficha. Filha de `workout_plans` (FK ON
    # DELETE SET NULL) e de `users` (CASCADE), entao fica logo antes das duas
    # - `truncar` desliga FOREIGN_KEY_CHECKS, mas a lista continua legivel na
    # ordem de dependencia.
    "generation_runs",
    "workout_plan_exercises",
    "workout_plans",
    "activities",
    # Modelo de Ficha: varios arquivos afirmam o contrato "sem Modelo
    # atribuido" (`window=cycle` -> 400, `has_template: False`). Sem limpar
    # estas tabelas, UMA linha deixada por outro arquivo da suite faz o usuario
    # recem-criado (mesmo id, porque `users` acabou de ser truncada) aparecer
    # COM Modelo, e a rota passa a responder 200 corretamente - o teste falha
    # sem que nada no codigo tenha mudado. Falha vista no CI e nao em DEV,
    # porque a ordem de execucao muda. (Licao registrada primeiro em
    # `test_dashboard_muscles.py` e de novo no PR #200, em dois arquivos; e
    # por ela estar em uma lista SO que ela vale para todos.)
    # Fase 4, PR H (056): intencao declarada por Modelo. A filha (alvos) cita a
    # mae (intencao) e `muscle_groups`; a mae cita `workout_plan_templates` -
    # as duas vem antes dos pais na ordem filho -> pai.
    "workout_plan_template_methodology_intent_targets",
    "workout_plan_template_methodology_intent",
    "workout_plan_template_slot_items",
    "workout_plan_template_slots",
    # 065: versoes do Modelo. Mae dos slots (`version_id`), filha de
    # `workout_plan_templates` - fica entre as duas.
    "workout_plan_template_versions",
    # 067: rascunho do Modelo (filha de `workout_plan_templates`). Sem
    # truncar, um rascunho deixado por outro arquivo apareceria como
    # `has_draft` no Modelo de mesmo id do arquivo seguinte.
    "workout_plan_template_drafts",
    "workout_plan_template_assignments",
    "workout_plan_templates",
    "training_type_muscle_group_targets",
    "training_types",
    "equipment_exercises",
    "equipment_exercise_mappings",
    "gym_equipment",
    "gym_equipment_catalog",
    "equipment",
    "gyms",
    "context_events",
    # M97 D (094): o overview le os periodos de deload (supressao de insights);
    # um periodo deixado por outro arquivo silenciaria "abaixo da meta" no seguinte.
    "deload_periods",
    "daily_checkins",
    "dashboard_alert_states",
    "body_measurements",
    "body_composition",
    "daily_biometrics",
    "intervals_activities",
    "intervals_wellness",
    # M98 C2 (097): zonas de FC do atleta (cache do Intervals; sem FK).
    "intervals_athlete_hr_zones",
    # M98 C4: o cardio do hub le a ultima recomendacao v3 (e, ate o M99, o PAI
    # manual; hoje o PAI e estimado de intervals_activities); uma linha deixada
    # por outro arquivo viraria "proxima sessao" ou PAI do seguinte.
    "manual_effort_metrics",
    "cardio_recommendations",
    "sleep_metrics",
    "muscle_recovery_coefficients",
    "exercise_legacy_id_map",
    "exercise_muscle_contributions",
    "exercise_aliases",
    "exercise_catalog",
    # Legado em aposentadoria (M13): ninguem mais le nem escreve nela, mas ela
    # continua no schema ate o PR que a remove - e a auditoria da 053 e o
    # registro de quem so existe la.
    "exercise_zepp_mappings_retirement_audit",
    "exercise_zepp_mappings",
    "muscle_groups",
    # Fase 6 (057): cadastro de macro-grupos. `muscle_groups.volume_class_id`
    # cita `muscle_volume_classes` e as faixas sao filhas dela - por isso as
    # duas vem DEPOIS de `muscle_groups`. Desde o PR 2 o motor le o cadastro:
    # um macro-grupo editado por outro arquivo mudaria a avaliacao do
    # seguinte. Quem precisa do seed da 057 regrava com
    # `tests.support.methodology_paridade.semear_macro_grupos`.
    # 073: faixas e metas semanais dos modos de contagem != primary_only (filhas).
    "muscle_volume_class_mode_ranges",
    "muscle_volume_class_weekly_targets",
    "muscle_volume_class_ranges",
    "muscle_volume_classes",
    "users",
)

_TABELAS_EXISTENTES_SQL = (
    "SELECT TABLE_NAME AS t FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()"
)


def tabelas_existentes(conn: Any) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(_TABELAS_EXISTENTES_SQL)
        return {str(row["t"]) for row in (cur.fetchall() or [])}


def truncar(conn: Any, tabelas: Sequence[str] = TABELAS_V3, *, extras: Iterable[str] = ()) -> None:
    """Esvazia `tabelas` (mais `extras`) numa conexao NEUTRA (PyMySQL direto).

    Tabela ausente no schema e ignorada - a lista e a uniao do que a suite v3
    usa, e um recorte de schema pode nao ter tudo (ex.: as do grupo B, que so
    nascem na 027 via `ensure_grupo_b_tables`). Ignorar mantem o helper util
    em qualquer estado de schema, em vez de obrigar cada arquivo a manter a
    propria lista - que foi exatamente como as duas armadilhas nasceram."""
    alvo = [*tabelas, *extras]
    presentes = tabelas_existentes(conn)
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for tabela in alvo:
            if tabela in presentes:
                cur.execute(f"TRUNCATE TABLE `{tabela}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()


# ---------------------------------------------------------------------------
# Relogio congelado (para comparar payloads)
# ---------------------------------------------------------------------------

_REAL_DATETIME = _dt.datetime
_REAL_DATE = _dt.date


class _DatetimeMeta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DATETIME)


class _DateMeta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DATE)


def congelar_relogio(monkeypatch: Any, agora: _dt.datetime | None = None) -> _dt.datetime:
    """Congela `datetime.now()`/`date.today()` nos modulos que derivam horas
    do relogio (`volume_service` e `muscle_recovery_service`) E o relogio de
    `time_windows.user_now`/`user_today` - a hora local do usuario usada por
    `analytics_dashboard` (`now_local`), `volume_service._user_now`,
    `insights_core`, `reports` e `workout_sessions`. Sem isso o `now_local`
    andava entre a chamada do pymysql e a do shim (63,45h vs 63,44h em
    `hours_since_last_stimulus`).

    `agora` e a hora de PAREDE do usuario no fuso default
    (`DEFAULT_TIMEZONE`): `user_now(DEFAULT_TIMEZONE)` devolve exatamente
    `agora`; outros fusos recebem o mesmo instante convertido.

    `agora` default = o instante REAL da chamada: o congelamento nao move os
    dados no tempo (o seed e relativo a "hoje"), so garante que duas chamadas
    seguidas enxerguem o MESMO instante. Sem isso,
    `hours_since_last_stimulus` e companhia mudam entre a chamada do pymysql e
    a do shim, e a comparacao de payload vira ruido.

    Devolve o instante congelado, para o teste poder usa-lo se precisar.

    O default e a hora de PAREDE do usuario (`user_now(DEFAULT_TIMEZONE)`),
    nao `datetime.now()` do processo: no runner do GitHub (UTC) o relogio do
    processo esta 3h a frente de Brasilia, e entre 21:00 e 00:00 o "hoje"
    congelado virava o dia seguinte ao `HOJE` com que os testes semeiam -
    a janela andava um dia (M42). Lido ANTES do patch, com o relogio real."""
    congelado = agora or time_windows.user_now(DEFAULT_TIMEZONE)

    class _Datetime(_REAL_DATETIME, metaclass=_DatetimeMeta):
        @classmethod
        def now(cls, tz=None):
            return congelado

    class _Date(_REAL_DATE, metaclass=_DateMeta):
        @classmethod
        def today(cls):
            return congelado.date()

    congelado_utc = congelado.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE)).astimezone(_dt.timezone.utc)

    class _DatetimeUsuario(_REAL_DATETIME, metaclass=_DatetimeMeta):
        @classmethod
        def now(cls, tz=None):
            return congelado if tz is None else congelado_utc.astimezone(tz)

    for modulo in (volume_service, muscle_recovery_service):
        monkeypatch.setattr(modulo, "datetime", _Datetime)
        monkeypatch.setattr(modulo, "date", _Date)
    # `user_now` e importado por nome em varios modulos; congelar o `datetime`
    # do proprio `time_windows` cobre todos de uma vez.
    monkeypatch.setattr(time_windows, "datetime", _DatetimeUsuario)
    return congelado


__all__ = ["TABELAS_V3", "congelar_relogio", "tabelas_existentes", "truncar"]
