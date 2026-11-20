import { fmtKg, fmtSignedPercent } from './dashboard-format';
import type { WindowTrend } from './dashboard-v3-types';
import type { ProgressionScope } from './progression-scope';

/** O que a tela mostra da tendência da janela (#176); só formata o que o backend mandou. */
export type WindowTrendView = {
  statusCode: string;
  reasons: string[];
  scope: ProgressionScope | null;
  /** "+12,4%"; `null` em dados insuficientes. */
  changeText: string | null;
  /** "53,3 kg → 66,7 kg"; `null` em dados insuficientes. */
  meansText: string | null;
  /** Versão da regra, para o rodapé; `null` sem ela. */
  ruleVersion: string | null;
};

/** `null` com tendência nula/ausente (servidor antigo): a seção não aparece. */
export function buildWindowTrendView(trend: WindowTrend | null | undefined): WindowTrendView | null {
  if (!trend) return null;
  const hasNumbers = trend.change_pct != null && trend.initial_mean != null && trend.final_mean != null;
  return {
    statusCode: trend.status,
    reasons: trend.reasons ?? [],
    scope: trend.scope ?? null,
    // `change_pct` é fração; ×100 é só unidade de exibição (fmtSignedPercent espera percentual).
    changeText: hasNumbers ? fmtSignedPercent((trend.change_pct as number) * 100) : null,
    meansText: hasNumbers ? `${fmtKg(trend.initial_mean)} → ${fmtKg(trend.final_mean)}` : null,
    ruleVersion: trend.rule_version ?? null,
  };
}
