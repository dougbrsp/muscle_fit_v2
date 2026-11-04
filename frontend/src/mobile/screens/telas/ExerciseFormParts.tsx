'use client';

import { ActionIcon, NumberInput, TextInput } from '@mantine/core';
import { IconCheck, IconMinus, IconPlus, IconSearch } from '@tabler/icons-react';
import { useId, useState, type ReactNode } from 'react';
import { EquipmentPhoto } from '@/components/EquipmentPhoto';
import { BottomSheet, ListGroup, ListRow } from '../../ui';
import styles from './ExerciseFormParts.module.css';

export type PickerOption = { value: string; label: string; photo_url?: string | null };

/** Acima disto a folha de escolha ganha o campo de busca (conforto de rolagem, não regra). */
const SEARCH_FROM_OPTIONS = 8;

function normalize(text: string): string {
  return text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

/**
 * Escolha de uma opção em folha, no lugar do select em cascata: a linha mostra
 * o valor inteiro (sem truncar em 320 px) e o toque abre uma lista de linhas de
 * 52 pt. Só apresentação: opções e efeito da escolha vêm de quem usa.
 */
export function OptionPickerRow({
  label,
  placeholder,
  options,
  value,
  onSelect,
  disabled,
  clearLabel,
  emptyText,
}: {
  label: string;
  /** Texto da linha enquanto nada está escolhido (ou enquanto está desabilitada). */
  placeholder: string;
  options: PickerOption[];
  value: string | null;
  onSelect: (value: string | null) => void;
  disabled?: boolean;
  /** Presente: a folha oferece esta opção para limpar a escolha (ex.: "Nenhum (exercício avulso)"). */
  clearLabel?: string;
  emptyText: string;
}) {
  const [opened, setOpened] = useState(false);
  const [query, setQuery] = useState('');
  const selected = options.find((o) => o.value === value) ?? null;
  // Valor escolhido que a lista atual não traz (ex.: opções ainda carregando): mostra o valor.
  const shown = selected?.label ?? value ?? null;
  const filtered = query.trim() ? options.filter((o) => normalize(o.label).includes(normalize(query))) : options;

  function close() {
    setOpened(false);
    setQuery('');
  }

  const title: ReactNode = shown ? shown : <span className={styles.placeholder}>{placeholder}</span>;

  return (
    <>
      <ListRow title={title} subtitle={label} onClick={disabled ? undefined : () => setOpened(true)} />
      <BottomSheet opened={opened} onClose={close} title={label}>
        {options.length >= SEARCH_FROM_OPTIONS ? (
          <div className={styles.search}>
            <TextInput
              aria-label={`Buscar em ${label}`}
              placeholder="Buscar"
              leftSection={<IconSearch size={16} />}
              value={query}
              onChange={(event) => setQuery(event.currentTarget.value)}
            />
          </div>
        ) : null}
        {options.length === 0 ? (
          <p className={styles.empty}>{emptyText}</p>
        ) : (
          <ListGroup aria-label={label}>
            {clearLabel && !query.trim() ? (
              <ListRow
                title={clearLabel}
                trailing={value == null ? <IconCheck size={20} className={styles.check} aria-hidden /> : <span />}
                onClick={() => {
                  onSelect(null);
                  close();
                }}
              />
            ) : null}
            {filtered.map((option) => (
              <ListRow
                key={option.value}
                title={
                  option.photo_url ? (
                    <span className={styles.withPhoto}>
                      <EquipmentPhoto photoUrl={option.photo_url} alt="" size={32} />
                      <span>{option.label}</span>
                    </span>
                  ) : (
                    option.label
                  )
                }
                trailing={option.value === value ? <IconCheck size={20} className={styles.check} aria-hidden /> : <span />}
                onClick={() => {
                  onSelect(option.value);
                  close();
                }}
              />
            ))}
            {filtered.length === 0 ? <p className={styles.empty}>{emptyText}</p> : null}
          </ListGroup>
        )}
      </BottomSheet>
    </>
  );
}

/**
 * Campo numérico de uma mão: rótulo à esquerda e, à direita, menos, valor
 * editável e mais (alvos de 44 pt). Limites, passo e casas chegam prontos de
 * quem usa (os mesmos campos do formulário do desktop); aqui nada é decidido.
 */
export function StepperField({
  label,
  value,
  onChange,
  step,
  min,
  max,
  decimalScale = 0,
  inputMode = 'numeric',
  stepper = true,
}: {
  label: string;
  value: number | null | undefined;
  onChange: (value: number | null) => void;
  step: number;
  min: number;
  max: number;
  decimalScale?: number;
  inputMode?: 'numeric' | 'decimal';
  /** Falso: só o campo (sem menos/mais), no mesmo alinhamento da linha. */
  stepper?: boolean;
}) {
  const id = useId();
  const current = value == null || Number.isNaN(value) ? null : value;
  const bump = (dir: 1 | -1) => {
    const next = Number(((current ?? 0) + dir * step).toFixed(decimalScale + 2));
    onChange(Math.min(max, Math.max(min, next)));
  };
  return (
    <div className={styles.field}>
      <label className={styles.fieldLabel} htmlFor={id}>
        {label}
      </label>
      <div className={styles.fieldControl}>
        {stepper ? (
          <ActionIcon
            size={44}
            radius="xl"
            variant="light"
            color="brand"
            aria-label={`Diminuir ${label}`}
            disabled={current == null || current <= min}
            onClick={() => bump(-1)}
          >
            <IconMinus size={18} />
          </ActionIcon>
        ) : null}
        <NumberInput
          id={id}
          hideControls
          inputMode={inputMode}
          min={min}
          max={max}
          step={step}
          decimalScale={decimalScale}
          value={current ?? undefined}
          onChange={(v) => onChange(v === '' || v == null ? null : Number(v))}
          styles={{ input: { textAlign: 'center', fontSize: 18, fontWeight: 700, height: 44, paddingInline: 4 } }}
          className={styles.fieldInput}
        />
        {stepper ? (
          <ActionIcon
            size={44}
            radius="xl"
            variant="light"
            color="brand"
            aria-label={`Aumentar ${label}`}
            disabled={current != null && current >= max}
            onClick={() => bump(1)}
          >
            <IconPlus size={18} />
          </ActionIcon>
        ) : null}
      </div>
    </div>
  );
}
