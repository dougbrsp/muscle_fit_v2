'use client';

import { Button, Card, Group, Loader, Stack, Text } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { IconArrowBackUp, IconCheck, IconSparkles } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { NutritionLimitations } from './NutritionLimitations';
import { TodayTargetCard } from './PlanPreview';
import { ApiError } from '@/lib/api';
import { nutritionErrorInfo } from '@/lib/nutrition-api';
import {
  fetchPlanSuggestionUsage,
  planSuggestionKeys,
  previewPlanDraft,
  requestPlanSuggestion,
  type PlanSuggestionCurrent,
  type PlanSuggestionFields,
  type PlanSuggestionResult,
} from '@/lib/nutrition-plan-suggestion';
import type { NutritionPlanSettingsPayload } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

const ATRASO_DA_PREVIA_MS = 500;

type Props = {
  userId: number | null;
  /** Estado do formulário para o pedido (sem gramas). */
  current: PlanSuggestionCurrent;
  /** Corpo completo do formulário, para a prévia do motor a cada ajuste. */
  draft: (NutritionPlanSettingsPayload & { user_id?: number }) | null;
  onApply: (fields: PlanSuggestionFields) => void;
  onUndo: () => void;
  onKeep: () => void;
  /** O formulário fica travado enquanto o job roda: "o que mudou" é contra o estado enviado. */
  onPendingChange: (pending: boolean) => void;
};

const MENSAGEM_DE_FALHA = 'Não foi possível gerar a sugestão.';

/**
 * "Sugerir com IA" (N6, D3): a IA sugere só parâmetros dentro das faixas do motor. A sugestão preenche o formulário como
 * rascunho; aqui ficam o que mudou, os motivos (códigos do motor com rótulo) e o resultado do motor para o formulário
 * como está, recalculado a cada ajuste. Nada é gravado até "Salvar rascunho".
 */
export function PlanSuggestionPanel({ userId, current, draft, onApply, onUndo, onKeep, onPendingChange }: Props) {
  const queryClient = useQueryClient();
  const [resultado, setResultado] = useState<PlanSuggestionResult | null>(null);

  const uso = useQuery({ queryKey: planSuggestionKeys.usage, queryFn: fetchPlanSuggestionUsage });
  const sugestao = useMutation({
    mutationFn: () => requestPlanSuggestion(current, userId),
    onMutate: () => onPendingChange(true),
    onSuccess: (dados) => {
      setResultado(dados);
      onApply(dados.fields);
    },
    onSettled: () => {
      onPendingChange(false);
      return queryClient.invalidateQueries({ queryKey: planSuggestionKeys.usage });
    },
  });

  const [corpoDaPrevia] = useDebouncedValue(resultado && draft ? JSON.stringify(draft) : null, ATRASO_DA_PREVIA_MS);
  const previa = useQuery({
    queryKey: planSuggestionKeys.preview(corpoDaPrevia),
    queryFn: () => previewPlanDraft(JSON.parse(corpoDaPrevia as string)),
    enabled: corpoDaPrevia !== null,
    retry: false,
  });

  const esgotada = uso.data != null && !uso.data.allowed;
  // Erro da API (403/422/429/503): texto e itens do motor. Falha ou prazo do job (`GET /jobs`): a mensagem já vem pronta.
  const erro = sugestao.isError
    ? sugestao.error instanceof ApiError
      ? nutritionErrorInfo(sugestao.error, MENSAGEM_DE_FALHA)
      : { message: sugestao.error.message || MENSAGEM_DE_FALHA, items: [] }
    : null;
  const erroPrevia = previa.isError ? nutritionErrorInfo(previa.error, 'Não foi possível calcular o formulário.') : null;

  return (
    <Stack gap="xs">
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Button
          variant="light"
          color="brand"
          leftSection={<IconSparkles size={16} />}
          onClick={() => sugestao.mutate()}
          loading={sugestao.isPending}
          disabled={esgotada}
          w={{ base: '100%', xs: 'auto' }}
        >
          Sugerir com IA
        </Button>
        {uso.data && (
          <Text size="xs" c={BRAND.textMuted}>
            {uso.data.remaining} de {uso.data.limit} hoje
          </Text>
        )}
      </Group>
      {sugestao.isPending && (
        <Text size="sm" c={BRAND.textMuted} aria-live="polite">
          A IA está analisando seus dados. Isso pode levar alguns segundos.
        </Text>
      )}
      {esgotada && !erro && (
        <Text size="sm" c={BRAND.textMuted}>
          As sugestões de hoje acabaram. Tente de novo amanhã ou ajuste os campos à mão.
        </Text>
      )}
      {erro && erro.items.length > 0 && <NutritionLimitations items={erro.items} title={erro.message} tone="error" />}
      {erro && erro.items.length === 0 && (
        <Text c="red" size="sm" role="alert">
          {erro.message}
        </Text>
      )}

      {resultado && (
        <Card withBorder radius="md" bg={BRAND.bgSurface}>
          <Stack gap="sm">
            <Text fw={600} c={BRAND.textPrimary}>
              Sugestão no formulário, ainda não salva
            </Text>
            {resultado.changes.length === 0 ? (
              <Text size="sm" c={BRAND.textMuted}>
                A IA manteve os valores que já estavam no formulário.
              </Text>
            ) : (
              <Stack gap={4}>
                {resultado.changes.map((mudanca) => (
                  <Group key={mudanca.field} justify="space-between" wrap="wrap" gap={4}>
                    <Text size="sm" c="dimmed">
                      {mudanca.label}
                    </Text>
                    <Text size="sm" c={BRAND.textPrimary}>
                      {mudanca.before_label} → {mudanca.after_label}
                    </Text>
                  </Group>
                ))}
              </Stack>
            )}
            <NutritionLimitations items={resultado.reasons} title="Motivos apontados pela IA" tone="info" />
            {previa.isFetching && !previa.data && <Loader size="sm" />}
            {previa.data && <TodayTargetCard target={previa.data.target} />}
            {previa.data && (
              <NutritionLimitations items={previa.data.blocking} title="O motor não deixa publicar assim" tone="error" />
            )}
            {previa.data && <NutritionLimitations items={previa.data.warnings} title="Avisos do motor" />}
            {erroPrevia && <NutritionLimitations items={erroPrevia.items} title={erroPrevia.message} tone="error" />}
            {erroPrevia && erroPrevia.items.length === 0 && (
              <Text c="red" size="sm" role="alert">
                {erroPrevia.message}
              </Text>
            )}
            <Group justify="flex-end" gap="xs" wrap="wrap">
              <Button
                variant="default"
                leftSection={<IconArrowBackUp size={16} />}
                onClick={() => {
                  setResultado(null);
                  onUndo();
                }}
              >
                Desfazer
              </Button>
              <Button
                variant="light"
                color="brand"
                leftSection={<IconCheck size={16} />}
                onClick={() => {
                  setResultado(null);
                  onKeep();
                }}
              >
                Manter
              </Button>
            </Group>
            <Text size="xs" c={BRAND.textMuted}>
              Ajuste os campos à vontade: o resultado acima é recalculado pelo motor. Nada muda até você salvar o rascunho.
            </Text>
          </Stack>
        </Card>
      )}
    </Stack>
  );
}
