'use client';

import { useRef, type KeyboardEvent } from 'react';
import { resolveSegment } from './pure';
import styles from './ui.module.css';

export type SegmentOption<V extends string> = { value: V; label: string };

/**
 * Controle segmentado (2 a 4 opções curtas): o filtro principal da tela. Os
 * demais filtros vão para a FilterSheet. Semântica de radiogroup, setas
 * movem a seleção, alvo de 40 pt de altura com folga de 4 pt no grupo.
 */
export function SegmentedFilter<V extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: readonly SegmentOption<V>[];
  value: V;
  onChange: (value: V) => void;
  /** Nome acessível do grupo (ex.: "Período"). */
  label: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const current = resolveSegment(options, value);

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0;
    if (step === 0) return;
    event.preventDefault();
    const next = (index + step + options.length) % options.length;
    onChange(options[next].value);
    refs.current[next]?.focus();
  }

  return (
    <div className={styles.segmented} role="radiogroup" aria-label={label}>
      {options.map((option, index) => (
        <button
          key={option.value}
          ref={(el) => {
            refs.current[index] = el;
          }}
          type="button"
          role="radio"
          aria-checked={option.value === current}
          tabIndex={option.value === current ? 0 : -1}
          className={styles.segment}
          onClick={() => onChange(option.value)}
          onKeyDown={(event) => onKeyDown(event, index)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
