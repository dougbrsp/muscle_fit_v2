import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, isTransientApiError } from '@/lib/api';
import { PAIR_SUGGESTION_QUERY_ROOT, WORKOUT_MODE_PLAN_QUERY_ROOT } from '@/lib/deload';
import {
  EQUIPMENT_LOAD_CEILING_PATH,
  EQUIPMENT_LOAD_CEILING_QUERY_ROOT,
  LOAD_CEILING_ERROR_MESSAGE,
  LOAD_CEILING_OFFLINE_MESSAGE,
  equipmentLoadCeilingPath,
  equipmentLoadCeilingQueryKey,
  type EquipmentLoadCeilingPair,
  type EquipmentLoadCeilingResponse,
} from '@/lib/equipment-load-ceiling';

export type LoadCeilingControl = {
  /** Teto do par em kg TOTAIS devolvido pelo servidor; `null` = sem teto. */
  maxLoadKg: number | null;
  busy: boolean;
  /** Marca o teto com a carga (kg) da série. Offline: avisa, não enfileira. */
  onMark: (loadKg: number) => void;
  onClear: () => void;
};

type MarkVariables = { pair: EquipmentLoadCeilingPair; loadKg: number };
type ClearVariables = { pair: EquipmentLoadCeilingPair };

/**
 * Estado e ações do teto de carga do par em execução. Falha de leitura (offline,
 * 503 antes da migração) = sem teto, sem erro na tela; as ações falham de forma
 * amigável (aviso), sem fila offline: o teto não é uma série.
 *
 * O par vai nas variáveis do `mutate` (a chave do cache sai delas, não do render
 * em que o clique foi dado) e, ao assentar, invalida o teto, a sugestão do par e
 * a ficha do Modo Treino (que mostram a carga/nota já limitadas pelo servidor).
 */
export function useEquipmentLoadCeiling(pair: EquipmentLoadCeilingPair | null): LoadCeilingControl {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: equipmentLoadCeilingQueryKey(pair),
    queryFn: () => apiFetch<EquipmentLoadCeilingResponse>(equipmentLoadCeilingPath(pair as EquipmentLoadCeilingPair)),
    enabled: pair != null,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const onError = (error: unknown) => {
    const offline = isTransientApiError(error) || (typeof navigator !== 'undefined' && navigator.onLine === false);
    notifications.show({ color: 'orange', message: offline ? LOAD_CEILING_OFFLINE_MESSAGE : LOAD_CEILING_ERROR_MESSAGE });
  };
  const onSettled = () => {
    for (const root of [EQUIPMENT_LOAD_CEILING_QUERY_ROOT, PAIR_SUGGESTION_QUERY_ROOT, WORKOUT_MODE_PLAN_QUERY_ROOT]) {
      void queryClient.invalidateQueries({ queryKey: [root] });
    }
  };

  const mark = useMutation({
    mutationFn: ({ pair: target, loadKg }: MarkVariables) =>
      apiFetch<EquipmentLoadCeilingResponse>(EQUIPMENT_LOAD_CEILING_PATH, {
        method: 'PUT',
        body: JSON.stringify({
          mapping_id: target.mappingId,
          gym_equipment_id: target.gymEquipmentId,
          max_load_kg: loadKg,
        }),
      }),
    onSuccess: (data, { pair: target }) => {
      queryClient.setQueryData<EquipmentLoadCeilingResponse>(equipmentLoadCeilingQueryKey(target), {
        ok: true,
        max_load_kg: data.max_load_kg,
      });
    },
    onError,
    onSettled,
  });
  const clear = useMutation({
    mutationFn: ({ pair: target }: ClearVariables) =>
      apiFetch<EquipmentLoadCeilingResponse>(equipmentLoadCeilingPath(target), { method: 'DELETE' }),
    onSuccess: (_data, { pair: target }) => {
      queryClient.setQueryData<EquipmentLoadCeilingResponse>(equipmentLoadCeilingQueryKey(target), {
        ok: true,
        max_load_kg: null,
      });
    },
    onError,
    onSettled,
  });

  return {
    maxLoadKg: query.data?.max_load_kg ?? null,
    busy: mark.isPending || clear.isPending,
    onMark: (loadKg) => {
      if (pair != null && loadKg > 0) mark.mutate({ pair, loadKg });
    },
    onClear: () => {
      if (pair != null) clear.mutate({ pair });
    },
  };
}
