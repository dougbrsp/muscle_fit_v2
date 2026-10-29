'use client';

import { Alert, Badge, Button, Group, Stack, Text, Textarea, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconSparkles } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import type {
  ExerciseCatalogItem,
  ExecutionDescriptionGenerateResponse,
  ExerciseUpsertResponse,
} from '@/lib/exercise-catalog-admin-types';
import { BRAND } from '@/theme';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/**
 * Descrição de EXECUÇÃO do exercício (migração 039): o texto que a tela de
 * briefing do modo treino mostra antes da primeira série.
 *
 * É gerado UMA vez pela OpenAI (`POST /exercises/{id}/execution-description/generate`)
 * e revisado aqui. Salvar por aqui é uma REVISÃO HUMANA: o backend carimba
 * `execution_description_reviewed_by/_at` e apaga a etiqueta do modelo — e a
 * geração em lote nunca sobrescreve um texto revisado sem `force`. Por isso o
 * botão de gerar pergunta antes de substituir um texto que já existe.
 *
 * Seção separada do formulário principal do drawer de propósito: ela tem o
 * próprio ciclo (gerar → revisar → salvar) e não precisa arrastar o resto dos
 * campos do exercício junto a cada salvamento.
 */
export function ExecutionDescriptionSection({
  exerciseId,
  exercise,
  isAdmin,
  detailQueryKey,
}: {
  exerciseId: number;
  exercise: ExerciseCatalogItem;
  isAdmin: boolean;
  detailQueryKey: readonly unknown[];
}) {
  const qc = useQueryClient();
  const [text, setText] = useState(exercise.execution_description || '');
  const [dirty, setDirty] = useState(false);

  // Enquanto o admin não editou nada, o textarea segue o servidor (inclusive
  // o texto que a geração acabou de trazer). Depois da primeira edição, o
  // rascunho local manda — recarregar por baixo apagaria o que ele escreveu.
  useEffect(() => {
    if (!dirty) setText(exercise.execution_description || '');
  }, [exercise.execution_description, dirty]);

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch<ExerciseUpsertResponse>(`/exercises/${exerciseId}`, {
        method: 'PUT',
        body: JSON.stringify({ execution_description: text.trim() || null }),
      }),
    onSuccess: async () => {
      setDirty(false);
      await qc.invalidateQueries({ queryKey: detailQueryKey });
    },
  });

  const generateMutation = useMutation({
    mutationFn: (force: boolean) =>
      apiFetch<ExecutionDescriptionGenerateResponse>(`/exercises/${exerciseId}/execution-description/generate`, {
        method: 'POST',
        body: JSON.stringify({ force }),
      }),
    onSuccess: async () => {
      setDirty(false);
      await qc.invalidateQueries({ queryKey: detailQueryKey });
    },
  });

  const hasText = Boolean((exercise.execution_description || '').trim());
  const reviewed = exercise.execution_description_reviewed_at != null;

  return (
    <Stack gap="sm">
      <Group gap="xs">
        {!hasText && (
          <Badge color="gray" variant="light">
            ainda não gerada
          </Badge>
        )}
        {reviewed && (
          <Badge color="teal" variant="light">
            revisada por humano
          </Badge>
        )}
        {!reviewed && exercise.execution_description_model && (
          <Badge color="blue" variant="light">
            {exercise.execution_description_model}
            {exercise.execution_description_prompt_version ? ` · ${exercise.execution_description_prompt_version}` : ''}
          </Badge>
        )}
      </Group>

      <Textarea
        label="Descrição de execução (modo treino)"
        description="Uma frase por linha. Aparece no briefing do exercício, antes da primeira série."
        autosize
        minRows={6}
        value={text}
        onChange={(event) => {
          setDirty(true);
          setText(event.currentTarget.value);
        }}
        disabled={!isAdmin}
      />

      {(saveMutation.isError || generateMutation.isError) && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(
            saveMutation.error || generateMutation.error,
            'Não foi possível salvar/gerar a descrição de execução.',
          )}
        </Alert>
      )}

      <Group justify="space-between">
        <Tooltip label={isAdmin ? 'Gera pela OpenAI e grava no catálogo' : 'Ação restrita a administradores'}>
          <Button
            variant="light"
            leftSection={<IconSparkles size={16} />}
            loading={generateMutation.isPending}
            disabled={!isAdmin}
            onClick={() => {
              if (hasText && !window.confirm('Substituir a descrição atual por uma nova geração?')) return;
              generateMutation.mutate(hasText);
            }}
          >
            {hasText ? 'Regerar com IA' : 'Gerar com IA'}
          </Button>
        </Tooltip>
        <Button
          color="brand"
          loading={saveMutation.isPending}
          disabled={!isAdmin || !dirty}
          onClick={() => saveMutation.mutate()}
        >
          Salvar descrição
        </Button>
      </Group>

      <Text size="xs" c={BRAND.textMuted}>
        Salvar aqui marca o texto como revisado por você — a geração em lote não sobrescreve textos revisados.
      </Text>
    </Stack>
  );
}
