'use client';

import { useEffect, useState } from 'react';

/**
 * Flag da UI mobile (plano mobile, seção 3.2). Módulo único: nenhum outro
 * arquivo lê `NEXT_PUBLIC_MOBILE_UI` nem `musclefit.flag.mobile-ui`.
 *
 * Precedência (maior primeiro):
 *   1. query `?mobile=0|1` (QA) — persistida so por `applyQueryOverride()`;
 *   2. override persistido em `localStorage['musclefit.flag.mobile-ui']` = `on`/`off`;
 *   3. `NEXT_PUBLIC_MOBILE_UI` (build-time); sem valor reconhecido = DESLIGADA.
 *
 * Sem regra de negócio: só liga/desliga a UI. Todo acesso a storage é
 * protegido (modo privado, cookies bloqueados e SSR não podem quebrar a tela).
 */

export const MOBILE_UI_STORAGE_KEY = 'musclefit.flag.mobile-ui';
export const MOBILE_UI_QUERY_PARAM = 'mobile';

export type MobileUiOverride = 'on' | 'off';

const TRUTHY = new Set(['1', 'true', 'on', 'yes']);
const FALSY = new Set(['0', 'false', 'off', 'no']);

/** Interpreta o valor build-time; qualquer valor desconhecido/ausente = false. */
export function parseBuildFlag(raw: string | undefined | null): boolean {
  if (raw == null) return false;
  return TRUTHY.has(raw.trim().toLowerCase());
}

/** Interpreta o valor do override em storage; inválido = sem override. */
export function parseStoredOverride(raw: string | null | undefined): MobileUiOverride | null {
  if (raw === 'on' || raw === 'off') return raw;
  return null;
}

/** Interpreta `?mobile=`; só `0` e `1` valem (QA). */
export function parseQueryOverride(search: string | null | undefined): MobileUiOverride | null {
  if (!search) return null;
  let value: string | null;
  try {
    value = new URLSearchParams(search).get(MOBILE_UI_QUERY_PARAM);
  } catch {
    return null;
  }
  if (value === '1') return 'on';
  if (value === '0') return 'off';
  return null;
}

/** Pura: decide a flag a partir das três fontes já lidas. */
export function resolveMobileUi(
  buildFlag: boolean,
  storedOverride: MobileUiOverride | null,
  queryOverride: MobileUiOverride | null,
): boolean {
  const override = queryOverride ?? storedOverride;
  if (override) return override === 'on';
  return buildFlag;
}

function readStorage(): MobileUiOverride | null {
  try {
    return parseStoredOverride(window.localStorage.getItem(MOBILE_UI_STORAGE_KEY));
  } catch {
    return null;
  }
}

function writeStorage(value: MobileUiOverride): void {
  try {
    window.localStorage.setItem(MOBILE_UI_STORAGE_KEY, value);
  } catch {
    // storage indisponível: o override vale só nesta leitura
  }
}

/** Remove o override persistido (volta ao valor de build). */
export function clearMobileUiOverride(): void {
  try {
    window.localStorage.removeItem(MOBILE_UI_STORAGE_KEY);
  } catch {
    // ignorado
  }
}

/**
 * Aplica o `?mobile=0|1` da URL atual persistindo o override (QA). E o UNICO
 * ponto com efeito colateral; chame-o uma vez, em efeito/inicializacao
 * (ex.: provider raiz), nunca durante o render. No servidor nao faz nada.
 */
export function applyQueryOverride(): void {
  if (typeof window === 'undefined') return;
  try {
    const queryOverride = parseQueryOverride(window.location.search);
    if (queryOverride) writeStorage(queryOverride);
  } catch {
    // ignorado
  }
}

/**
 * Flag efetiva, leitura PURA (build + storage + query; nao grava nada).
 * No servidor/prerender (sem `window`) usa so o valor de build.
 * NAO chamar durante o render de componentes: no export estatico o servidor
 * devolve o valor de build e o cliente pode divergir (hydration mismatch).
 * Em componentes use `useMobileUi()`.
 * `process.env.NEXT_PUBLIC_MOBILE_UI` precisa ficar literal para o Next
 * substitui-lo em build-time.
 */
export function isMobileUiEnabled(): boolean {
  const buildFlag = parseBuildFlag(process.env.NEXT_PUBLIC_MOBILE_UI);
  if (typeof window === 'undefined') return buildFlag;

  let queryOverride: MobileUiOverride | null = null;
  try {
    queryOverride = parseQueryOverride(window.location.search);
  } catch {
    queryOverride = null;
  }
  return resolveMobileUi(buildFlag, readStorage(), queryOverride);
}

/** `null` ate montar (sem hydration mismatch); depois a flag efetiva. */
export function useMobileUi(): boolean | null {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  useEffect(() => {
    setEnabled(isMobileUiEnabled());
  }, []);
  return enabled;
}
