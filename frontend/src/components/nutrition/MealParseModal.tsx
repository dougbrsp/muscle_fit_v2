'use client';

import { Alert, Button, FileButton, Group, Loader, Modal, Select, Stack, Text, Textarea } from '@mantine/core';
import { IconAlertTriangle, IconCamera, IconPhoto, IconSparkles } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { ApiError } from '@/lib/api';
import { MEAL_PARSE_QUOTA_EXCEEDED_LABEL, MEAL_PARSE_TEXT_MAX_CHARS, MEAL_PHOTO, MEAL_SLOT_OPTIONS } from '@/lib/nutrition-constants';
import {
  fetchMealParseQuota,
  fetchMealPhotoUsage,
  mealParseKeys,
  mealPhotoKeys,
  nutritionErrorInfo,
  parseMealText,
  readMealPhoto,
  type LabelPhotoStage,
} from '@/lib/nutrition-api';
import type { MealEditorForm } from '@/lib/nutrition-meal-form';
import { editorFormFromParse, editorFormFromPhoto } from '@/lib/nutrition-meal-parse';
import type { NutritionLimitation } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

type ReadingProps = {
  onClose: () => void;
  /** Aluno do diário (admin/PT) ou `null` (o próprio usuário). */
  userId: number | null;
  date: string;
  /** A leitura terminou: o editor da refeição abre com os itens para confirmar. */
  onParsed: (form: MealEditorForm) => void;
};

type Props = ReadingProps & {
  opened: boolean;
  /** Descrever em texto (L5b) ou fotografar o prato (L10). */
  mode?: 'text' | 'photo';
};

const STAGE_TEXT: Record<LabelPhotoStage, string> = {
  resizing: 'Reduzindo a foto no seu aparelho…',
  uploading: 'Enviando a foto…',
  reading: 'Identificando os alimentos (pode levar alguns segundos)…',
};

/**
 * Leitura da refeição por IA: a pessoa descreve (L5b) ou fotografa (L10) o que comeu,
 * a API dispara o job (202) e a tela espera o resultado. Os itens, a escolha do
 * alimento no catálogo e a quantidade proposta vêm prontos do motor; a pessoa edita
 * tudo no editor de refeição de sempre, que grava pelo caminho de sempre. A cota do
 * dia (de quem pede) vem da API.
 */
export function MealParseModal({ opened, onClose, userId, date, onParsed, mode = 'text' }: Props) {
  return (
    <Modal opened={opened} onClose={onClose} title={mode === 'photo' ? 'Foto do prato' : 'Descrever a refeição'} size="lg">
      {mode === 'photo' ? (
        <PhotoReading opened={opened} onClose={onClose} userId={userId} date={date} onParsed={onParsed} />
      ) : (
        <TextReading opened={opened} onClose={onClose} userId={userId} date={date} onParsed={onParsed} />
      )}
    </Modal>
  );
}

function TextReading({ opened, onClose, userId, date, onParsed }: ReadingProps & { opened: boolean }) {
  const qc = useQueryClient();
  const [texto, setTexto] = useState('');
  const [slot, setSlot] = useState<string | null>(null);

  const cota = useQuery({
    queryKey: mealParseKeys.quota,
    queryFn: fetchMealParseQuota,
    enabled: opened,
    retry: false,
    staleTime: 30_000,
  });

  const ler = useMutation({
    mutationFn: () =>
      parseMealText({
        text: texto,
        local_date: date,
        meal_slot: slot,
        ...(userId != null ? { user_id: userId } : {}),
      }),
    onSuccess: (resultado) => {
      onParsed(editorFormFromParse(resultado, { date, slot, text: texto }));
      setTexto('');
    },
    onSettled: () => qc.invalidateQueries({ queryKey: mealParseKeys.quota }),
  });

  const esgotada = cota.data ? !cota.data.allowed : false;
  const erro = ler.isError ? nutritionErrorInfo(ler.error, 'Não foi possível ler a refeição.') : null;

  return (
    <Stack gap="sm">
      <Text size="sm" c={BRAND.textMuted}>
        Escreva o que você comeu, com as quantidades se souber. A IA separa os alimentos e o diário procura cada um
        no catálogo; você confere tudo antes de salvar.
      </Text>
      <Textarea
        label="O que você comeu?"
        placeholder="Ex.: arroz, feijão, um bife grelhado e salada"
        value={texto}
        onChange={(e) => setTexto(e.currentTarget.value)}
        maxLength={MEAL_PARSE_TEXT_MAX_CHARS}
        autosize
        minRows={3}
        disabled={ler.isPending}
      />
      <Select
        label="Refeição (opcional)"
        placeholder="Escolha"
        data={[...MEAL_SLOT_OPTIONS]}
        value={slot}
        onChange={setSlot}
        clearable
        w={260}
        disabled={ler.isPending}
      />
      {cota.data && (
        <Text size="xs" c={BRAND.textMuted}>
          Leituras por IA hoje: {cota.data.used} de {cota.data.limit}.
        </Text>
      )}
      {esgotada && (
        <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
          {MEAL_PARSE_QUOTA_EXCEEDED_LABEL}
        </Alert>
      )}
      {erro && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {erro.message}
          {erro.items.map((item) => (
            <Text key={item.code} size="sm">
              {item.label}
            </Text>
          ))}
        </Alert>
      )}
      <Group justify="flex-end">
        <Button variant="subtle" color="gray" onClick={onClose} disabled={ler.isPending}>
          Cancelar
        </Button>
        <Button
          color="brand"
          leftSection={<IconSparkles size={16} />}
          onClick={() => ler.mutate()}
          loading={ler.isPending}
          disabled={!texto.trim() || esgotada}
        >
          Ler com IA
        </Button>
      </Group>
    </Stack>
  );
}

/**
 * Foto do prato (L10): câmera ou galeria; a foto é SEMPRE reduzida por canvas antes do
 * envio (sem EXIF/XMP) e a IA só identifica os alimentos e estima os gramas. Foto sem
 * comida ou não identificada volta com a lista vazia e o motivo, sem abrir o editor.
 */
function PhotoReading({ opened, onClose, userId, date, onParsed }: ReadingProps & { opened: boolean }) {
  const qc = useQueryClient();
  const [slot, setSlot] = useState<string | null>(null);
  const [stage, setStage] = useState<LabelPhotoStage | null>(null);
  const [semItens, setSemItens] = useState<NutritionLimitation[]>([]);

  const cota = useQuery({
    queryKey: mealPhotoKeys.usage,
    queryFn: fetchMealPhotoUsage,
    enabled: opened,
    retry: false,
    staleTime: 30_000,
  });

  const ler = useMutation({
    mutationFn: (file: File) =>
      readMealPhoto(file, { local_date: date, meal_slot: slot, ...(userId != null ? { user_id: userId } : {}) }, setStage),
    onSuccess: (resultado) => {
      if (resultado.items.length === 0) {
        setSemItens(resultado.limitations);
        return;
      }
      onParsed(editorFormFromPhoto(resultado, { date, slot }));
    },
    onSettled: async () => {
      setStage(null);
      await qc.invalidateQueries({ queryKey: mealPhotoKeys.usage });
    },
  });

  function escolher(file: File | null) {
    if (!file) return;
    setSemItens([]);
    ler.mutate(file);
  }

  const esgotada = cota.data ? !cota.data.allowed : false;
  const ocupado = ler.isPending;
  // Erro da API (413/415/429/503): texto e itens do motor. Falha da redução ou do job
  // (`GET /jobs`): a mensagem já vem pronta (do navegador ou da API).
  const erro = ler.isError
    ? ler.error instanceof ApiError
      ? nutritionErrorInfo(ler.error, 'Não foi possível analisar a foto.')
      : { message: ler.error.message || 'Não foi possível analisar a foto.', items: [] }
    : null;

  return (
    <Stack gap="sm">
      <Text size="sm" c={BRAND.textMuted}>
        Fotografe o prato de cima, inteiro e bem iluminado. A foto é reduzida no seu aparelho antes do envio e fica
        guardada por {MEAL_PHOTO.retention_days} dias. A IA identifica os alimentos e estima os gramas; você confere,
        edita, remove ou adiciona itens antes de salvar.
      </Text>
      <Select
        label="Refeição (opcional)"
        placeholder="Escolha"
        data={[...MEAL_SLOT_OPTIONS]}
        value={slot}
        onChange={setSlot}
        clearable
        w={260}
        disabled={ocupado}
      />
      {cota.data && (
        <Text size="xs" c={BRAND.textMuted}>
          Análises de foto hoje: {cota.data.used} de {cota.data.limit}.
        </Text>
      )}
      {esgotada && (
        <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
          {MEAL_PHOTO.quota_exceeded_label}
        </Alert>
      )}
      {ocupado && stage && (
        <Group gap="xs">
          <Loader size="xs" />
          <Text size="sm" c={BRAND.textMuted}>
            {STAGE_TEXT[stage]}
          </Text>
        </Group>
      )}
      {semItens.map((aviso) => (
        <Alert key={aviso.code} color="yellow" icon={<IconAlertTriangle size={16} />}>
          {aviso.label}
        </Alert>
      ))}
      {erro && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {erro.message}
          {erro.items.map((item) => (
            <Text key={item.code} size="sm">
              {item.label}
            </Text>
          ))}
        </Alert>
      )}
      <Group justify="flex-end" gap="sm" wrap="wrap">
        <Button variant="subtle" color="gray" onClick={onClose} disabled={ocupado}>
          Cancelar
        </Button>
        <FileButton onChange={escolher} accept="image/*" disabled={ocupado || esgotada}>
          {(props) => (
            <Button {...props} variant="light" color="brand" leftSection={<IconPhoto size={16} />} disabled={ocupado || esgotada}>
              Escolher da galeria
            </Button>
          )}
        </FileButton>
        <FileButton onChange={escolher} accept="image/*" capture="environment" disabled={ocupado || esgotada}>
          {(props) => (
            <Button {...props} color="brand" leftSection={<IconCamera size={16} />} loading={ocupado} disabled={esgotada}>
              Tirar foto
            </Button>
          )}
        </FileButton>
      </Group>
    </Stack>
  );
}
