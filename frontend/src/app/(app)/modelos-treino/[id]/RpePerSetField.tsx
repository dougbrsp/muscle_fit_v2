'use client';

import { Button, Group, Input, NumberInput, Text } from '@mantine/core';
import { useEffect, useState } from 'react';
import { MIN_SETS_COUNT } from '@/lib/prescription-defaults';
import {
  commitRpeDraft,
  fitRpeTargets,
  invalidRpePositions,
  RPE_TARGET_SCALE,
  sanitizeRpeDraft,
  stepDecimalScale,
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
  // Dica do objetivo (`itemFieldPlaceholders(...).rpe`), exibida como descrição.
  hint?: string | null;
};

const WARNING = 'Valor fora da escala de RPE; mantido o anterior.';

export function RpePerSetField({ targets, setsCount, onCommit, storedText = null, hint = null }: RpePerSetFieldProps) {
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

  const [warned, setWarned] = useState<number[]>([]);

  const commit = (next: RpeDraftValue[]) => {
    setWarned(invalidRpePositions(next));
    const list = commitRpeDraft(sanitizeRpeDraft(next, shown));
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
      description={
        storedText
          ? `Ao preencher, o alvo gravado (${storedText}) será substituído.`
          : shown.length === 0
            ? `Sem RPE definido${hint ? ` (${hint})` : ''}`
            : (hint ?? undefined)
      }
    >
      {/* Grade regular: colunas de mesma largura que quebram em telas estreitas. */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(64px, 1fr))', gap: 6, alignItems: 'end' }}>
        {draft.map((value, index) => (
          <NumberInput
            key={index}
            size="xs"
            label={`S${index + 1}`}
            aria-label={`RPE esperado da série ${index + 1}`}
            placeholder="—"
            value={value ?? ''}
            min={RPE_TARGET_SCALE.min}
            max={RPE_TARGET_SCALE.max}
            step={RPE_TARGET_SCALE.step}
            decimalScale={stepDecimalScale(RPE_TARGET_SCALE.step)}
            hideControls
            // Grava sempre o digitado ("15" passa por "1"); a validação é só ao sair (commit),
            // que restaura o último confirmado e avisa. clampBehavior none: o Mantine não troca 15 por 10.
            clampBehavior="none"
            error={warned.includes(index) ? WARNING : undefined}
            onChange={(v) => {
              setWarned((w) => w.filter((i) => i !== index));
              setDraft((d) => d.map((x, i) => (i === index ? v : x)));
            }}
            onBlur={() => commit(draft)}
          />
        ))}
      </div>
      <Button
        mt={4}
        size="compact-xs"
        variant="subtle"
        disabled={filledFirst(draft) === null}
        onClick={() => commit(draft.map(() => filledFirst(draft)))}
      >
        Repetir a 1ª em todas
      </Button>
    </Input.Wrapper>
  );
}

function filledFirst(draft: readonly RpeDraftValue[]): number | string | null {
  const v = draft[0];
  return v === '' || v == null ? null : v;
}
