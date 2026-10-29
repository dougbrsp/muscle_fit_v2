'use client';

import { IconBodyScan, IconChevronDown } from '@tabler/icons-react';
import { useId, useState } from 'react';
import type { MuscleMapEntry } from '@/components/MuscleBodyMap';
import { MuscleBodyMapLazy as MuscleBodyMap } from '@/components/MuscleBodyMapLazy';
import { MUSCLE_MAP_COLORS, MUSCLE_MAP_LEGEND } from '@/lib/workout-mode-types';
import styles from './workout-mode.module.css';

/**
 * "Conecte-se com os seus músculos": diagrama frente/costas do
 * `MuscleBodyMap` (o mesmo do dashboard de recuperação), com a legenda
 * Principal/Secundário dele. Uma linha de 44 px recolhida por padrão que
 * expande no lugar; fechado, o mapa nem é montado (não baixa nada).
 *
 * `subject` diz DE QUAL exercício são os músculos ("Exercício atual: Supino"
 * / "Próximo exercício: Remada"), para a tela nunca misturar grupos.
 * `openClassName` deixa a linha aberta ocupar a fileira toda (ver `.pairRow`).
 */
export function MuscleMapDisclosure({
  items,
  subject,
  title = 'Conecte-se com os seus músculos',
  defaultOpen = false,
  openClassName,
}: {
  items: MuscleMapEntry[];
  subject?: string | null;
  title?: string;
  defaultOpen?: boolean;
  openClassName?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const baseId = useId();
  const headerId = `${baseId}-header`;
  const panelId = `${baseId}-panel`;
  if (items.length === 0) return null;

  return (
    <section
      className={open && openClassName ? `${styles.accordion} ${openClassName}` : styles.accordion}
      data-testid="muscle-map-disclosure"
    >
      <button
        type="button"
        id={headerId}
        className={styles.accordionHeader}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
      >
        <span className={styles.accordionIcon} aria-hidden="true">
          <IconBodyScan size={18} />
        </span>
        <span className={styles.accordionText}>
          <span className={styles.accordionTitle}>{title}</span>
          {subject && <span className={styles.accordionSummary}>{subject}</span>}
        </span>
        <span className={styles.accordionChevron} aria-hidden="true">
          <IconChevronDown size={18} />
        </span>
      </button>
      <div id={panelId} role="region" aria-labelledby={headerId} hidden={!open} className={styles.mapPanel}>
        {open && <MuscleBodyMap items={items} colors={MUSCLE_MAP_COLORS} legend={MUSCLE_MAP_LEGEND} />}
      </div>
    </section>
  );
}
