'use client';

import { Badge } from '@mantine/core';
import type { MantineSize } from '@mantine/core';
import type { DataQuality, Severity } from '@/lib/dashboard-v3-types';
import { StatusBadge } from './StatusBadge';
import {
  SEVERITY_ORDER as REGISTRY_SEVERITY_ORDER,
  statusVisual,
} from '@/lib/dashboard-status';

/**
 * Severidades `INFO | ATTENTION | ACTION` (`contracts.Severity`) e veredito
 * `DataQuality` do Novo Dashboard.
 *
 * Desde a F0.4 os rótulos, cores, ícones e descrições moram em
 * `lib/dashboard-status.ts` junto com os demais enums de status; este arquivo
 * permanece como a API que a tela `/dashboard/data-quality` (F0.3) já
 * importa, agora delegando ao ponto único em vez de manter tabelas próprias.
 */

export const SEVERITY_ORDER: Severity[] = [...REGISTRY_SEVERITY_ORDER];

/** Frase do agrupamento: por que esses itens estão juntos. */
export const SEVERITY_DESCRIPTIONS: Record<Severity, string> = Object.fromEntries(
  REGISTRY_SEVERITY_ORDER.map((code) => [code, statusVisual('severity', code).description ?? '']),
) as Record<Severity, string>;

export function SeverityBadge({ severity, size = 'sm' }: { severity: Severity; size?: MantineSize }) {
  return <StatusBadge domain="severity" code={severity} size={size} />;
}

/** Veredito de qualidade com prefixo opcional ("Confiança nas conclusões: Alta"). */
export function DataQualityBadge({ quality, label }: { quality: DataQuality; label?: string }) {
  const visual = statusVisual('dataQuality', quality);
  return (
    <Badge
      color={visual.color}
      variant="light"
      radius="sm"
      leftSection={<visual.Icon size={12} aria-hidden />}
    >
      {label ? `${label}: ` : ''}
      {visual.label}
    </Badge>
  );
}
