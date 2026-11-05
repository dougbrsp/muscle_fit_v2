'use client';

import { Button, Stack, Tabs, Text, Title } from '@mantine/core';
import { IconBarcode, IconCamera, IconPencil, IconWorld } from '@tabler/icons-react';
import { useState } from 'react';
import { BarcodeLookup } from './BarcodeLookup';
import { FoodForm } from './FoodForm';
import { FoodRegistrationFinish } from './FoodRegistrationFinish';
import { LabelPhotoUpload } from './LabelPhotoUpload';
import { NutritionLimitations } from './NutritionLimitations';
import { UsdaSearch } from './UsdaSearch';
import { LABEL_LEGIBILITY_OPTIONS, LABEL_PHOTO, nutritionLabel } from '@/lib/nutrition-constants';
import { foodFormFromPrefill } from '@/lib/nutrition-food-form';
import {
  manualFormWithName,
  registrationAfterBarcodeNotFound,
  type RegistrationTab,
} from '@/lib/nutrition-food-registration';
import type { ExternalFoodResponse, FoodSaveResponse, LabelPhotoReading } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

type Props = {
  /** Termo buscado: vira o nome do alimento na aba de digitação. */
  initialName?: string;
  /** Código já conhecido: preenche o EAN da digitação. */
  initialBarcode?: string;
  /** Aba inicial (padrão: digitação). */
  initialTab?: RegistrationTab;
  /** Inclui a aba de importação do USDA (página de alimentos). */
  withUsda?: boolean;
  /** Alimento novo salvo (digitação ou rótulo). */
  onSaved: (food: FoodSaveResponse) => void;
  /** Produto já existente ou trazido de fonte externa (código de barras, USDA): já gravado pela API. */
  onFound: (food: ExternalFoodResponse) => void;
  /** EAN já cadastrado (409 no cadastro): abrir/usar o alimento existente. */
  onUseExisting: (foodId: number) => void;
  /** Cancelar na digitação ou na revisão do rótulo. O fluxo volta ao estado limpo. */
  onCancel?: () => void;
  /** Muda quando a tela quer o fluxo limpo (ex.: depois de abrir um produto na página). */
  resetSignal?: number;
};

/**
 * Fluxo único de cadastro de alimento: digitação, código de barras (câmera ou número), foto do rótulo e,
 * na página de alimentos, importação do USDA. Usado em `/nutricao/alimentos` e dentro do registro da refeição
 * (modal no desktop, folha no celular). A API valida e converte; a tela só digita e exibe. As abas ficam
 * montadas para não perder o que foi digitado ao trocar; a câmera só existe com o leitor aberto e é liberada
 * ao fechar (o leitor desmonta o vídeo).
 */
export function FoodRegistrationFlow({
  initialName,
  initialBarcode,
  initialTab = 'manual',
  withUsda = false,
  onSaved,
  onFound,
  onUseExisting,
  onCancel,
  resetSignal = 0,
}: Props) {
  const [tab, setTab] = useState<RegistrationTab>(initialTab);
  const [barcode, setBarcode] = useState(initialBarcode ?? '');
  const [barcodeReads, setBarcodeReads] = useState(0);
  const [formKey, setFormKey] = useState(0);
  const [lookupResets, setLookupResets] = useState(0);
  const [labelReading, setLabelReading] = useState<LabelPhotoReading | null>(null);
  const [labelReads, setLabelReads] = useState(0);
  const [unreadable, setUnreadable] = useState<LabelPhotoReading | null>(null);
  const [lastResetSignal, setLastResetSignal] = useState(resetSignal);
  // Etapa final (favoritar/público): no lugar das abas, só depois de o alimento estar salvo/achado.
  const [finish, setFinish] = useState<{ food: FoodSaveResponse; entregar: () => void } | null>(null);

  function clear() {
    setBarcode('');
    setFormKey((n) => n + 1);
    setLookupResets((n) => n + 1);
    setLabelReading(null);
    setUnreadable(null);
    setFinish(null);
  }

  // Reset pedido de fora (ajuste de estado durante a renderização, sem efeito).
  if (lastResetSignal !== resetSignal) {
    setLastResetSignal(resetSignal);
    clear();
  }

  function afterLabelRead(reading: LabelPhotoReading) {
    if (reading.readable) {
      setUnreadable(null);
      setLabelReads((n) => n + 1);
      setLabelReading(reading);
    } else {
      setLabelReading(null);
      setUnreadable(reading);
    }
  }

  /** Código sem produto: cai na digitação com o código preenchido (o que já foi digitado fica). */
  function barcodeNotFound(ean: string): boolean {
    const destino = registrationAfterBarcodeNotFound(ean);
    if (!destino) return false;
    setBarcode(destino.barcode);
    setBarcodeReads((n) => n + 1);
    setTab(destino.tab);
    return true;
  }

  function saved(food: FoodSaveResponse) {
    clear();
    setFinish({ food, entregar: () => onSaved(food) });
  }

  function found(food: ExternalFoodResponse) {
    clear();
    setFinish({ food, entregar: () => onFound(food) });
  }

  function cancel() {
    clear();
    onCancel?.();
  }

  if (finish) {
    return (
      <FoodRegistrationFinish
        food={finish.food}
        onConclude={() => {
          finish.entregar();
          setFinish(null);
        }}
      />
    );
  }

  return (
    <Tabs value={tab} onChange={(valor) => valor && setTab(valor as RegistrationTab)} keepMounted>
      <Tabs.List style={{ flexWrap: 'nowrap', overflowX: 'auto' }}>
        <Tabs.Tab value="manual" leftSection={<IconPencil size={16} />} style={{ minHeight: 44 }}>
          Digitação
        </Tabs.Tab>
        <Tabs.Tab value="barcode" leftSection={<IconBarcode size={16} />} style={{ minHeight: 44 }}>
          Código de barras
        </Tabs.Tab>
        <Tabs.Tab value="label" leftSection={<IconCamera size={16} />} style={{ minHeight: 44 }}>
          Rótulo
        </Tabs.Tab>
        {withUsda && (
          <Tabs.Tab value="usda" leftSection={<IconWorld size={16} />} style={{ minHeight: 44 }}>
            USDA
          </Tabs.Tab>
        )}
      </Tabs.List>

      <Tabs.Panel value="manual" pt="md">
        <FoodForm
          key={`manual-${formKey}`}
          food={null}
          initial={manualFormWithName(initialName)}
          barcode={barcode || undefined}
          barcodeRead={barcodeReads}
          onUseExisting={onUseExisting}
          onSaved={saved}
          onCancel={cancel}
        />
      </Tabs.Panel>

      <Tabs.Panel value="barcode" pt="md">
        <BarcodeLookup onFound={found} onNotFound={barcodeNotFound} resetSignal={lookupResets} />
      </Tabs.Panel>

      <Tabs.Panel value="label" pt="md">
        <Stack gap="sm">
          {labelReading ? (
            <>
              <Title order={5} c={BRAND.textPrimary}>
                Alimento da foto do rótulo
              </Title>
              <Text size="sm" c={BRAND.textMuted}>
                {nutritionLabel(LABEL_LEGIBILITY_OPTIONS, labelReading.legibility)}. Confira cada valor com a embalagem
                antes de salvar: a leitura pode errar.
              </Text>
              <NutritionLimitations items={labelReading.errors} title="Corrija antes de salvar" tone="error" />
              <NutritionLimitations items={labelReading.warnings} title="Confira" tone="warning" />
              {labelReading.ai_notes.length > 0 && (
                <NutritionLimitations
                  items={labelReading.ai_notes.map((nota, i) => ({ code: `ai_note_${i}`, label: nota }))}
                  title="Observações da leitura"
                  tone="info"
                />
              )}
              <FoodForm
                key={`rotulo-${labelReads}`}
                food={null}
                initial={foodFormFromPrefill(labelReading.food, labelReading)}
                createSource={LABEL_PHOTO.food_source}
                onUseExisting={onUseExisting}
                onSaved={saved}
                onCancel={() => setLabelReading(null)}
              />
              <Button variant="subtle" color="gray" onClick={() => setLabelReading(null)} style={{ minHeight: 44 }}>
                Tirar outra foto
              </Button>
            </>
          ) : (
            <>
              <LabelPhotoUpload onRead={afterLabelRead} />
              {unreadable && (
                <NutritionLimitations
                  items={unreadable.errors}
                  title={nutritionLabel(LABEL_LEGIBILITY_OPTIONS, unreadable.legibility) ?? 'Foto do rótulo'}
                  tone="error"
                />
              )}
            </>
          )}
        </Stack>
      </Tabs.Panel>

      {withUsda && (
        <Tabs.Panel value="usda" pt="md">
          <UsdaSearch onImported={found} />
        </Tabs.Panel>
      )}
    </Tabs>
  );
}
