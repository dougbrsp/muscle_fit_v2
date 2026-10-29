'use client';

import {
  NumberInput,
  Textarea,
  TextInput,
  type NumberInputProps,
  type TextareaProps,
  type TextInputProps,
} from '@mantine/core';
import { useEffect, useRef, useState, type FocusEvent } from 'react';

// Campos do builder do modelo de treino com ESTADO LOCAL e promoção no blur
// (M10). O estado do builder é um só e grande (slots + itens + todas as
// tabelas de preview): promover cada tecla a ele re-renderiza a tela inteira
// e, antes, ainda refazia o preview de ativação. Aqui a digitação fica no
// componente; o valor só sobe para o builder quando o foco sai do campo (troca
// de campo/card), e o preview reage a essa promoção — nunca a cada tecla.
//
// O `value` vindo do pai continua sendo a fonte de verdade: mudanças externas
// (ex.: "Aplicar" do preenchimento em lote de % no M1) ressincronizam o rascunho.

type CommitTextProps = Omit<TextInputProps, 'value' | 'onChange' | 'defaultValue' | 'onBlur'> & {
  value: string;
  onCommit: (value: string) => void;
  onBlur?: (event: FocusEvent<HTMLInputElement>) => void;
};

export function BlurCommitTextInput({ value, onCommit, onBlur, ...rest }: CommitTextProps) {
  const [draft, setDraft] = useState(value);
  const draftRef = useRef(value);
  useEffect(() => {
    setDraft(value);
    draftRef.current = value;
  }, [value]);

  return (
    <TextInput
      {...rest}
      value={draft}
      onChange={(e) => {
        draftRef.current = e.currentTarget.value;
        setDraft(e.currentTarget.value);
      }}
      onBlur={(e) => {
        if (draftRef.current !== value) onCommit(draftRef.current);
        onBlur?.(e);
      }}
    />
  );
}

type CommitTextareaProps = Omit<TextareaProps, 'value' | 'onChange' | 'defaultValue' | 'onBlur'> & {
  value: string;
  onCommit: (value: string) => void;
  onBlur?: (event: FocusEvent<HTMLTextAreaElement>) => void;
};

export function BlurCommitTextarea({ value, onCommit, onBlur, ...rest }: CommitTextareaProps) {
  const [draft, setDraft] = useState(value);
  const draftRef = useRef(value);
  useEffect(() => {
    setDraft(value);
    draftRef.current = value;
  }, [value]);

  return (
    <Textarea
      {...rest}
      value={draft}
      onChange={(e) => {
        draftRef.current = e.currentTarget.value;
        setDraft(e.currentTarget.value);
      }}
      onBlur={(e) => {
        if (draftRef.current !== value) onCommit(draftRef.current);
        onBlur?.(e);
      }}
    />
  );
}

type CommitNumberProps = Omit<NumberInputProps, 'value' | 'onChange' | 'defaultValue' | 'onBlur'> & {
  value: number | string | undefined;
  onCommit: (value: number | string) => void;
  onBlur?: (event: FocusEvent<HTMLInputElement>) => void;
};

export function BlurCommitNumberInput({ value, onCommit, onBlur, ...rest }: CommitNumberProps) {
  const [draft, setDraft] = useState<number | string>(value ?? '');
  const draftRef = useRef<number | string>(value ?? '');
  useEffect(() => {
    setDraft(value ?? '');
    draftRef.current = value ?? '';
  }, [value]);

  return (
    <NumberInput
      {...rest}
      value={draft}
      onChange={(v) => {
        draftRef.current = v;
        setDraft(v);
      }}
      onBlur={(e) => {
        // O clamp de min/max do Mantine dispara `onChange` dentro do blur,
        // antes deste handler — `draftRef` já traz o valor final.
        if (draftRef.current !== (value ?? '')) onCommit(draftRef.current);
        onBlur?.(e);
      }}
    />
  );
}
