'use client';

import { Badge, Tooltip } from '@mantine/core';
import type { MantineSize } from '@mantine/core';
import { statusVisual, type StatusDomain } from '@/lib/dashboard-status';

/**
 * F1.7 — o `Badge` do Mantine tem ALTURA FIXA e corta o texto com reticências
 * (`white-space:nowrap; overflow:hidden; text-overflow:ellipsis`). Em largura
 * de celular isso apagava justamente o que o selo precisa dizer (ex.:
 * "Próximo: Supino inclinado com halteres" virava "...com hal…"), contrariando
 * a regra §14 do plano: o rótulo SEMPRE tem de ser legível, nunca só a cor.
 *
 * Com estes `styles` o selo quebra em duas linhas quando não cabe e mantém a
 * altura do tamanho escolhido quando cabe em uma. Exportado para os badges
 * que NÃO passam pelo StatusBadge mas também exibem texto do usuário (nome de
 * equipamento, convenção de carga).
 */
export const BADGE_QUEBRA_LINHA = {
  root: {
    height: 'auto',
    minHeight: 'var(--badge-height)',
    paddingTop: 2,
    paddingBottom: 2,
  },
  label: { whiteSpace: 'normal', overflow: 'visible', lineHeight: 1.3 },
} as const;

/**
 * Badge de status do dashboard — PONTO ÚNICO de apresentação de todos os enums
 * de status (ver `lib/dashboard-status.ts`).
 *
 * Nunca depende só de cor (§14 do plano): sempre renderiza o rótulo e, por
 * padrão, o ícone do estado. `tooltip` acrescenta a explicação vinda do
 * backend (ex.: `explanation[]` da recuperação) sem substituir a descrição
 * canônica do estado.
 */
export function StatusBadge({
  domain,
  code,
  size = 'sm',
  withIcon = true,
  tooltip,
  suffix,
}: {
  domain: StatusDomain;
  code: string | null | undefined;
  size?: MantineSize;
  withIcon?: boolean;
  /** Texto extra do backend, somado à descrição canônica do estado. */
  tooltip?: string | null;
  /** Complemento curto no próprio badge (ex.: ` · alvo 8`). */
  suffix?: string | null;
}) {
  const visual = statusVisual(domain, code);
  const { Icon } = visual;

  const badge = (
    <Badge
      color={visual.color}
      variant="light"
      radius="sm"
      size={size}
      leftSection={withIcon ? <Icon size={12} aria-hidden /> : undefined}
      style={tooltip || visual.description ? { cursor: 'help' } : undefined}
      styles={BADGE_QUEBRA_LINHA}
    >
      {visual.label}
      {suffix ?? ''}
    </Badge>
  );

  const label = [visual.description, tooltip].filter(Boolean).join('\n');
  if (!label) return badge;

  return (
    <Tooltip label={label} multiline w={320} withArrow>
      {badge}
    </Tooltip>
  );
}
