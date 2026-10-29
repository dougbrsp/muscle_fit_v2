'use client';

import { ActionIcon, Group, Text, TextInput, Tooltip } from '@mantine/core';
import { IconPencil, IconX } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRef, useState, type KeyboardEvent } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import type { IntervalsWellnessManualPatch, IntervalsWellnessSource } from '@/lib/intervals-types';
import { formatNumberOrDash } from '@/lib/physical-indicators-types';
import { BRAND } from '@/theme';

type ManualField = keyof IntervalsWellnessManualPatch;

type Props = {
  date: string;
  field: ManualField;
  label: string;
  value: number | null;
  source: IntervalsWellnessSource;
};

const MANUAL_TOOLTIP = 'Editado manualmente — será substituído se o Intervals enviar dado deste dia.';

// Célula editável de FC de repouso / HRV na tabela de wellness.
//
// A tela não conhece regra numérica nenhuma: a faixa aceita e a precedência
// manual × Intervals.icu moram no backend (`intervals_store`); um valor fora
// da faixa volta 422 e a mensagem do backend é mostrada aqui.
export function WellnessManualCell({ date, field, label, value, source }: Props) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  // Enter salva e o blur que vem depois não pode salvar de novo; Esc cancela
  // e o blur da desmontagem não pode salvar.
  const doneRef = useRef(false);

  const mutation = useMutation({
    mutationFn: (patch: IntervalsWellnessManualPatch) =>
      apiFetch(`/integrations/intervals/wellness/${date}`, { method: 'PATCH', body: JSON.stringify(patch) }),
    onSuccess: () => {
      setEditing(false);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ['intervals-icu-dashboard'] });
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : 'Não foi possível salvar.');
      doneRef.current = false;
    },
  });

  const startEditing = () => {
    doneRef.current = false;
    setDraft(value === null ? '' : String(value));
    setError(null);
    setEditing(true);
  };

  const cancel = () => {
    doneRef.current = true;
    setEditing(false);
    setError(null);
  };

  const save = () => {
    if (doneRef.current || mutation.isPending) return;
    const text = draft.trim();
    // Campo vazio não apaga nada: limpar a edição é a ação própria (X).
    if (text === '' || Number(text) === value) {
      cancel();
      return;
    }
    const parsed = Number(text);
    if (!Number.isFinite(parsed)) {
      setError('Informe um número.');
      return;
    }
    doneRef.current = true;
    mutation.mutate({ [field]: parsed });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      save();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      cancel();
    }
  };

  if (editing) {
    return (
      <TextInput
        size="xs"
        type="number"
        inputMode="decimal"
        autoFocus
        aria-label={`${label} em ${date}`}
        value={draft}
        onChange={(event) => setDraft(event.currentTarget.value)}
        onKeyDown={onKeyDown}
        onBlur={save}
        disabled={mutation.isPending}
        error={error}
        w={110}
      />
    );
  }

  const manual = source === 'manual';
  return (
    <Group gap={4} wrap="nowrap">
      <Text size="sm" span>
        {formatNumberOrDash(value, 0)}
      </Text>
      {manual && (
        <Tooltip label={MANUAL_TOOLTIP} withArrow multiline w={240}>
          <Text size="xs" c={BRAND.textMuted} span aria-label="Editado manualmente">
            ✎
          </Text>
        </Tooltip>
      )}
      <Tooltip label={`Editar ${label}`} withArrow>
        <ActionIcon variant="subtle" color="gray" size="sm" aria-label={`Editar ${label} em ${date}`} onClick={startEditing}>
          <IconPencil size={14} />
        </ActionIcon>
      </Tooltip>
      {manual && (
        <Tooltip label="Limpar edição manual (o próximo sync preenche)" withArrow>
          <ActionIcon
            variant="subtle"
            color="gray"
            size="sm"
            aria-label={`Limpar edição manual de ${label} em ${date}`}
            loading={mutation.isPending}
            onClick={() => mutation.mutate({ [field]: null })}
          >
            <IconX size={14} />
          </ActionIcon>
        </Tooltip>
      )}
      {error && !editing && (
        <Text size="xs" c="red" span>
          {error}
        </Text>
      )}
    </Group>
  );
}
