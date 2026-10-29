from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..rag_chunking import MAX_RAG_BASE_CHARS, select_rag_text, truncate_rag
from .body_composition_trend_service import BodyCompositionTrendService
from .cardio_context_service import CardioAgentContextService
from .dashboard_aggregator_service import DashboardAggregatorService

logger = logging.getLogger(__name__)

# CardioAgentContextService.build_context(days=...) tem varias janelas
# hard-coded internamente, independentes do `days` recebido:
# - body_composition: sempre fixo em 30 dias (BodyCompositionTrendService.get_body_trend(days=30));
# - muscle_recovery: sempre fixo em 14 dias (MuscleRecoveryService.get_recovery_overview(days=14, ...));
# - weekly_training_load: nao usa `days` (TrainingLoadService.get_weekly_summary() sem parametro);
# - past_recommendations: limit=10 fixo, sem filtro de janela.
# Apenas recent_workouts/intervals_wellness/effort_metrics_estimated/sleep_metrics
# de fato respeitam `days`. Passar days=90 (janela do relatorio) para esta
# classe infla desnecessariamente recent_workouts/intervals_wellness/etc. sem
# nenhum ganho (as janelas mais relevantes ali ja sao fixas e curtas), entao
# aqui a chamada e capada em CARDIO_CONTEXT_MAX_DAYS - nao alteramos a classe
# original, so o uso feito por este service.
CARDIO_CONTEXT_MAX_DAYS = 30


class ReportDataService:
    """Agrega, num unico payload, todos os dados usados pela geracao do
    Relatorio (tela /relatorios): evolucao de treinos, recomposicao
    corporal e contexto de cardio, reaproveitando os services de
    `intelligence/` ja existentes sem duplicar calculo/SQL - so agrega. Nao
    chama IA nem gera grafico/PDF (responsabilidades de `report_ai.py` e
    `report_pdf_builder.py`, respectivamente)."""

    def __init__(self) -> None:
        self.dashboard_aggregator_service = DashboardAggregatorService()
        self.body_composition_service = BodyCompositionTrendService()
        self.cardio_context_service = CardioAgentContextService()

    def build_report_payload(self, user_id: int, days: int = 90) -> dict[str, Any]:
        cardio_days = min(days, CARDIO_CONTEXT_MAX_DAYS)

        return {
            "days": days,
            "generated_at": datetime.now().isoformat(),
            "training": self.dashboard_aggregator_service.build_training_evolution_payload(days=days, user_id=user_id),
            "body_composition": self.body_composition_service.get_body_trend(days=days, user_id=user_id),
            "cardio": self.cardio_context_service.build_context(days=cardio_days, user_id=user_id),
        }

    def build_rag_context(self, training_payload: dict[str, Any]) -> str:
        """Monta o texto de RAG local de metodo de treino (workspace/rag)
        relevante para os grupos musculares presentes no relatorio, reaproveitando
        `select_rag_text` (mesmo mecanismo ja usado na geracao de ficha de
        treino). Deliberadamente NAO entra no payload de `build_report_payload`
        - esse payload e persistido inteiro em `reports.context_snapshot`, e o
        RAG e reproduzivel a qualquer momento a partir dos arquivos, sem
        necessidade de guardar uma copia no banco. Tolerante a falha: se o RAG
        estiver ausente/malformado, loga um aviso e retorna string vazia, sem
        derrubar a geracao do relatorio."""
        volume_overview = training_payload.get("volume_overview") or []
        muscle_groups: list[str] = []
        seen: set[str] = set()
        for item in volume_overview:
            group = item.get("muscle_group")
            if not group or group in seen:
                continue
            seen.add(group)
            muscle_groups.append(group)

        # IA-02: `has_training_type=True` - esta tela nao tem tipo de treino, e o
        # fallback recursivo de TIPOS_TREINO (default False) existe so para a
        # montagem de ficha legada; aqui ele puxava TODAS as subpastas. E o mesmo
        # teto da base da Periodizacao e dos Insights (dono: `rag_chunking`).
        try:
            return truncate_rag(
                select_rag_text(muscle_groups, has_training_type=True), MAX_RAG_BASE_CHARS, "RAG base"
            )
        except Exception as exc:  # noqa: BLE001 - RAG e apoio, nunca deve derrubar o relatorio
            logger.warning("report_data_service: falha ao montar contexto RAG: %s", exc)
            return ""
