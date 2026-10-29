"""Comparativos POR SESSAO e mapa de calor de `/analytics/dashboard/muscles`
(F2.5 / M34) - as regras que nao dependem de banco.

O que cada bloco protege:

1. **A unidade e SESSAO, nao janela.** T-0 e a ultima sessao em que o GRUPO
   apareceu. Dois treinos no mesmo dia sao T-0 e T-1; duas semanas sem treinar
   nao criam buraco nenhum. Foi misturar essas duas leituras sem rotulo, na
   `/volume-grupamentos`, que o projeto decidiu nao repetir.
2. **Series preparatorias ficam fora, inclusive do RPE** - garantido pela
   cadeia de JOIN (`sql_not_preparatory`), afirmado aqui pelo lado de fora: o
   montador nunca ve uma serie de aquecimento, entao ele nao pode "decidir"
   inclui-la.
3. **As colunas do comparativo por equipamento sao as MESMAS sessoes do
   grupo.** Um equipamento ausente em T-2 nao desliza o T-3 dele para a coluna
   do T-2.
4. **O mapa de calor normaliza DENTRO da sessao** e entrega faixa NOMEADA -
   cor nunca e o unico sinal (§14).
5. **Ausencia nunca vira zero**: sem RPE registrado, `avg_rpe` e `None`; sem
   carga registrada, `tonnage_kg` e `None`.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.intelligence.dashboard.contracts import DataQuality, MuscleComparativesOut
from app.intelligence.dashboard.metrics_registry import (
    MuscleHeatmapBand,
    classify_heatmap_band,
)
from app.intelligence.dashboard.muscle_summary_service import (
    MAX_HEATMAP_SESSIONS,
    SESSION_COMPARATIVE_POINTS,
    assemble_comparatives,
)

PEITO = 1
TRICEPS = 2
NOMES = {PEITO: "Peito", TRICEPS: "Tríceps"}


def _row(
    *,
    group=PEITO,
    activity=100,
    local_date="2026-09-20",
    equipment="Supino reto",
    physical_sets=3,
    stimulus=3.0,
    tonnage=1500.0,
    tonnage_n=3,
    rpe_sum=24.0,
    rpe_n=3,
    has_direct=1,
    name="Treino A",
):
    return {
        "muscle_group_id": group,
        "activity_id": activity,
        "activity_name": name,
        "local_date": local_date,
        "trained_at": f"{local_date} 19:00:00",
        "equipment_name": equipment,
        "physical_sets": physical_sets,
        "stimulus_sets": stimulus,
        "tonnage_kg": tonnage,
        "tonnage_n": tonnage_n,
        "rpe_sum": rpe_sum,
        "rpe_n": rpe_n,
        "has_direct": has_direct,
    }


def _assemble(rows, **kwargs):
    return assemble_comparatives(rows, group_names=NOMES, **kwargs)


# ---------------------------------------------------------------------------
# 1) A unidade e SESSAO
# ---------------------------------------------------------------------------


def test_t0_e_a_ultima_sessao_do_grupo_nao_a_ultima_semana():
    """Duas sessoes no MESMO dia sao T-0 e T-1 - um recorte por semana as
    fundiria num ponto so e esconderia que houve duas."""
    bloco = _assemble(
        [
            _row(activity=10, local_date="2026-09-20"),
            _row(activity=11, local_date="2026-09-20"),
        ]
    )
    grupo = bloco["by_session"][0]
    assert [p["offset"] for p in grupo["points"]] == [0, 1]
    # A mais recente (maior activity_id no mesmo dia) e T-0.
    assert grupo["points"][0]["activity_id"] == 11
    assert grupo["sessions_in_window"] == 2
    assert bloco["unit"] == "session"


def test_pausa_longa_nao_cria_ponto_vazio():
    """Por SESSAO nao existe "semana sem treino": T-1 e simplesmente a sessao
    anterior, por mais antiga que seja."""
    bloco = _assemble(
        [
            _row(activity=10, local_date="2026-09-20"),
            _row(activity=1, local_date="2026-01-05"),
        ]
    )
    pontos = bloco["by_session"][0]["points"]
    assert len(pontos) == 2
    assert pontos[1]["local_date"] == date(2026, 1, 5)


def test_comparativo_para_em_quatro_sessoes():
    rows = [_row(activity=100 + i, local_date=f"2026-09-{10 + i:02d}") for i in range(7)]
    grupo = _assemble(rows)["by_session"][0]
    assert len(grupo["points"]) == SESSION_COMPARATIVE_POINTS
    assert grupo["sessions_in_window"] == 7
    # `sessions_in_window` maior que `points` e RECORTE, nao falta de dado.
    assert grupo["points"][0]["activity_id"] == 106


def test_unidade_por_sessao_e_declarada_em_limitacao():
    bloco = _assemble([_row()])
    assert any("SESSÃO" in limitation for limitation in bloco["limitations"])


def test_contrato_aceita_o_bloco_montado():
    """O montador devolve exatamente o que `MuscleComparativesOut` exige
    (`extra="forbid"`): um campo a mais ou a menos reprova aqui."""
    bloco = _assemble([_row(), _row(group=TRICEPS, stimulus=1.5, equipment=None)])
    validado = MuscleComparativesOut.model_validate(bloco)
    assert validado.unit == "session"
    assert {item.muscle_group for item in validado.by_session} == {"Peito", "Tríceps"}


# ---------------------------------------------------------------------------
# 2) Ausencia nunca vira zero
# ---------------------------------------------------------------------------


def test_sem_rpe_registrado_a_media_e_none_e_nao_zero():
    bloco = _assemble([_row(rpe_sum=0.0, rpe_n=0)])
    assert bloco["by_session"][0]["points"][0]["avg_rpe"] is None


def test_sem_carga_registrada_a_tonelagem_e_none_e_nao_zero():
    """Peso corporal nao registra quilos: somar zero e dizer "tonelagem 0 kg"
    seria inventar um fato."""
    bloco = _assemble([_row(tonnage=0.0, tonnage_n=0)])
    assert bloco["by_session"][0]["points"][0]["tonnage_kg"] is None


def test_m58_series_fora_da_tonelagem_sao_somadas_e_declaradas_em_todos_os_blocos():
    """Graviton assistido no mesmo grupo/sessao: o SQL ja tirou as series da
    tonelagem (`tonnage_n` so conta as que entram) e mandou a contagem em
    `tonnage_excluded_sets`; o montador soma e expoe nos tres recortes."""
    bloco = _assemble(
        [
            _row(equipment="Supino reto"),
            {**_row(equipment="Graviton", tonnage=0.0, tonnage_n=0), "tonnage_excluded_sets": 3},
        ]
    )
    ponto = bloco["by_session"][0]["points"][0]
    assert ponto["tonnage_kg"] == 1500.0
    assert ponto["tonnage_excluded_sets"] == 3
    por_equip = {e["equipment_name"]: e for e in bloco["by_equipment"][0]["equipments"]}
    assert por_equip["Graviton"]["points"][0]["tonnage_kg"] is None
    assert por_equip["Graviton"]["points"][0]["tonnage_excluded_sets"] == 3
    assert por_equip["Supino reto"]["points"][0]["tonnage_excluded_sets"] == 0
    assert bloco["heatmap"][0]["cells"][0]["tonnage_excluded_sets"] == 3
    MuscleComparativesOut.model_validate(bloco)


def test_m58_linha_antiga_sem_o_campo_novo_continua_valida():
    bloco = _assemble([_row()])
    assert bloco["by_session"][0]["points"][0]["tonnage_excluded_sets"] == 0


@pytest.mark.parametrize("tipo", [float, str])
def test_m58_tonelagem_arredonda_em_decimal_igual_nos_dois_backends(tipo):
    """73,845 chega como `Decimal` no PyMySQL e como float/texto no shim. O
    arredondamento e meia-para-cima em Decimal: 73.85 nos dois (round() de
    float daria 73.84 - o flake que ja existe em outro teste)."""
    from decimal import Decimal

    esperado = _assemble([_row(tonnage=Decimal("73.845"))])["by_session"][0]["points"][0]["tonnage_kg"]
    obtido = _assemble([_row(tonnage=tipo(73.845))])["by_session"][0]["points"][0]["tonnage_kg"]
    assert esperado == obtido == 73.85


def test_m58_sql_do_bloco_por_sessao_exclui_assistido_e_peso_corporal():
    from app.intelligence.dashboard.muscle_summary_service import SESSION_EQUIPMENT_SQL, session_equipment_sql

    sql = session_equipment_sql("eqex.load_convention")
    assert "LEFT JOIN equipment_exercises eqex" in sql
    assert "NOT IN ('assisted', 'bodyweight')" in sql
    assert "tonnage_excluded_sets" in sql
    assert "a.user_id = %s" in sql
    assert ";" not in sql  # um statement so (Data API)
    # Sem a coluna da 045, tudo conta como total.
    assert "NOT IN ('assisted'" not in SESSION_EQUIPMENT_SQL


def test_rpe_medio_e_ponderado_pelo_numero_de_series_com_rpe():
    """Duas series a 8 e uma a 5 dao 7, nao 6,5 (media das medias)."""
    bloco = _assemble(
        [
            _row(equipment="A", rpe_sum=16.0, rpe_n=2),
            _row(equipment="B", rpe_sum=5.0, rpe_n=1),
        ]
    )
    assert bloco["by_session"][0]["points"][0]["avg_rpe"] == 7.0


# ---------------------------------------------------------------------------
# 3) Comparativo por EQUIPAMENTO
# ---------------------------------------------------------------------------


def test_colunas_do_equipamento_sao_as_sessoes_do_grupo():
    """O equipamento so apareceu em T-0; ele NAO pode ocupar a coluna T-1 com
    a sessao seguinte, que e de outro equipamento."""
    bloco = _assemble(
        [
            _row(activity=20, local_date="2026-09-20", equipment="Halter"),
            _row(activity=19, local_date="2026-09-17", equipment="Barra"),
        ]
    )
    por_equipamento = {
        row["equipment_name"]: row for row in bloco["by_equipment"][0]["equipments"]
    }
    assert [p["offset"] for p in por_equipamento["Halter"]["points"]] == [0]
    assert [p["offset"] for p in por_equipamento["Barra"]["points"]] == [1]


def test_equipamentos_ordenados_pelo_que_mais_contribuiu():
    bloco = _assemble(
        [
            _row(equipment="Pouco", stimulus=1.0),
            _row(equipment="Muito", stimulus=6.0),
        ]
    )
    linhas = bloco["by_equipment"][0]["equipments"]
    assert [row["equipment_name"] for row in linhas] == ["Muito", "Pouco"]
    assert linhas[0]["share_of_group"] == pytest.approx(6 / 7, abs=1e-4)


def test_sessao_sem_equipamento_cadastrado_nao_ganha_nome_inventado():
    """`None` e "nao registrado". Um rotulo como "Sem equipamento" no payload
    seria indistinguivel de um aparelho chamado assim."""
    bloco = _assemble([_row(equipment=None)])
    assert bloco["by_equipment"][0]["equipments"][0]["equipment_name"] is None


def test_corte_de_equipamentos_e_declarado():
    rows = [_row(equipment=f"Aparelho {i}", stimulus=float(10 - i)) for i in range(9)]
    grupo = _assemble(rows, equipment_limit=3)["by_equipment"][0]
    assert len(grupo["equipments"]) == 3
    assert grupo["equipment_truncated"] is True


# ---------------------------------------------------------------------------
# 4) Mapa de calor
# ---------------------------------------------------------------------------


def test_faixa_do_mapa_de_calor_e_relativa_a_maior_celula_da_sessao():
    bloco = _assemble(
        [
            _row(group=PEITO, activity=30, stimulus=8.0),
            _row(group=TRICEPS, activity=30, stimulus=2.0),
        ]
    )
    celulas = {cell["muscle_group"]: cell for cell in bloco["heatmap"][0]["cells"]}
    assert celulas["Peito"]["share_of_max"] == 1.0
    assert celulas["Peito"]["band"] == MuscleHeatmapBand.VERY_HIGH.value
    assert celulas["Tríceps"]["share_of_max"] == 0.25
    assert celulas["Tríceps"]["band"] == MuscleHeatmapBand.LOW.value


def test_escopo_do_mapa_de_calor_e_declarado():
    bloco = _assemble([_row()])
    assert any("mesma sessão" in limitation for limitation in bloco["limitations"])


def test_toda_celula_tem_faixa_nomeada_e_valor():
    """Cor nunca e o unico sinal (§14): a celula ja chega com faixa e numero,
    entao a tela pode renderizar rotulo + valor sem depender de matiz."""
    bloco = _assemble([_row(), _row(group=TRICEPS, stimulus=1.0)])
    for cell in bloco["heatmap"][0]["cells"]:
        assert cell["band"] in {band.value for band in MuscleHeatmapBand}
        assert cell["effective_sets_stimulus"] is not None


@pytest.mark.parametrize(
    "share, esperado",
    [
        (None, MuscleHeatmapBand.NONE),
        (0.0, MuscleHeatmapBand.NONE),
        (0.25, MuscleHeatmapBand.LOW),
        (0.2501, MuscleHeatmapBand.MODERATE),
        (0.5, MuscleHeatmapBand.MODERATE),
        (0.75, MuscleHeatmapBand.HIGH),
        (1.0, MuscleHeatmapBand.VERY_HIGH),
    ],
)
def test_fronteiras_da_faixa(share, esperado):
    assert classify_heatmap_band(share) is esperado


def test_ausencia_de_estimulo_nao_e_faixa_baixa():
    """`NONE` e um estado proprio: "nao foi tocado" nao e "foi pouco"."""
    assert classify_heatmap_band(0.0) is MuscleHeatmapBand.NONE
    assert classify_heatmap_band(None) is MuscleHeatmapBand.NONE


def test_mapa_de_calor_corta_pelas_sessoes_mais_recentes_e_declara():
    rows = [_row(activity=200 + i, local_date=f"2026-09-{i + 1:02d}") for i in range(12)]
    bloco = _assemble(rows)
    assert len(bloco["heatmap"]) == MAX_HEATMAP_SESSIONS
    assert bloco["heatmap"][0]["activity_id"] == 211
    assert any("mais antiga" in limitation for limitation in bloco["limitations"])


# ---------------------------------------------------------------------------
# 5) Qualidade do bloco
# ---------------------------------------------------------------------------


def test_sem_sessao_nenhuma_o_bloco_e_insuficiente_e_nao_vazio_silencioso():
    bloco = _assemble([])
    assert bloco["data_quality"] is DataQuality.INSUFFICIENT
    assert bloco["sessions_compared"] == 0
    assert bloco["by_session"] == []


def test_uma_sessao_so_nao_e_comparativo():
    """Sem T-1 nao ha com o que comparar - a leitura existe, mas e fraca."""
    assert _assemble([_row()])["data_quality"] is DataQuality.LOW


def test_grupo_com_menos_de_quatro_sessoes_derruba_a_qualidade_para_media():
    rows = [_row(activity=300 + i, local_date=f"2026-09-{10 + i:02d}") for i in range(4)]
    rows.append(_row(group=TRICEPS, activity=300, local_date="2026-09-10", stimulus=1.0))
    bloco = _assemble(rows)
    assert bloco["data_quality"] is DataQuality.MEDIUM
    assert any("Tríceps" in limitation for limitation in bloco["limitations"])
