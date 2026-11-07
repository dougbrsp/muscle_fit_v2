import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, isTransientApiError } from '@/lib/api';
import {
  EQUIPMENT_LOAD_CEILING_PATH,
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

/**
 * Estado e ações do teto de carga do par em execução. Falha de leitura (offline,
 * 503 antes da migração) = sem teto, sem erro na tela; as ações falham de forma
 * amigável (aviso), sem fila offline: o teto não é uma série.
 */
export function useEquipmentLoadCeiling(pair: EquipmentLoadCeilingPair | null): LoadCeilingControl {
  const queryClient = useQueryClient();
  const key = equipmentLoadCeilingQueryKey(pair);
  const query = useQuery({
    queryKey: key,
    queryFn: () => apiFetch<EquipmentLoadCeilingResponse>(equipmentLoadCeilingPath(pair as EquipmentLoadCeilingPair)),
    enabled: pair != null,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const onError = (error: unknown) => {
    const offline = isTransientApiError(error) || (typeof navigator !== 'undefined' && navigator.onLine === false);
    notifications.show({ color: 'orange', message: offline ? LOAD_CEILING_OFFLINE_MESSAGE : LOAD_CEILING_ERROR_MESSAGE });
  };
  const onSuccess = (data: EquipmentLoadCeilingResponse) => {
    queryClient.setQueryData<EquipmentLoadCeilingResponse>(key, { ok: true, max_load_kg: data.max_load_kg });
  };

  const mark = useMutation({
    mutationFn: (loadKg: number) =>
      apiFetch<EquipmentLoadCeilingResponse>(EQUIPMENT_LOAD_CEILING_PATH, {
        method: 'PUT',
        body: JSON.stringify({
          mapping_id: (pair as EquipmentLoadCeilingPair).mappingId,
          gym_equipment_id: (pair as EquipmentLoadCeilingPair).gymEquipmentId,
          max_load_kg: loadKg,
        }),
      }),
    onSuccess,
    onError,
  });
  const clear = useMutation({
    mutationFn: () =>
      apiFetch<EquipmentLoadCeilingResponse>(equipmentLoadCeilingPath(pair as EquipmentLoadCeilingPair), {
        method: 'DELETE',
      }),
    onSuccess: () => queryClient.setQueryData<EquipmentLoadCeilingResponse>(key, { ok: true, max_load_kg: null }),
    onError,
  });

  return {
    maxLoadKg: query.data?.max_load_kg ?? null,
    busy: mark.isPending || clear.isPending,
    onMark: (loadKg) => {
      if (pair != null && loadKg > 0) mark.mutate(loadKg);
    },
    onClear: () => {
      if (pair != null) clear.mutate();
    },
  };
}
