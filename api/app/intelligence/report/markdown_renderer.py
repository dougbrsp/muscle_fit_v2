"""Renderizador do relatorio em Markdown (GFM), em portugues.

Funcao PURA: recebe o dicionario de `markdown_report_service.
collect_report_data` e devolve texto. Nao le banco, relogio nem ambiente -
o "gerado em" vem dentro de `data`. Toda secao tolera dado ausente: chave
faltando, secao `available: False` ou lista vazia viram a frase
`SEM_DADOS`, nunca uma excecao nem uma tabela de zeros.

Numeros no formato brasileiro (virgula decimal, ponto de milhar). Ausencia
e travessao ("—"), nunca zero.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

from ...domain.set_measures import SetMeasure, format_set_measure
from ...domain.set_sides import WORST_SIDE_RULE_TEXT
from ...domain.training_goal import goal_label, is_default
from ..methodology_volume import STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT

SEM_DADOS = "_Sem dados no período._"
INDISPONIVEL = "_Seção indisponível: não foi possível ler estes dados agora (detalhe em “Qualidade dos dados”)._"
TRACO = "—"

WEEKDAYS = {1: "Segunda", 2: "Terça", 3: "Quarta", 4: "Quinta", 5: "Sexta", 6: "Sábado", 7: "Domingo"}

STATUS_LABELS = {
    "BELOW_MIN": "Abaixo do mínimo",
    "BETWEEN_MIN_TARGET": "Aproximando da meta",
    "ON_TARGET": "Na meta",
    "BETWEEN_TARGET_MAX": "Acima da meta",
    "ABOVE_MAX": "Acima do máximo",
    "BELOW_TARGET": "Abaixo do planejado",
    "ABOVE_TARGET": "Acima do planejado",
    "NO_TARGET": "Sem meta",
    "INSUFFICIENT_DATA": "Dados insuficientes",
}
# Modelo `equipment_series`: a meta e o proprio plano, e "ON_TARGET" quer
# dizer "no planejado" (mesmos rotulos de `frontend/src/lib/dashboard-status.ts`).
PLANNED_STATUS_LABELS = {**STATUS_LABELS, "ON_TARGET": "No planejado"}

TARGET_SOURCE_LABELS = {
    "explicit": "tipo de treino escolhido",
    "assigned_template": "meta do tipo de treino do Modelo atribuído",
    "template_plan": "séries planejadas no próprio Modelo",
    "fallback_generic": "meta genérica (sem Modelo atribuído)",
}

SIGNAL_LABELS = {
    "sleep_duration": "Duração do sono",
    "sleep_regularity": "Regularidade do sono",
    "hrv": "Variabilidade da FC (HRV)",
    "resting_hr": "FC de repouso",
    "form_tsb": "Forma (TSB)",
    "muscle_pain": "Dor muscular (por sessão)",
    "joint_pain": "Dor articular",
    "performance": "Desempenho recente",
}

UNIT_LABELS = {"sessoes": "sessões", "pts": "pts", "x": "×"}

DIRECTION_LABELS = {
    "ABOVE": "↑ acima da baseline",
    "IN_LINE": "→ em linha com a baseline",
    "BELOW": "↓ abaixo da baseline",
}
READING_LABELS = {"FAVORABLE": "favorável", "UNFAVORABLE": "desfavorável", "NEUTRAL": "neutro"}

VOLUME_MODE_LABELS = {
    "percent_budget": "% de orçamento (motor v2)",
    "equipment_series": "Equipamento + séries fixas",
}
INTENT_LABELS = {
    "standard": "Padrão",
    "specialization": "Especialização",
    "maintenance": "Manutenção",
    "cutting": "Cutting (definição)",
}
CONVENTION_LABELS = {"assisted": "assistido", "bodyweight": "peso corporal"}
NO_EQUIPMENT_LABEL = "Sem equipamento"


# ---------------------------------------------------------------------------
# Formatacao
# ---------------------------------------------------------------------------


def fmt_num(value: Any, decimals: int = 1) -> str:
    """`1234.5` -> `1.234,5`. `None` -> travessao. Inteiro exato sai sem
    casas quando `decimals == 0`."""
    if value is None:
        return TRACO
    try:
        number = float(value)
    except (TypeError, ValueError):
        return TRACO
    text = f"{number:,.{decimals}f}"
    return text.replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")


def fmt_int(value: Any) -> str:
    return fmt_num(value, 0)


def fmt_signed(value: Any, decimals: int = 1, unit: str = "") -> str:
    if value is None:
        return TRACO
    number = float(value)
    sign = "+" if number > 0 else ""
    suffix = f" {unit}" if unit else ""
    return f"{sign}{fmt_num(number, decimals)}{suffix}"


def fmt_unit(value: Any, unit: str, decimals: int = 1) -> str:
    if value is None:
        return TRACO
    return f"{fmt_num(value, decimals)} {unit}"


def fmt_pct(fraction: Any, decimals: int = 0) -> str:
    """Fracao 0..1 -> `85%`."""
    if fraction is None:
        return TRACO
    return f"{fmt_num(float(fraction) * 100.0, decimals)}%"


def fmt_date(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10]).strftime("%d/%m/%Y")
        except ValueError:
            return value
    return TRACO


def fmt_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    return fmt_date(value)


def _cell(value: Any) -> str:
    """Celula de tabela GFM: `|` escapado e quebra de linha removida."""
    text = TRACO if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]], align: Optional[Sequence[str]] = None) -> str:
    """Tabela GFM. `align` por coluna: `l` (padrao) ou `r`."""
    align = list(align or ["l"] * len(headers))
    lines = [
        "| " + " | ".join(_cell(h) for h in headers) + " |",
        "| " + " | ".join("---:" if a == "r" else "---" for a in align) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    return "\n".join(lines)


def fmt_pct_value(value: Any, decimals: int = 0) -> str:
    """Percentual ja em 0..100 -> `82%`."""
    if value is None:
        return TRACO
    return f"{fmt_num(value, decimals)}%"


def fmt_minutes(value: Any) -> str:
    return TRACO if value is None else f"{fmt_int(value)} min"


def fmt_load(max_kg: Any, avg_kg: Any) -> str:
    if max_kg is None and avg_kg is None:
        return TRACO
    return f"{fmt_unit(max_kg, 'kg', 1)} / {fmt_unit(avg_kg, 'kg', 1)}"


def _sets(value: Any) -> str:
    """Contagem de series: inteiro sem casas, fracao (tecnica) com 1 casa."""
    if value is None:
        return TRACO
    number = float(value)
    return fmt_int(number) if number.is_integer() else fmt_num(number, 1)


def _daily_table(
    columns: Sequence[tuple[str, str, Any]],
    rows: Iterable[Mapping[str, Any]],
    *,
    drop_empty: bool = False,
) -> str:
    """Tabela diaria. `columns`: (titulo, alinhamento, funcao linha->texto).
    `drop_empty` tira as colunas sem nenhum valor no periodo (a data nunca
    sai) - metrica que a pessoa nao registra nao vira coluna de travessoes."""
    rows = list(rows)
    cells = [[fn(row) for _, _, fn in columns] for row in rows]
    keep = list(range(len(columns)))
    if drop_empty and cells:
        keep = [i for i in keep if i == 0 or any(c[i] != TRACO for c in cells)]
    return table(
        [columns[i][0] for i in keep],
        [[c[i] for i in keep] for c in cells],
        [columns[i][1] for i in keep],
    )


def _section(data: Mapping[str, Any], key: str) -> Optional[Mapping[str, Any]]:
    """Secao disponivel ou `None` (ausente/indisponivel)."""
    section = data.get(key)
    if not isinstance(section, Mapping) or not section.get("available"):
        return None
    return section


def _unavailable_or_empty(data: Mapping[str, Any], key: str) -> str:
    section = data.get(key)
    if isinstance(section, Mapping) and section.get("available") is False:
        return INDISPONIVEL
    return SEM_DADOS


# ---------------------------------------------------------------------------
# Secoes
# ---------------------------------------------------------------------------


def _header(data: Mapping[str, Any]) -> str:
    window = data.get("window") or {}
    lines = [
        "# Relatório MuscleFit",
        "",
        f"- **Período:** {fmt_date(window.get('date_from'))} a {fmt_date(window.get('date_to'))}"
        f" ({fmt_int(window.get('days'))} dias)",
        f"- **Fuso horário:** {data.get('timezone') or TRACO}",
        f"- **Gerado em:** {fmt_datetime(data.get('generated_at'))}",
        "",
        "Relatório só com dados registrados no MuscleFit, sem análise por IA. Os números saem "
        "dos mesmos cálculos das abas do Dashboard. Ausência de registro aparece como “—” ou "
        "“sem dados no período”, nunca como zero.",
        "",
        "Cada seção traz primeiro o resumo do período e, em seguida, o detalhamento diário: uma linha "
        "por data (DD/MM/AAAA, no fuso acima), em ordem cronológica, só nos dias com registro. Nas "
        "seções de grupo muscular e de equipamento, a soma das linhas diárias é o total do período.",
    ]
    return "\n".join(lines)


def _muscles(data: Mapping[str, Any]) -> str:
    out = ["## 1. Treino por grupo muscular"]
    section = _section(data, "muscles")
    groups = [g for g in (section or {}).get("groups") or []]
    if section is None or not groups:
        out.append(_unavailable_or_empty(data, "muscles"))
        return "\n\n".join(out)

    source = section.get("target_source")
    labels = PLANNED_STATUS_LABELS if source == "template_plan" else STATUS_LABELS
    intro = [
        "- **Séries físicas:** séries de trabalho registradas que tocaram o grupo (aquecimento e "
        "série de ajuste não contam).",
        "- **Séries efetivas:** séries ponderadas pelo estímulo de cada exercício no grupo "
        "(1 = direto, frações = indireto/estabilizador), com as técnicas avançadas contadas pela regra "
        "da Fase 5.",
        "- **Tonelagem:** repetições × carga total × peso do estímulo, só nas séries de trabalho. "
        "Exercícios cadastrados como assistidos ou de peso corporal ficam fora.",
        f"- **Meta:** {TARGET_SOURCE_LABELS.get(str(source), str(source or TRACO))}"
        + (f" — Modelo “{section['template_name']}”" if section.get("template_name") else "")
        + ". Faixa em séries efetivas por semana (mín. / alvo / máx.).",
    ]
    goals = _period_goals(data)
    if any(not is_default(goal) for goal in goals):
        # M97 F4: so quando o periodo tem serie de forca (o texto de sempre
        # nao muda num periodo so de hipertrofia). Peso interpolado do dono.
        intro.append(
            f"- **Objetivos no período:** {', '.join(goal_label(goal) for goal in goals)}. Nas séries efetivas "
            f"a série de força vale {fmt_num(STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT, 1)} (peso do volume "
            "hipertrófico); séries físicas e tonelagem contam cheias."
        )
    out.append("\n".join(intro))

    rows = []
    for g in groups:
        meta = (
            TRACO
            if g.get("target") is None
            else f"{fmt_num(g.get('target_min'))} / {fmt_num(g.get('target'))} / {fmt_num(g.get('target_max'))}"
        )
        rows.append(
            [
                g.get("name"),
                g.get("macro_group") or TRACO,
                fmt_int(g.get("physical_sets")),
                fmt_num(g.get("effective_sets")),
                fmt_num(g.get("weekly_effective_sets")),
                fmt_unit(g.get("tonnage_kg"), "kg", 0),
                fmt_num(g.get("avg_rpe")),
                f"{fmt_int(g.get('direct_sessions'))} / {fmt_int(g.get('participation_sessions'))}",
                meta,
                labels.get(str(g.get("status")), str(g.get("status") or TRACO)),
            ]
        )
    out.append(
        table(
            [
                "Grupo",
                "Macro-grupo",
                "Séries físicas",
                "Séries efetivas",
                "Séries efetivas/semana",
                "Tonelagem",
                "RPE médio",
                "Sessões (diretas / total)",
                "Meta semanal",
                "Status",
            ],
            rows,
            ["l", "l", "r", "r", "r", "r", "r", "r", "r", "l"],
        )
    )

    macros = section.get("macro_groups") or []
    if macros:
        out.append("### Subtotais por macro-grupo")
        out.append(
            table(
                ["Macro-grupo", "Grupos", "Séries físicas", "Séries efetivas", "Séries efetivas/semana", "Tonelagem"],
                [
                    [
                        m.get("name"),
                        fmt_int(m.get("groups")),
                        fmt_int(m.get("physical_sets")),
                        fmt_num(m.get("effective_sets")),
                        fmt_num(m.get("weekly_effective_sets")),
                        fmt_unit(m.get("tonnage_kg"), "kg", 0),
                    ]
                    for m in macros
                ],
                ["l", "r", "r", "r", "r", "r"],
            )
        )
        out.append(
            "_Os subtotais somam as linhas da tabela acima. Um mesmo exercício pode contar em mais de um "
            "grupo (ex.: supino no peitoral e no tríceps), então as séries físicas do subtotal não são "
            "o número de séries feitas._"
        )

    out.append("### Detalhamento diário por grupo")
    days = section.get("days") or []
    if days:
        out.append(
            _daily_table(
                [
                    ("Data", "l", lambda d: fmt_date(d.get("local_date"))),
                    ("Grupo", "l", lambda d: d.get("name")),
                    ("Séries físicas", "r", lambda d: _sets(d.get("physical_sets"))),
                    ("Séries efetivas", "r", lambda d: fmt_num(d.get("effective_sets"), 2)),
                    ("Tonelagem", "r", lambda d: fmt_unit(d.get("tonnage_kg"), "kg", 0)),
                    ("RPE médio", "r", lambda d: fmt_num(d.get("avg_rpe"))),
                    ("Exercícios / sessões", "r", lambda d: f"{fmt_int(d.get('exercises'))} / {fmt_int(d.get('sessions'))}"),
                ],
                days,
            )
        )
    else:
        out.append(SEM_DADOS)
    return "\n\n".join(out)


def _equipment_tonnage(item: Mapping[str, Any]) -> str:
    excluded = [CONVENTION_LABELS.get(c, c) for c in item.get("excluded_conventions") or []]
    value = fmt_unit(item.get("tonnage_kg"), "kg", 0)
    if not excluded:
        return value
    label = ", ".join(excluded)
    if item.get("tonnage_kg") is None:
        return f"— ({label}: ver reps)"
    return f"{value} (+ séries {label} fora)"


def _equipment_reps(item: Mapping[str, Any]) -> str:
    """Reps da linha e, quando houve serie POR TEMPO (Lote T3), os segundos
    no texto do dono (`set_measures.format_set_measure`: "180 s")."""
    reps = fmt_int(item.get("reps"))
    seconds = item.get("duration_seconds") or 0
    if not seconds:
        return reps
    return f"{reps} + {format_set_measure(SetMeasure.DURATION, None, seconds)}"


def _equipment(data: Mapping[str, Any]) -> str:
    out = ["## 2. Treino por equipamento"]
    section = _section(data, "equipment")
    period = (section or {}).get("period") or []
    if section is None or not period:
        out.append(_unavailable_or_empty(data, "equipment"))
        return "\n\n".join(out)

    out.append(
        "\n".join(
            [
                "- **Séries:** séries registradas no equipamento (aquecimento e série de ajuste não contam).",
                "- **Séries efetivas:** as mesmas séries, com as técnicas avançadas contadas pela regra da Fase 5 "
                "(sem peso de estímulo: aqui a série não é de um grupo).",
                "- **Tonelagem, carga e reps:** só séries de trabalho; a carga é a registrada (total), sem conversão. "
                "Nos pares cadastrados como assistido ou peso corporal a carga registrada não é a carga movida: "
                "a tonelagem fica fora e valem as reps. Séries por tempo somam os segundos ao lado das reps e, "
                "como as de máximo de repetições, ficam fora da tonelagem.",
                f"- **Unilateral:** {WORST_SIDE_RULE_TEXT}",
                f"- Séries sem equipamento cadastrado aparecem como “{NO_EQUIPMENT_LABEL}”.",
            ]
        )
    )
    out.append("### Resumo do período")
    out.append(_equipment_period_table(period))
    by_goal = section.get("by_goal") or {}
    if len(by_goal) > 1:
        # M97 F4: periodo com mais de um objetivo -> um bloco por objetivo
        # (resumo + detalhamento), cortado das MESMAS linhas do resumo geral.
        for goal, block in by_goal.items():
            label = goal_label(goal)
            out.append(f"### Objetivo: {label} — resumo do período")
            out.append(_equipment_period_table(block.get("period") or []))
            out.append(f"### Objetivo: {label} — detalhamento diário por equipamento")
            out.append(_equipment_days_table(block.get("days") or []))
        return "\n\n".join(out)
    out.append("### Detalhamento diário por equipamento")
    out.append(_equipment_days_table(section.get("days") or []))
    return "\n\n".join(out)


def _period_goals(data: Mapping[str, Any]) -> list[str]:
    """Objetivos presentes no periodo (lidos no bloco de equipamento, que ve
    toda serie registrada), na ordem do dono."""
    return list(((data.get("equipment") or {}).get("goals")) or [])


def _equipment_period_table(period: Sequence[Mapping[str, Any]]) -> str:
    return _daily_table(
        [
            ("Equipamento", "l", lambda e: e.get("equipment")),
            ("Exercícios", "r", lambda e: fmt_int(e.get("exercises"))),
            ("Dias", "r", lambda e: fmt_int(e.get("days"))),
            ("Séries", "r", lambda e: _sets(e.get("physical_sets"))),
            ("Séries efetivas", "r", lambda e: fmt_num(e.get("effective_sets"), 2)),
            ("Tonelagem", "r", _equipment_tonnage),
            ("RPE médio", "r", lambda e: fmt_num(e.get("avg_rpe"))),
            ("Carga máx. / média", "r", lambda e: fmt_load(e.get("load_max"), e.get("load_avg"))),
            ("Reps", "r", _equipment_reps),
        ],
        period,
    )


def _equipment_days_table(days: Sequence[Mapping[str, Any]]) -> str:
    return _daily_table(
        [
            ("Data", "l", lambda e: fmt_date(e.get("local_date"))),
            ("Equipamento", "l", lambda e: e.get("equipment")),
            ("Exercício", "l", lambda e: e.get("exercise")),
            ("Séries", "r", lambda e: _sets(e.get("physical_sets"))),
            ("Séries efetivas", "r", lambda e: fmt_num(e.get("effective_sets"), 2)),
            ("Tonelagem", "r", _equipment_tonnage),
            ("RPE médio", "r", lambda e: fmt_num(e.get("avg_rpe"))),
            ("Carga máx. / média", "r", lambda e: fmt_load(e.get("load_max"), e.get("load_avg"))),
            ("Reps", "r", _equipment_reps),
        ],
        days,
    )


def _signal_value(value: Any, unit: Optional[str]) -> str:
    if value is None:
        return TRACO
    if unit == "%":
        return f"{fmt_num(value, 0)}%"
    if unit in ("0-10", "1-5", "x"):
        return fmt_num(value, 2 if unit == "x" else 1)
    if unit in ("sessoes",):
        return fmt_num(value, 0)
    label = UNIT_LABELS.get(str(unit), unit or "")
    return f"{fmt_num(value, 1)} {label}".strip()


def _recovery(data: Mapping[str, Any]) -> str:
    out = ["## 3. Sono, HRV, FC e recuperação"]
    section = _section(data, "recovery")
    if section is None:
        out.append(_unavailable_or_empty(data, "recovery"))
        return "\n\n".join(out)

    sleep = section.get("sleep") or {}
    wellness = section.get("wellness") or {}
    signals = section.get("signals") or []
    days = section.get("days") or []
    has_signal = any(s.get("observations") for s in signals)
    if not sleep.get("days") and not wellness.get("days") and not has_signal and not days:
        out.append(SEM_DADOS)
        return "\n\n".join(out)

    out.append("### Médias do período")
    averages = [
        ["Sono — duração", fmt_unit(sleep.get("avg_duration_hours"), "h", 2), fmt_int(sleep.get("duration_days"))],
        ["Sono — profundo", fmt_unit(sleep.get("avg_deep_hours"), "h", 2), fmt_int(sleep.get("deep_days"))],
        ["Sono — REM", fmt_unit(sleep.get("avg_rem_hours"), "h", 2), fmt_int(sleep.get("rem_days"))],
        [
            "Sono — regularidade",
            TRACO if sleep.get("avg_regularity_percent") is None else f"{fmt_num(sleep['avg_regularity_percent'], 0)}%",
            fmt_int(sleep.get("regularity_days")),
        ],
        ["HRV", fmt_unit(wellness.get("avg_hrv_ms"), "ms", 1), fmt_int(wellness.get("hrv_days"))],
        ["FC de repouso", fmt_unit(wellness.get("avg_resting_hr_bpm"), "bpm", 1), fmt_int(wellness.get("resting_hr_days"))],
        ["Forma (TSB)", fmt_num(wellness.get("avg_form_tsb"), 1), fmt_int(wellness.get("form_tsb_days"))],
    ]
    out.append(table(["Indicador", "Média", "Dias com dado"], averages, ["l", "r", "r"]))

    if signals:
        baseline_days = section.get("baseline_window_days")
        out.append("### Comparação com a sua baseline")
        out.append(
            "Valor do período = mediana dos dias com registro. Baseline = sua mediana habitual nos "
            f"{fmt_int(baseline_days) if baseline_days else TRACO} dias anteriores ao período. "
            "Sem baseline suficiente, a tendência fica em branco."
        )
        rows = []
        for s in signals:
            unit = s.get("unit")
            direction = DIRECTION_LABELS.get(str(s.get("direction")))
            reading = READING_LABELS.get(str(s.get("reading")))
            trend = TRACO if direction is None else (f"{direction} ({reading})" if reading else direction)
            rows.append(
                [
                    SIGNAL_LABELS.get(str(s.get("code")), s.get("label") or s.get("code")),
                    _signal_value(s.get("current"), unit),
                    _signal_value(s.get("baseline"), unit),
                    trend,
                    fmt_int(s.get("observations")),
                ]
            )
        out.append(table(["Sinal", "Período", "Baseline", "Tendência", "Dias com dado"], rows, ["l", "r", "r", "l", "r"]))
        if any(str(s.get("code")) == "performance" for s in signals):
            # Mesma base da prontidao (`exercise_performance`): o sinal nao e
            # uma mediana de dias contra a baseline habitual como os demais.
            out.append(
                "Desempenho recente: última sessão de cada exercício contra a anterior comparável "
                "(mesmo exercício, equipamento e convenção de carga), agregada por grupo muscular; "
                "baseline = 1,00. Nessa linha, \"Dias com dado\" conta grupos musculares."
            )

    out.append("### Detalhamento diário")
    if days:
        out.append(
            "Check-in na escala 1–5 (energia, dor, estresse, humor). Dor muscular e articular: a maior "
            "registrada nos exercícios do dia. Colunas sem nenhum registro no período não aparecem."
        )
        out.append(
            _daily_table(
                [
                    ("Data", "l", lambda d: fmt_date(d.get("local_date"))),
                    ("Sono", "r", lambda d: fmt_unit(d.get("sleep_hours"), "h", 2)),
                    ("Profundo", "r", lambda d: fmt_unit(d.get("deep_hours"), "h", 2)),
                    ("REM", "r", lambda d: fmt_unit(d.get("rem_hours"), "h", 2)),
                    ("Regularidade", "r", lambda d: fmt_pct_value(d.get("regularity_percent"))),
                    ("HRV", "r", lambda d: fmt_unit(d.get("hrv_ms"), "ms", 1)),
                    ("FC repouso", "r", lambda d: fmt_unit(d.get("resting_hr_bpm"), "bpm", 0)),
                    ("TSB (forma)", "r", lambda d: fmt_num(d.get("form_tsb"), 1)),
                    ("Readiness", "r", lambda d: fmt_num(d.get("readiness"), 0)),
                    ("Energia", "r", lambda d: fmt_num(d.get("energy"), 0)),
                    ("Dor (check-in)", "r", lambda d: fmt_num(d.get("soreness"), 0)),
                    ("Estresse", "r", lambda d: fmt_num(d.get("stress"), 0)),
                    ("Humor", "r", lambda d: fmt_num(d.get("mood"), 0)),
                    ("Dor muscular", "r", lambda d: fmt_num(d.get("muscle_pain"), 0)),
                    ("Dor articular", "r", lambda d: fmt_num(d.get("joint_pain"), 0)),
                ],
                days,
                drop_empty=True,
            )
        )
    else:
        out.append(SEM_DADOS)
    return "\n\n".join(out)


def _body(data: Mapping[str, Any]) -> str:
    out = ["## 4. Corpo"]
    section = _section(data, "body")
    if section is None:
        out.append(_unavailable_or_empty(data, "body"))
        return "\n\n".join(out)

    weight = section.get("weight") or {}
    bio = section.get("bioimpedance") or {}
    measurements = section.get("measurements") or []
    body_daily = section.get("daily") or {}
    if (
        not weight.get("count")
        and not bio.get("count")
        and not measurements
        and not body_daily.get("days")
        and not body_daily.get("measurement_days")
    ):
        out.append(SEM_DADOS)
        return "\n\n".join(out)

    out.append("### Peso")
    if weight.get("count"):
        out.append(
            table(
                ["Início", "Fim", "Média móvel 7 dias (fim)", "Variação da média móvel", "Pesagens"],
                [
                    [
                        f"{fmt_unit(weight.get('first_kg'), 'kg', 1)} ({fmt_date(weight.get('first_date'))})",
                        f"{fmt_unit(weight.get('last_kg'), 'kg', 1)} ({fmt_date(weight.get('last_date'))})",
                        fmt_unit(weight.get("ma7_kg"), "kg", 1),
                        fmt_signed(weight.get("change_kg"), 1, "kg")
                        + (
                            f" ({fmt_signed(weight.get('change_pct'), 1)}%)"
                            if weight.get("change_pct") is not None
                            else ""
                        ),
                        fmt_int(weight.get("count")),
                    ]
                ],
                ["r", "r", "r", "r", "r"],
            )
        )
    else:
        out.append(SEM_DADOS)

    out.append("### Bioimpedância")
    if bio.get("count"):
        out.append(
            table(
                ["Indicador", "Última leitura", "Variação no período"],
                [
                    [
                        "Gordura corporal",
                        TRACO if bio.get("body_fat_percent") is None else f"{fmt_num(bio['body_fat_percent'], 1)}%",
                        fmt_signed(bio.get("body_fat_change"), 1, "p.p."),
                    ],
                    [
                        "Massa muscular",
                        fmt_unit(bio.get("muscle_mass_kg"), "kg", 1),
                        fmt_signed(bio.get("muscle_mass_change"), 1, "kg"),
                    ],
                    [
                        "Água corporal",
                        TRACO if bio.get("body_water_percent") is None else f"{fmt_num(bio['body_water_percent'], 1)}%",
                        TRACO,
                    ],
                ],
                ["l", "r", "r"],
            )
        )
        out.append(f"{fmt_int(bio.get('count'))} leitura(s); a última em {fmt_date(bio.get('last_date'))}.")
    else:
        out.append(SEM_DADOS)

    out.append("### Medidas")
    if measurements:
        out.append(
            table(
                ["Local", "Última medida", "Data", "Variação no período", "Medições"],
                [
                    [
                        m.get("label"),
                        fmt_unit(m.get("last_cm"), "cm", 1),
                        fmt_date(m.get("last_date")),
                        fmt_signed(m.get("change_cm"), 1, "cm"),
                        fmt_int(m.get("count")),
                    ]
                    for m in measurements
                ],
                ["l", "r", "r", "r", "r"],
            )
        )
    else:
        out.append(SEM_DADOS)

    out.append("### Detalhamento diário — peso e bioimpedância")
    days = body_daily.get("days") or []
    if days:
        out.append(
            "Peso: uma pesagem por dia (balança de bioimpedância > registro manual > Intervals.icu). "
            "Bioimpedância: a última leitura do dia. Colunas sem nenhum registro no período não aparecem."
        )
        out.append(
            _daily_table(
                [("Data", "l", lambda d: fmt_date(d.get("local_date"))),
                 ("Peso", "r", lambda d: fmt_unit(d.get("weight_kg"), "kg", 1))]
                + [
                    (label, "r", (lambda f, unit, dec: lambda d: _body_value(d.get(f), unit, dec))(field, unit, dec))
                    for field, label, unit, dec in BODY_FIELDS
                ],
                days,
                drop_empty=True,
            )
        )
    else:
        out.append(SEM_DADOS)

    out.append("### Detalhamento diário — medidas")
    sites = body_daily.get("measurement_sites") or []
    measurement_days = body_daily.get("measurement_days") or []
    if sites and measurement_days:
        out.append(
            _daily_table(
                [("Data", "l", lambda d: fmt_date(d.get("local_date")))]
                + [
                    (site["label"], "r", (lambda key: lambda d: fmt_unit((d.get("values") or {}).get(key), "cm", 1))(site["site"]))
                    for site in sites
                ],
                measurement_days,
            )
        )
    else:
        out.append(SEM_DADOS)
    return "\n\n".join(out)


BODY_FIELDS: tuple[tuple[str, str, str, int], ...] = (
    ("bmi", "IMC", "", 1),
    ("body_fat_percent", "% gordura", "%", 1),
    ("fat_mass_kg", "Massa gorda", "kg", 1),
    ("fat_free_mass_kg", "Massa magra", "kg", 1),
    ("muscle_mass_kg", "Massa muscular", "kg", 1),
    ("skeletal_muscle_percent", "% músculo esquelético", "%", 1),
    ("body_water_percent", "% água", "%", 1),
    ("visceral_fat_rating", "Gordura visceral", "", 1),
    ("bone_mass_kg", "Massa óssea", "kg", 1),
    ("bmr_kcal", "TMB", "kcal", 0),
    ("subcutaneous_fat_percent", "% gordura subcutânea", "%", 1),
    ("protein_percent", "% proteína", "%", 1),
    ("metabolic_age", "Idade metabólica", "anos", 0),
)


def _body_value(value: Any, unit: str, decimals: int) -> str:
    if value is None:
        return TRACO
    if unit == "%":
        return fmt_pct_value(value, decimals)
    return fmt_unit(value, unit, decimals) if unit else fmt_num(value, decimals)


def _cardio(data: Mapping[str, Any]) -> str:
    out = ["## 5. Cardio"]
    section = _section(data, "cardio")
    if section is None:
        out.append(_unavailable_or_empty(data, "cardio"))
        return "\n\n".join(out)

    totals = section.get("totals") or {}
    strength = section.get("strength_hr") or {}
    if not section.get("intervals_configured"):
        out.append("_O Intervals.icu não está configurado no seu perfil: o cardio sincronizado não aparece aqui._")

    if totals.get("sessions"):
        out.append("### Totais do período")
        out.append(
            table(
                ["Sessões", "Minutos", "Distância", "Calorias", "Carga (Intervals)", "FC média", "FC máxima"],
                [
                    [
                        fmt_int(totals.get("sessions")),
                        fmt_int(totals.get("minutes")),
                        fmt_unit(totals.get("distance_km"), "km", 1),
                        fmt_unit(totals.get("calories_kcal"), "kcal", 0),
                        fmt_num(totals.get("training_load"), 0),
                        fmt_unit(totals.get("avg_hr_bpm"), "bpm", 0),
                        fmt_unit(totals.get("max_hr_bpm"), "bpm", 0),
                    ]
                ],
                ["r"] * 7,
            )
        )
        sports = section.get("by_sport") or []
        if sports:
            out.append("### Por modalidade")
            out.append(
                table(
                    ["Modalidade", "Sessões", "Minutos", "Distância", "Calorias", "FC média", "FC máxima"],
                    [
                        [
                            s.get("sport_type"),
                            fmt_int(s.get("sessions")),
                            fmt_int(s.get("minutes")),
                            fmt_unit(s.get("distance_km"), "km", 1),
                            fmt_unit(s.get("calories_kcal"), "kcal", 0),
                            fmt_unit(s.get("avg_hr_bpm"), "bpm", 0),
                            fmt_unit(s.get("max_hr_bpm"), "bpm", 0),
                        ]
                        for s in sports
                    ],
                    ["l", "r", "r", "r", "r", "r", "r"],
                )
            )
        if totals.get("sessions_with_hr", 0) < totals.get("sessions", 0):
            out.append(
                f"_{fmt_int(totals['sessions'] - totals.get('sessions_with_hr', 0))} de {fmt_int(totals['sessions'])} "
                "sessões de cardio sem frequência cardíaca: contam nos minutos e ficam fora da média de FC._"
            )
    else:
        out.append("### Totais do período")
        out.append(SEM_DADOS)

    out.append("### Sessões de cardio (detalhamento diário)")
    sessions = section.get("sessions") or []
    if sessions:
        out.append(
            _daily_table(
                [
                    ("Data", "l", lambda c: fmt_date(c.get("local_date"))),
                    ("Modalidade", "l", lambda c: c.get("sport_type")),
                    ("Atividade", "l", lambda c: c.get("name")),
                    ("Duração", "r", lambda c: fmt_minutes(c.get("minutes"))),
                    ("Distância", "r", lambda c: fmt_unit(c.get("distance_km"), "km", 2)),
                    ("FC média", "r", lambda c: fmt_unit(c.get("avg_hr_bpm"), "bpm", 0)),
                    ("FC máx.", "r", lambda c: fmt_unit(c.get("max_hr_bpm"), "bpm", 0)),
                    ("Calorias", "r", lambda c: fmt_unit(c.get("calories_kcal"), "kcal", 0)),
                    ("Carga", "r", lambda c: fmt_num(c.get("training_load"), 0)),
                ],
                sessions,
            )
        )
    else:
        out.append(SEM_DADOS)

    out.append("### Frequência cardíaca nos treinos de musculação")
    if strength.get("sessions_total"):
        out.append(
            table(
                ["Sessões", "Com FC", "Cobertura", "FC média", "FC máxima"],
                [
                    [
                        fmt_int(strength.get("sessions_total")),
                        fmt_int(strength.get("sessions_with_hr")),
                        TRACO if strength.get("coverage_pct") is None else f"{fmt_num(strength['coverage_pct'], 0)}%",
                        fmt_unit(strength.get("avg_hr_bpm"), "bpm", 0),
                        fmt_unit(strength.get("max_hr_bpm"), "bpm", 0),
                    ]
                ],
                ["r"] * 5,
            )
        )
    else:
        out.append(SEM_DADOS)
    return "\n\n".join(out)


def _slot_label(slot: Mapping[str, Any]) -> str:
    weekday = WEEKDAYS.get(slot.get("weekday")) if isinstance(slot.get("weekday"), int) else None
    name = slot.get("name") or f"Dia {slot.get('sequence')}"
    return f"{name} ({weekday})" if weekday else str(name)


def _item_label(item: Mapping[str, Any]) -> str:
    if item.get("kind") == "cardio":
        # M98 C0: texto canônico do dono (`cardio_prescription.describe`),
        # pronto no serviço; dado antigo sem o texto cai nos minutos.
        text = item.get("cardio_text")
        if text:
            return f"Cardio — {text}"
        minutes = item.get("cardio_minutes")
        return f"Cardio{f' — {minutes} min' if minutes else ''}"
    parts = []
    if item.get("exercise"):
        parts.append(str(item["exercise"]))
        if item.get("equipment"):
            parts[-1] += f" ({item['equipment']})"
    elif item.get("groups"):
        parts.append(", ".join(item["groups"]))
    else:
        parts.append(TRACO)
    if item.get("sets") is not None:
        parts.append(f"{fmt_int(item['sets'])} séries")
    if item.get("reps_target"):
        parts.append(f"{item['reps_target']} reps")
    if item.get("technique"):
        applications = item.get("technique_applications")
        parts.append(f"técnica: {item['technique']}" + (f" ×{applications}" if applications else ""))
    return " · ".join(parts)


def _timeline_session(row: Mapping[str, Any]) -> Any:
    label = row.get("deload_label")
    if not label:
        return row.get("name")
    return f"{row.get('name') or TRACO} ({label})"


def _cycles(data: Mapping[str, Any]) -> str:
    out = ["## 6. Ciclos de treino"]
    section = _section(data, "cycles")
    if section is None:
        out.append(_unavailable_or_empty(data, "cycles"))
        return "\n\n".join(out)

    segments = section.get("segments") or []
    out.append("### Modelos vigentes no período")
    if segments:
        out.append(
            table(
                ["De", "Até", "Modelo", "Tipo de treino", "Modo de volume", "Sessões feitas"],
                [
                    [
                        fmt_date(s.get("date_from")),
                        fmt_date(s.get("date_to")),
                        s.get("template_name") or "Sem Modelo atribuído",
                        s.get("training_type") or TRACO,
                        VOLUME_MODE_LABELS.get(str(s.get("volume_mode")), s.get("volume_mode") or TRACO),
                        fmt_int(s.get("sessions")),
                    ]
                    for s in segments
                ],
                ["r", "r", "l", "l", "l", "r"],
            )
        )
    else:
        out.append(SEM_DADOS)

    adherence = section.get("adherence") or {}
    out.append("### Adesão")
    if adherence.get("has_template"):
        out.append(
            table(
                ["Esperadas", "Feitas", "Com ficha do Modelo", "Fora do Modelo", "Faltaram", "Adesão"],
                [
                    [
                        fmt_num(adherence.get("expected_sessions"), 1),
                        fmt_int(adherence.get("completed_sessions")),
                        fmt_int(adherence.get("planned_sessions")),
                        fmt_int(adherence.get("unplanned_sessions")),
                        fmt_num(adherence.get("missed_sessions"), 1),
                        fmt_pct(adherence.get("rate")),
                    ]
                ],
                ["r"] * 6,
            )
        )
    elif adherence.get("completed_sessions"):
        out.append(
            f"Sem Modelo atribuído: {fmt_int(adherence.get('completed_sessions'))} sessão(ões) feitas, "
            "sem plano para comparar."
        )
    else:
        out.append(SEM_DADOS)

    out.append("### Linha do tempo dos treinos (detalhamento diário)")
    timeline = section.get("timeline") or []
    if timeline:
        out.append(
            "Uma linha por sessão de musculação. “Dia do Modelo” só aparece quando a sessão foi feita a "
            "partir de uma ficha do Modelo. FC: a medida na própria sessão ou, sem ela, a do Intervals.icu "
            "associada ao treino. RPE: média das séries registradas."
        )
        out.append(
            _daily_table(
                [
                    ("Data", "l", lambda t: fmt_date(t.get("local_date"))),
                    ("Modelo vigente", "l", lambda t: t.get("template_name") or "Sem Modelo atribuído"),
                    ("Dia do Modelo", "l", lambda t: t.get("slot")),
                    # M97 F4: sessao de deload sinalizada ao lado do nome.
                    ("Sessão", "l", _timeline_session),
                    ("Duração", "r", lambda t: fmt_minutes(t.get("minutes"))),
                    ("Séries", "r", lambda t: fmt_int(t.get("physical_sets"))),
                    ("FC média", "r", lambda t: fmt_unit(t.get("avg_hr_bpm"), "bpm", 0)),
                    ("FC máx.", "r", lambda t: fmt_unit(t.get("max_hr_bpm"), "bpm", 0)),
                    ("RPE médio", "r", lambda t: fmt_num(t.get("avg_rpe"))),
                ],
                timeline,
            )
        )
    else:
        out.append(SEM_DADOS)

    template = section.get("current_template")
    out.append("### Modelo atual")
    if not template:
        out.append("_Nenhum Modelo de treino atribuído hoje._")
        return "\n\n".join(out)

    intent = INTENT_LABELS.get(str(template.get("intent")), str(template.get("intent") or TRACO))
    targets = template.get("intent_targets") or []
    lines = [
        f"- **Nome:** {template.get('name') or TRACO}",
        f"- **Tipo de treino:** {template.get('training_type') or TRACO}",
        f"- **Modo de volume:** {VOLUME_MODE_LABELS.get(str(template.get('volume_mode')), template.get('volume_mode') or TRACO)}",
        f"- **Intenção metodológica:** {intent}" + (f" (grupos-alvo: {', '.join(targets)})" if targets else ""),
    ]
    out.append("\n".join(lines))

    slots = template.get("slots") or []
    if not slots:
        out.append("_O Modelo não tem dias cadastrados._")
        return "\n\n".join(out)
    out.append(
        table(
            ["#", "Dia", "Grupos", "Séries-alvo", "Técnicas"],
            [
                [
                    fmt_int(slot.get("sequence")),
                    _slot_label(slot) + (" — descanso" if slot.get("slot_type") == "descanso" else ""),
                    ", ".join(slot.get("groups") or []) or TRACO,
                    TRACO if slot.get("slot_type") == "descanso" else fmt_int(slot.get("target_sets")),
                    ", ".join(
                        dict.fromkeys(i["technique"] for i in slot.get("items") or [] if i.get("technique"))
                    )
                    or TRACO,
                ]
                for slot in slots
            ],
            ["r", "l", "l", "r", "l"],
        )
    )
    for slot in slots:
        items = slot.get("items") or []
        if not items:
            continue
        out.append(f"**{_slot_label(slot)}**\n\n" + "\n".join(f"- {_item_label(i)}" for i in items))
    return "\n\n".join(out)


def _quality(data: Mapping[str, Any]) -> str:
    out = ["## 7. Qualidade dos dados"]
    quality = data.get("quality") or {}
    lines: list[str] = []

    unattributed = quality.get("unattributed_sets")
    if unattributed:
        lines.append(
            f"- **{fmt_int(unattributed)} série(s) sem grupo muscular:** o exercício não tem contribuição "
            "muscular cadastrada, então essas séries não aparecem na seção 1 (mas aparecem na seção 2, por equipamento)."
        )
    excluded = quality.get("tonnage_excluded_sets")
    if excluded:
        by_convention = quality.get("tonnage_excluded_by_convention") or {}
        detail = ", ".join(
            f"{fmt_int(n)} {CONVENTION_LABELS.get(code, code)}" for code, n in by_convention.items() if n
        )
        lines.append(
            f"- **{fmt_int(excluded)} série(s) fora da tonelagem**"
            + (f" ({detail})" if detail else "")
            + ": nesses aparelhos o número registrado não é a carga movida."
        )
    unknown = quality.get("unknown_load_conventions") or []
    if unknown:
        lines.append(
            "- **Convenção de carga desconhecida** ("
            + ", ".join(unknown)
            + "): tratada como carga total na tonelagem."
        )
    empty = quality.get("empty_sources") or []
    if empty:
        lines.append("- **Fontes sem registro no período:** " + "; ".join(empty) + ".")
    unavailable = quality.get("unavailable_sections") or []
    if unavailable:
        lines.append(
            "- **Seções que não puderam ser lidas agora:** "
            + "; ".join(unavailable)
            + ". Tente gerar o relatório de novo em instantes."
        )
    invariant = quality.get("daily_invariant_problems") or []
    if invariant:
        lines.append(
            "- **Detalhamento diário não fecha com o total do período:** " + "; ".join(invariant) + "."
        )
    if quality.get("cardio_truncated"):
        lines.append("- **Cardio:** o período tem mais sessões do que o limite lido; os totais são um piso.")

    if not lines:
        lines.append("- Nenhuma lacuna encontrada nos dados deste período.")
    out.append("\n".join(lines))
    return "\n\n".join(out)


def render_markdown(data: Mapping[str, Any]) -> str:
    """Relatorio completo em Markdown (GFM). Termina com uma quebra de linha."""
    parts = [
        _header(data),
        _muscles(data),
        _equipment(data),
        _recovery(data),
        _body(data),
        _cardio(data),
        _cycles(data),
        _quality(data),
    ]
    return "\n\n".join(parts).rstrip() + "\n"


__all__ = ["SEM_DADOS", "fmt_num", "render_markdown", "table"]
