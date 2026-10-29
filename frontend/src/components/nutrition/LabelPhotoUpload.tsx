'use client';

import { Alert, Button, FileButton, Group, Loader, Stack, Text } from '@mantine/core';
import { IconAlertTriangle, IconCamera, IconPhoto } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { NutritionLimitations } from './NutritionLimitations';
import { ApiError } from '@/lib/api';
import {
  fetchLabelPhotoUsage,
  labelPhotoKeys,
  nutritionErrorInfo,
  readLabelPhoto,
  type LabelPhotoStage,
} from '@/lib/nutrition-api';
import { LABEL_PHOTO } from '@/lib/nutrition-constants';
import type { LabelPhotoReading } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

type Props = {
  onRead: (reading: LabelPhotoReading) => void;
};

const STAGE_TEXT: Record<LabelPhotoStage, string> = {
  resizing: 'Reduzindo a foto no seu aparelho…',
  uploading: 'Enviando a foto…',
  reading: 'Lendo a tabela nutricional (pode levar alguns segundos)…',
};

/**
 * Foto do rótulo (Lote L6, plano §9 b e §10): a foto escolhida é SEMPRE reduzida
 * por canvas antes do envio (parâmetros do dono pelo JSON gerado; sem EXIF/XMP no
 * corpo, por causa do WAF) — se o navegador não conseguir reduzir, nada é enviado.
 * A API lê a tabela com IA, o motor revisa (Atwater, porção × por 100) e a tela
 * recebe o formulário pré-preenchido para o usuário conferir e salvar. Cota do dia,
 * limite e avisos vêm da API.
 */
export function LabelPhotoUpload({ onRead }: Props) {
  const qc = useQueryClient();
  const [stage, setStage] = useState<LabelPhotoStage | null>(null);

  const usageQuery = useQuery({
    queryKey: labelPhotoKeys.usage,
    queryFn: fetchLabelPhotoUsage,
    staleTime: 30_000,
    retry: false,
  });

  const leitura = useMutation({
    mutationFn: (file: File) => readLabelPhoto(file, setStage),
    onSuccess: onRead,
    onSettled: async () => {
      setStage(null);
      await qc.invalidateQueries({ queryKey: labelPhotoKeys.usage });
    },
  });

  function escolher(file: File | null) {
    if (file) leitura.mutate(file);
  }

  const uso = usageQuery.data ?? null;
  const semCota = uso !== null && !uso.allowed;
  const ocupado = leitura.isPending;
  // Erro da API (413/415/429/503): texto e itens do motor. Falha da redução ou do
  // job (`GET /jobs`): a mensagem já vem pronta (do navegador ou da API).
  const erro = leitura.isError
    ? leitura.error instanceof ApiError
      ? nutritionErrorInfo(leitura.error, 'Não foi possível ler o rótulo.')
      : { message: leitura.error.message || 'Não foi possível ler o rótulo.', items: [] }
    : null;

  return (
    <Stack gap="sm">
      <Text size="sm" c={BRAND.textMuted}>
        Fotografe a tabela nutricional da embalagem, de frente e com a tabela inteira na foto. A foto é reduzida no seu
        aparelho antes do envio e fica guardada por {LABEL_PHOTO.retention_days} dias; você confere os valores antes de
        salvar.
      </Text>
      {uso && (
        <Text size="xs" c={semCota ? 'red' : BRAND.textMuted}>
          Leituras hoje: {uso.used} de {uso.limit}.
        </Text>
      )}
      <Group gap="sm" wrap="wrap">
        <FileButton onChange={escolher} accept="image/*" capture="environment" disabled={ocupado || semCota}>
          {(props) => (
            <Button {...props} color="brand" leftSection={<IconCamera size={16} />} disabled={ocupado || semCota}>
              Tirar foto
            </Button>
          )}
        </FileButton>
        <FileButton onChange={escolher} accept="image/*" disabled={ocupado || semCota}>
          {(props) => (
            <Button {...props} variant="light" color="brand" leftSection={<IconPhoto size={16} />} disabled={ocupado || semCota}>
              Escolher foto
            </Button>
          )}
        </FileButton>
      </Group>

      {ocupado && stage && (
        <Group gap="xs">
          <Loader size="xs" />
          <Text size="sm" c={BRAND.textMuted}>
            {STAGE_TEXT[stage]}
          </Text>
        </Group>
      )}
      {erro && erro.items.length > 0 && <NutritionLimitations items={erro.items} title={erro.message} tone="error" />}
      {erro && erro.items.length === 0 && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {erro.message}
        </Alert>
      )}
    </Stack>
  );
}
