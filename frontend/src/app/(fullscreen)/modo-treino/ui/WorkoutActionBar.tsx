'use client';

import { Button } from '@mantine/core';
import type { ReactNode } from 'react';
import styles from '../workout-mode.module.css';
import { IconAction, type WorkoutIconAction } from './IconAction';

export type WorkoutSecondaryAction = {
  key: string;
  label: string;
  icon?: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  testId?: string;
};

/**
 * Rodapé de ação do Modo Treino: CTA primário laranja (52 px) e, NA MESMA
 * LINHA, as ações de ícone explícitas (Trocar / Pular / Encerrar, com
 * legenda curta). Ações de texto que não são do exercício (ex.: "Pular
 * série" das sub-etapas avançadas) ficam numa linha baixa logo abaixo.
 *
 * Fica no FIM do fluxo da tela (`margin-top: auto`) e grudado no rodapé
 * (`position: sticky`) com `env(safe-area-inset-bottom)`: quando a tela cabe
 * na viewport ele não cobre nada; quando não cabe (telas muito baixas), o CTA
 * continua alcançável e só o conteúdo secundário rola por baixo dele.
 */
export function WorkoutActionBar({
  primaryLabel,
  primaryIcon,
  onPrimary,
  primaryLoading = false,
  primaryDisabled = false,
  primaryTestId,
  notice,
  iconActions = [],
  secondary = [],
}: {
  primaryLabel: string;
  primaryIcon?: ReactNode;
  onPrimary: () => void;
  primaryLoading?: boolean;
  // CTA bloqueado (dados incompletos): quem chama mostra o motivo em `notice`.
  primaryDisabled?: boolean;
  primaryTestId?: string;
  // Aviso curto logo acima do CTA (erro ao salvar, campo faltando), visível
  // junto da ação em vez de perdido no fim da tela.
  notice?: ReactNode;
  // Ações do exercício como ícone + legenda, ao lado do CTA.
  iconActions?: WorkoutIconAction[];
  // Ações de texto, numa linha baixa abaixo do CTA.
  secondary?: WorkoutSecondaryAction[];
}) {
  return (
    <div className={styles.actionBar} data-testid="workout-action-bar">
      {notice && <div className={styles.actionNotice}>{notice}</div>}
      <div className={styles.actionRow}>
        {iconActions.map((action) => (
          <IconAction key={action.key} action={action} />
        ))}
        <Button
          size="lg"
          radius="xl"
          h={52}
          px="md"
          leftSection={primaryIcon}
          onClick={onPrimary}
          loading={primaryLoading}
          disabled={primaryDisabled}
          className={styles.primaryCta}
          data-testid={primaryTestId}
        >
          {primaryLabel}
        </Button>
      </div>
      {secondary.length > 0 && (
        <div className={styles.secondaryRow}>
          {secondary.map((action) => (
            <Button
              key={action.key}
              variant="subtle"
              color="gray"
              radius="xl"
              h={44}
              size="sm"
              leftSection={action.icon}
              onClick={action.onClick}
              disabled={action.disabled}
              data-testid={action.testId}
            >
              {action.label}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}
