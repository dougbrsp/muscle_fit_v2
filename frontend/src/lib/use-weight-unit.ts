'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from './api';
import { DEFAULT_WEIGHT_UNIT, normalizeWeightUnit, type WeightUnit } from './weight-units';

// Mesma queryKey do card de perfil (`PerfilClient`): a preferência é UM campo
// de `GET /users/me`, não um endpoint novo — trocá-la em qualquer tela
// invalida a mesma entrada de cache e as duas telas concordam na hora.
export const MY_PROFILE_QUERY_KEY = ['my-profile'] as const;

// Espelho local da preferência. O servidor é a fonte da verdade (vale entre
// dispositivos); isto existe só para o Modo Treino já abrir na unidade certa,
// sem um frame em kg enquanto `GET /users/me` viaja — o mesmo padrão de
// `loadPerSetAiPreference` em `workout-mode-storage.ts`.
const STORAGE_KEY = 'musclefit.weight_unit';

type ProfileUnitResponse = { item?: { preferred_weight_unit?: string | null } | null };

export function loadStoredWeightUnit(): WeightUnit {
  if (typeof window === 'undefined') return DEFAULT_WEIGHT_UNIT;
  try {
    return normalizeWeightUnit(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return DEFAULT_WEIGHT_UNIT;
  }
}

export function saveStoredWeightUnit(unit: WeightUnit): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, unit);
  } catch {
    // modo privado / storage cheio: a preferência do servidor continua valendo
  }
}

/**
 * Unidade de carga preferida do usuário (melhoria M3).
 *
 * Leitura: `GET /users/me` -> `preferred_weight_unit` (coluna da migração 040,
 * default 'kg'). Escrita: `PUT /users/me` com o mesmo campo — nenhum endpoint
 * novo foi criado, porque isto é, por definição, um dado de PERFIL.
 *
 * A troca é OTIMISTA: a tela já mostra a unidade nova antes da resposta (o
 * usuário está no meio de uma série, não pode esperar a rede), e a requisição
 * segue em background. Se ela falhar, a preferência continua valendo nesta
 * sessão (localStorage) e volta ao valor do servidor no próximo carregamento —
 * nenhuma carga é gravada errada por isso, porque o que vai ao banco é sempre
 * convertido para kg na hora de salvar a série.
 */
export function useWeightUnit(): {
  unit: WeightUnit;
  setUnit: (unit: WeightUnit) => void;
  isSaving: boolean;
} {
  const queryClient = useQueryClient();
  const [localUnit, setLocalUnit] = useState<WeightUnit>(DEFAULT_WEIGHT_UNIT);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    setLocalUnit(loadStoredWeightUnit());
  }, []);

  const profileQuery = useQuery({
    queryKey: MY_PROFILE_QUERY_KEY,
    queryFn: () => apiFetch<ProfileUnitResponse>('/users/me'),
    staleTime: 5 * 60 * 1000,
  });

  const serverUnit = profileQuery.data?.item?.preferred_weight_unit;

  // O servidor só sobrescreve o estado local enquanto o usuário não tiver
  // trocado a unidade nesta tela — senão uma revalidação do react-query
  // desfaria a escolha dele no meio do treino.
  useEffect(() => {
    if (touched || serverUnit == null) return;
    const normalized = normalizeWeightUnit(serverUnit);
    setLocalUnit(normalized);
    saveStoredWeightUnit(normalized);
  }, [serverUnit, touched]);

  const mutation = useMutation({
    mutationFn: (unit: WeightUnit) =>
      apiFetch<ProfileUnitResponse>('/users/me', {
        method: 'PUT',
        body: JSON.stringify({ preferred_weight_unit: unit }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: MY_PROFILE_QUERY_KEY });
    },
  });

  const setUnit = useCallback(
    (unit: WeightUnit) => {
      const normalized = normalizeWeightUnit(unit);
      setTouched(true);
      setLocalUnit(normalized);
      saveStoredWeightUnit(normalized);
      mutation.mutate(normalized);
    },
    [mutation],
  );

  return { unit: localUnit, setUnit, isSaving: mutation.isPending };
}
