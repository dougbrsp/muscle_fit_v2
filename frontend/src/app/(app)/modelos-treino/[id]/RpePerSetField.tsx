'use client';

import { Button, Group, Input, NumberInput, Text } from '@mantine/core';
import { useEffect, useState } from 'react';
import { MIN_SETS_COUNT } from '@/lib/prescription-defaults';
import {
  commitRpeDraft,
  fitRpeTargets,
  RPE_TARGET_SCALE,
  type RpeDraftValue,
} from '@/lib/workout-plan-template-form';

// RPE esperado por série (#146). Um campo por série de trabalho; a digitação
// fica no rascunho local e a lista só sobe no blur (mesmo padrão do
// `BlurCommit*`). Limites e passo vêm do espelho gerado; a lista sem buracos
// vem de `commitRpeDraft` (só copia valores). Tudo em branco = sem alvo.
type RpePerSetFieldProps = {
  targets: number[];
  setsCount: number | null;
  onCommit: (targets: number[]) => void;
  // Alvo gravado só em texto (sem lista, não editado): mostrado literal até o
  // usuário pedir para definir por série (`storedRpeTextOnly`).
  storedText?: string | null;
};

export function RpePerSetField({ targets, setsCount, onCommit, storedText = null }: RpePerSetFieldProps) {
  const [opened, setOpened] = useState(false);
  const shown = fitRpeTargets(targets, setsCount);
  // Sem lista, mostra os campos em branco (quantidade = séries do item).
  const count = Math.max(MIN_SETS_COUNT, setsCount ?? shown.length);
  const shownKey = `${count}:${shown.join(',')}`;
  const [draft, setDraft] = useState<RpeDraftValue[]>(() => Array.from({ length: count }, (_, i) => shown[i] ?? ''));
  useEffect(() => {
    setDraft(Array.from({ length: count }, (_, i) => shown[i] ?? ''));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- shownKey resume count + shown
  }, [shownKey]);

  const commit = (next: RpeDraftValue[]) => {
    const list = commitRpeDraft(next);
    setDraft(Array.from({ length: count }, (_, i) => list[i] ?? ''));
    if (list.join(',') !== shown.join(',')) onCommit(list);
  };

  if (storedText && !opened) {
    return (
      <Input.Wrapper size="xs" label="RPE esperado por série">
        <Group gap={6} wrap="wrap" align="center">
          <Text size="xs" role="status">
            Alvo gravado: {storedText}
          </Text>
          <Button size="compact-xs" variant="subtle" onClick={() => setOpened(true)}>
            Definir por série
          </Button>
        </Group>
      </Input.Wrapper>
    );
  }

  return (
    <Input.Wrapper
      size="xs"
      label="RPE esperado por série"
      description={storedText ? `Ao preencher, o alvo gravado (${storedText}) será substituído.` : undefined}
    >
      <Group gap={4} wrap="wrap" align="flex-end">
        {draft.map((value, index) => (
          <NumberInput
            key={index}
            size="xs"
            w={62}
            aria-label={`RPE esperado da série ${index + 1}`}
            placeholder={`S${index + 1}`}
            value={value ?? ''}
            min={RPE_TARGET_SCALE.min}
            max={RPE_TARGET_SCALE.max}
            step={RPE_TARGET_SCALE.step}
            decimalScale={1}
            hideControls
            onChange={(v) => setDraft((d) => d.map((x, i) => (i === index ? v : x)))}
            onBlur={() => commit(draft)}
          />
        ))}
        <Button
          size="compact-xs"
          variant="subtle"
          disabled={filledFirst(draft) === null}
          onClick={() => commit(draft.map(() => filledFirst(draft)))}
        >
          Repetir a 1ª em todas
        </Button>
      </Group>
    </Input.Wrapper>
  );
}

function filledFirst(draft: readonly RpeDraftValue[]): number | string | null {
  const v = draft[0];
  return v === '' || v == null ? null : v;
}
