'use client';

import { Button, Group, SegmentedControl, Stack, Switch, Text } from '@mantine/core';
import { IconCalendarEvent } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { NutritionLimitations } from './NutritionLimitations';
import { fetchCycleDaysFromTemplate, nutritionErrorInfo } from '@/lib/nutrition-api';
import { CARB_CYCLE_POLICY_OPTIONS, DAY_KIND_OPTIONS, DEFAULT_DAY_KIND, WEEKDAY_OPTIONS, type NutritionCode } from '@/lib/nutrition-constants';
import { cyclePolicyHint } from '@/lib/nutrition-cycle-policy';
import { formatGrams, shareToPercent } from '@/lib/nutrition-format';
import { applyTemplateProposal } from '@/lib/nutrition-plan-form';
import type { NutritionDayTarget } from '@/lib/nutrition-types';
import { formatNumberOrDash } from '@/lib/format';
import motorConstants from '@/lib/motor-constants.generated.json';
import { BRAND } from '@/theme';

/** Dia da semana ("0" = segunda) -> tipo do dia (código de `DAY_KIND_OPTIONS`). */
export type CycleDaysValue = Record<string, NutritionCode>;

type Props = {
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  value: CycleDaysValue;
  onChange: (value: CycleDaysValue) => void;
  policy: NutritionCode;
  onPolicyChange: (policy: NutritionCode) => void;
  /** Dono do plano (admin/PT); sem valor, o próprio usuário. */
  userId?: number | null;
  /** Semana da prévia do rascunho salvo (gordura em g e % por dia, da API); sem ela, não mostra. */
  week?: NutritionDayTarget[];
  /** "Definir as gramas manualmente" ligado: com a política de compensação, ela não se aplica. */
  manualOn?: boolean;
  /** Rótulo do aviso vindo da API (prévia), quando houver. */
  ignoredManualLabel?: string | null;
};

/** Avisos do motor que a tela mostra por dia (códigos do espelho gerado): carbo limitado e ciclo não aplicado. */
const DAY_WARNING_CODES: ReadonlyArray<string> = [
  motorConstants.nutrition.fat_compensation_carb_limited,
  motorConstants.nutrition.fat_compensation_cycle_skipped,
];

/**
 * Lista global sem os avisos que de fato aparecem no resumo de algum dia exibido (evita repetir);
 * aviso de dia que não é exibido (ex.: fora de `publish_days`) continua na lista global.
 */
export function withoutDayWarnings<T extends { code: string }>(items: T[], shownDays: NutritionDayTarget[]): T[] {
  const mostrados = new Set(shownDays.flatMap((dia) => dia.limitations.map((item) => item.code)));
  return items.filter((item) => !(DAY_WARNING_CODES.includes(item.code) && mostrados.has(item.code)));
}

/** Gordura do dia em g e % das kcal (ambos da API) e o aviso de carbo limitado, com o rótulo pronto. */
export function DayFatSummary({ target }: { target: NutritionDayTarget }) {
  const pct = shareToPercent(target.macro_kcal_shares?.fat);
  const avisos = target.limitations.filter((item) => DAY_WARNING_CODES.includes(item.code));
  return (
    <>
      <Text size="xs" c={BRAND.textMuted} data-testid="dia-gordura">
        Gordura {formatGrams(target.fat_g)}
        {pct == null ? '' : ` (${formatNumberOrDash(pct, 0)}% das kcal)`}
      </Text>
      {avisos.map((aviso) => (
        <Text key={aviso.code} size="xs" c="yellow" data-testid="dia-aviso-ciclo">
          {aviso.label}
        </Text>
      ))}
    </>
  );
}

/** Dia da semana da data ISO, segunda = "0" (a chave de `cycle_days`). */
function weekdayKey(isoDate: string): string {
  return String((new Date(`${isoDate.slice(0, 10)}T12:00:00Z`).getUTCDay() + 6) % 7);
}

/** Valor inicial: todos os dias com o tipo `normal` (o código vem do motor, `DAY_KIND_OPTIONS`). */
export function flatCycleDays(normalCode: NutritionCode): CycleDaysValue {
  return Object.fromEntries(WEEKDAY_OPTIONS.map((dia) => [dia.value, normalCode]));
}

/**
 * Dias de mais e menos gasto (D5): o usuário marca cada dia da semana como alto, normal ou
 * baixo, ou puxa a proposta do Modelo de treino e ajusta. A média semanal e os multiplicadores
 * são do motor; aqui só se escolhe o tipo de cada dia.
 */
export function PlanCycleDays({ enabled, onEnabledChange, value, onChange, policy, onPolicyChange, userId, week, manualOn, ignoredManualLabel }: Props) {
  const normal = DEFAULT_DAY_KIND;
  const templateMutation = useMutation({
    mutationFn: () => fetchCycleDaysFromTemplate(userId ?? null),
    onSuccess: (proposta) => {
      // Sem proposta (sem Modelo, Modelo parcial ou que treina todo dia) o que foi marcado fica como está.
      onChange(applyTemplateProposal(value, proposta.cycle_days));
    },
  });
  const proposta = templateMutation.data;
  const erro = templateMutation.isError
    ? nutritionErrorInfo(templateMutation.error, 'Não foi possível ler o Modelo de treino.')
    : null;

  return (
    <Stack gap="xs">
      <Switch
        label="Dias de mais e menos gasto"
        description="Marque os dias da semana com mais ou menos carboidrato. Em semana completa do mesmo plano, a média da semana não muda; publicando no meio da semana, os dias que já passaram não entram."
        checked={enabled}
        onChange={(event) => onEnabledChange(event.currentTarget.checked)}
      />
      {enabled && (
        <Stack gap="xs">
          <Group>
            <Button
              variant="light"
              size="xs"
              leftSection={<IconCalendarEvent size={14} />}
              loading={templateMutation.isPending}
              onClick={() => templateMutation.mutate()}
            >
              Puxar do Modelo de treino
            </Button>
            <Text size="xs" c={BRAND.textMuted}>
              Dias de treino viram dia alto e os demais, baixo. Você ajusta antes de salvar.
            </Text>
          </Group>
          {proposta && <NutritionLimitations items={proposta.notes} title="Sobre o Modelo" tone="info" />}
          {erro && <NutritionLimitations items={erro.items} title={erro.message} tone="error" />}
          {erro && erro.items.length === 0 && (
            <Text c="red" size="sm">
              {erro.message}
            </Text>
          )}
          <div>
            <Text size="sm" fw={500} id="carb-cycle-policy-label">
              Como compensar nos dias de ciclo
            </Text>
            <SegmentedControl
              fullWidth
              data={CARB_CYCLE_POLICY_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              value={policy}
              onChange={onPolicyChange}
              aria-labelledby="carb-cycle-policy-label"
            />
            <Text size="xs" c={BRAND.textMuted} mt={4}>
              {cyclePolicyHint(policy)}
            </Text>
            {manualOn && policy === motorConstants.nutrition.fat_compensation_policy && (
              <Text size="xs" c="yellow" mt={4} role="status" data-testid="aviso-manual-compensacao">
                {ignoredManualLabel ?? motorConstants.nutrition.fat_compensation_ignored_manual_label}
              </Text>
            )}
          </div>
          {WEEKDAY_OPTIONS.map((dia) => (
            <div key={dia.value}>
              <Text size="xs" c="dimmed">
                {dia.label}
              </Text>
              <SegmentedControl
                fullWidth
                size="xs"
                data={DAY_KIND_OPTIONS.map((tipo) => ({ value: tipo.value, label: tipo.label }))}
                value={value[dia.value] ?? normal}
                onChange={(tipo) => onChange({ ...value, [dia.value]: tipo })}
                aria-label={dia.label}
              />
              {week?.filter((alvo) => weekdayKey(alvo.local_date) === dia.value).map((alvo) => (
                <DayFatSummary key={alvo.local_date} target={alvo} />
              ))}
            </div>
          ))}
        </Stack>
      )}
    </Stack>
  );
}
