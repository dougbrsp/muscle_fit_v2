'use client';

import { Alert, Button, FileButton, Group, Loader, Modal, Select, Stack, Text, Textarea } from '@mantine/core';
import { IconAlertTriangle, IconCamera, IconPhoto, IconSparkles } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
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
import type { AiQuota, NutritionLimitation } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

type Props = {
  opened: boolean;
  onClose: () => void;
  date: string;
  /** A leitura terminou: o editor da refeição abre com os itens para confirmar. */
  onParsed: (form: MealEditorForm) => void;
  /** Descrever em texto (L5b) ou fotografar o prato (L10). */
  mode?: 'text' | 'photo';
};

type ReadingError = { message: string; items: NutritionLimitation[] };

const STAGE_TEXT: Record<LabelPhotoStage, string> = {
  resizing: 'Reduzindo a foto no seu aparelho…',
  uploading: 'Enviando a foto…',
  reading: 'Identificando os alimentos (pode levar alguns segundos)…',
};

function ErrorAlert({ erro }: { erro: ReadingError }) {
  return (
    <Alert color="red" icon={<IconAlertTriangle size={16} />}>
      {erro.message}
      {erro.items.map((item) => (
        <Text key={item.code} size="sm">
          {item.label}
        </Text>
      ))}
    </Alert>
  );
}

/**
 * Leitura da refeição por IA: a pessoa descreve (L5b) ou fotografa (L10) o que comeu,
 * a API dispara o job (202) e a tela espera o resultado. Os itens, a escolha do
 * alimento no catálogo e a quantidade proposta vêm prontos do motor; a pessoa edita
 * tudo no editor de refeição de sempre, que grava pelo caminho de sempre. A cota do
 * dia (de quem pede) vem da API.
 *
 * Estado e mutações ficam AQUI, fora do `<Modal>`, que desmonta o conteúdo ao fechar:
 * o rascunho sobrevive, e enquanto há envio o modal não fecha (nem por X, Esc ou clique
 * fora), para não permitir um segundo envio que gastaria a cota de novo.
 */
export function MealParseModal({ opened, onClose, date, onParsed, mode = 'text' }: Props) {
  const qc = useQueryClient();
  const [texto, setTexto] = useState('');
  const [slotTexto, setSlotTexto] = useState<string | null>(null);
  const [slotFoto, setSlotFoto] = useState<string | null>(null);
  const [stage, setStage] = useState<LabelPhotoStage | null>(null);
  const [semItens, setSemItens] = useState<NutritionLimitation[]>([]);

  const cotaTexto = useQuery({
    queryKey: mealParseKeys.quota,
    queryFn: fetchMealParseQuota,
    enabled: opened && mode === 'text',
    retry: false,
    staleTime: 30_000,
  });
  const cotaFoto = useQuery({
    queryKey: mealPhotoKeys.usage,
    queryFn: fetchMealPhotoUsage,
    enabled: opened && mode === 'photo',
    retry: false,
    staleTime: 30_000,
  });

  const lerTexto = useMutation({
    mutationFn: () =>
      parseMealText({
        text: texto,
        local_date: date,
        meal_slot: slotTexto,
      }),
    onSuccess: (resultado) => {
      onParsed(editorFormFromParse(resultado, { date, slot: slotTexto, text: texto }));
      setTexto('');
      setSlotTexto(null);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: mealParseKeys.quota }),
  });

  const lerFoto = useMutation({
    mutationFn: (file: File) =>
      readMealPhoto(file, { local_date: date, meal_slot: slotFoto }, setStage),
    onSuccess: (resultado) => {
      if (resultado.items.length === 0) {
        setSemItens(resultado.limitations);
        return;
      }
      onParsed(editorFormFromPhoto(resultado, { date, slot: slotFoto }));
      setSlotFoto(null);
    },
    onSettled: async () => {
      setStage(null);
      await qc.invalidateQueries({ queryKey: mealPhotoKeys.usage });
    },
  });

  const enviando = lerTexto.isPending || lerFoto.isPending;

  function fechar() {
    if (enviando) return;
    lerTexto.reset();
    lerFoto.reset();
    setSemItens([]);
    onClose();
  }

  function escolherFoto(file: File) {
    if (lerFoto.isPending) return;
    setSemItens([]);
    lerFoto.mutate(file);
  }

  // Erro da API (413/415/429/503): texto e itens do motor. Falha da redução ou do job
  // (`GET /jobs`): a mensagem já vem pronta (do navegador ou da API).
  const erroFoto: ReadingError | null = lerFoto.isError
    ? lerFoto.error instanceof ApiError
      ? nutritionErrorInfo(lerFoto.error, 'Não foi possível analisar a foto.')
      : { message: lerFoto.error.message || 'Não foi possível analisar a foto.', items: [] }
    : null;

  return (
    <Modal
      opened={opened}
      onClose={fechar}
      closeOnEscape={!enviando}
      closeOnClickOutside={!enviando}
      withCloseButton={!enviando}
      title={mode === 'photo' ? 'Foto do prato' : 'Descrever a refeição'}
      size="lg"
    >
      {mode === 'photo' ? (
        <PhotoReading
          slot={slotFoto}
          onSlotChange={setSlotFoto}
          quota={cotaFoto.data ?? null}
          busy={lerFoto.isPending}
          stage={stage}
          noItems={semItens}
          error={erroFoto}
          onPick={escolherFoto}
          onClose={fechar}
        />
      ) : (
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
            disabled={lerTexto.isPending}
          />
          <Select
            label="Refeição (opcional)"
            placeholder="Escolha"
            data={[...MEAL_SLOT_OPTIONS]}
            value={slotTexto}
            onChange={setSlotTexto}
            clearable
            w={260}
            disabled={lerTexto.isPending}
          />
          {cotaTexto.data && (
            <Text size="xs" c={BRAND.textMuted}>
              Leituras por IA hoje: {cotaTexto.data.used} de {cotaTexto.data.limit}.
            </Text>
          )}
          {cotaTexto.data && !cotaTexto.data.allowed && (
            <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
              {MEAL_PARSE_QUOTA_EXCEEDED_LABEL}
            </Alert>
          )}
          {lerTexto.isError && <ErrorAlert erro={nutritionErrorInfo(lerTexto.error, 'Não foi possível ler a refeição.')} />}
          <Group justify="flex-end">
            <Button variant="subtle" color="gray" onClick={fechar} disabled={lerTexto.isPending}>
              Cancelar
            </Button>
            <Button
              color="brand"
              leftSection={<IconSparkles size={16} />}
              onClick={() => lerTexto.mutate()}
              loading={lerTexto.isPending}
              disabled={!texto.trim() || (cotaTexto.data ? !cotaTexto.data.allowed : false)}
            >
              Ler com IA
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}

type PhotoReadingProps = {
  slot: string | null;
  onSlotChange: (slot: string | null) => void;
  quota: AiQuota | null;
  busy: boolean;
  stage: LabelPhotoStage | null;
  /** Foto sem comida ou não identificada: o motivo, pronto do motor. */
  noItems: NutritionLimitation[];
  error: ReadingError | null;
  onPick: (file: File) => void;
  onClose: () => void;
};

/**
 * Foto do prato (L10): câmera ou galeria; a foto é SEMPRE reduzida por canvas antes do
 * envio (sem EXIF/XMP) e a IA só identifica os alimentos e estima os gramas. Foto sem
 * comida ou não identificada volta com a lista vazia e o motivo, sem abrir o editor.
 */
export function PhotoReading({ slot, onSlotChange, quota, busy, stage, noItems, error, onPick, onClose }: PhotoReadingProps) {
  const resetCamera = useRef<() => void>(null);
  const resetGaleria = useRef<() => void>(null);
  const esgotada = quota ? !quota.allowed : false;

  function escolher(file: File | null) {
    if (!file) return;
    onPick(file);
    // Sem isto, escolher a mesma foto depois de um erro não dispara o `change` do input.
    resetCamera.current?.();
    resetGaleria.current?.();
  }

  return (
    <Stack gap="sm">
      <Text size="sm" c={BRAND.textMuted}>
        Fotografe o prato de cima, inteiro e bem iluminado. A foto é reduzida no seu aparelho antes do envio e expira
        depois de {MEAL_PHOTO.retention_label}. A IA identifica os alimentos e estima os gramas; você confere, edita,
        remove ou adiciona itens antes de salvar.
      </Text>
      <Select
        label="Refeição (opcional)"
        placeholder="Escolha"
        data={[...MEAL_SLOT_OPTIONS]}
        value={slot}
        onChange={onSlotChange}
        clearable
        w={260}
        disabled={busy}
      />
      {quota && (
        <Text size="xs" c={BRAND.textMuted}>
          Análises de foto hoje: {quota.used} de {quota.limit}.
        </Text>
      )}
      {esgotada && (
        <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
          {MEAL_PHOTO.quota_exceeded_label}
        </Alert>
      )}
      {busy && stage && (
        <Group gap="xs">
          <Loader size="xs" />
          <Text size="sm" c={BRAND.textMuted}>
            {STAGE_TEXT[stage]}
          </Text>
        </Group>
      )}
      {noItems.map((aviso) => (
        <Alert key={aviso.code} color="yellow" icon={<IconAlertTriangle size={16} />}>
          {aviso.label}
        </Alert>
      ))}
      {error && <ErrorAlert erro={error} />}
      <Group justify="flex-end" gap="sm" wrap="wrap">
        <Button variant="subtle" color="gray" onClick={onClose} disabled={busy}>
          Cancelar
        </Button>
        <FileButton resetRef={resetGaleria} onChange={escolher} accept="image/*" disabled={busy || esgotada}>
          {(props) => (
            <Button {...props} variant="light" color="brand" leftSection={<IconPhoto size={16} />} disabled={busy || esgotada}>
              Escolher da galeria
            </Button>
          )}
        </FileButton>
        <FileButton resetRef={resetCamera} onChange={escolher} accept="image/*" capture="environment" disabled={busy || esgotada}>
          {(props) => (
            <Button {...props} color="brand" leftSection={<IconCamera size={16} />} loading={busy} disabled={esgotada}>
              Tirar foto
            </Button>
          )}
        </FileButton>
      </Group>
    </Stack>
  );
}
