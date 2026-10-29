'use client';

import { Badge, Divider, Group, Paper, Stack, Text } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { countPeriodizationReport, describePeriodizationEntry } from '@/lib/periodization';
import {
  PERIODIZATION_REPORT_CODE_LABEL,
  type PeriodizationReportEntry,
  type PeriodizationSeed,
} from '@/lib/periodization-types';
import { BRAND } from '@/theme';

// --- Painel do rascunho de periodização (PR-17) -----------------------------

function ReportEntryList({ entries, color }: { entries: PeriodizationReportEntry[]; color: string }) {
  return (
    <Stack gap={6}>
      {entries.map((entry, index) => (
        <Group key={`${entry.code}-${index}`} gap="xs" align="flex-start" wrap="nowrap">
          <Badge size="sm" variant="light" color={color}>
            {PERIODIZATION_REPORT_CODE_LABEL[entry.code] || entry.code}
          </Badge>
          <Text size="xs" c={BRAND.textPrimary}>
            {describePeriodizationEntry(entry)}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

/**
 * Painel lateral do rascunho gerado: relatório do validador determinístico
 * (`errors`/`warnings`/`auto_fixes`), o `rationale` do modelo e as
 * `source_citations`. Tudo informativo — não bloqueia a edição, e nada aqui foi
 * gravado em banco.
 *
 * `errors` na prática vem vazio (um rascunho com erro estrutural termina o job
 * em `failed` e nunca chega até aqui), mas é exibido do mesmo jeito: se um dia
 * chegar, o usuário precisa ver, não descobrir no 422 do save.
 */
export function PeriodizationDraftPanel({ seed }: { seed: PeriodizationSeed }) {
  const { report } = seed;
  return (
    <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Stack gap="sm">
        <Group gap="xs" wrap="wrap">
          <Badge color="violet" variant="light" leftSection={<IconInfoCircle size={12} />}>
            Rascunho gerado
          </Badge>
          {seed.model && (
            <Badge color="gray" variant="light">
              {seed.model}
            </Badge>
          )}
          {seed.promptVersion && (
            <Badge color="gray" variant="light">
              {seed.promptVersion}
            </Badge>
          )}
        </Group>

        {countPeriodizationReport(report) === 0 ? (
          <Text size="xs" c={BRAND.textMuted}>
            O validador determinístico não apontou nenhum erro, aviso ou correção automática neste rascunho.
          </Text>
        ) : (
          <Stack gap="sm">
            {report.errors.length > 0 && (
              <div>
                <Text size="xs" fw={700} c={BRAND.textPrimary}>
                  Erros ({report.errors.length})
                </Text>
                <ReportEntryList entries={report.errors} color="red" />
              </div>
            )}
            {report.warnings.length > 0 && (
              <div>
                <Text size="xs" fw={700} c={BRAND.textPrimary}>
                  Avisos ({report.warnings.length})
                </Text>
                <ReportEntryList entries={report.warnings} color="orange" />
              </div>
            )}
            {report.auto_fixes.length > 0 && (
              <div>
                <Text size="xs" fw={700} c={BRAND.textPrimary}>
                  Correções automáticas ({report.auto_fixes.length})
                </Text>
                <ReportEntryList entries={report.auto_fixes} color="teal" />
              </div>
            )}
          </Stack>
        )}

        {seed.rationale && (
          <>
            <Divider label="Justificativa do modelo" labelPosition="left" />
            <Text size="xs" c={BRAND.textPrimary} style={{ whiteSpace: 'pre-wrap' }}>
              {seed.rationale}
            </Text>
          </>
        )}

        {seed.sourceCitations.length > 0 && (
          <>
            <Divider label="Fontes citadas" labelPosition="left" />
            <Stack gap={2}>
              {seed.sourceCitations.map((citation, index) => (
                <Text key={`${citation}-${index}`} size="xs" c={BRAND.textMuted}>
                  {citation}
                </Text>
              ))}
            </Stack>
          </>
        )}
      </Stack>
    </Paper>
  );
}
