'use client';

import { Alert, Button, Group, Stack, Text, TextInput } from '@mantine/core';
import { IconAlertTriangle, IconBarcode, IconCamera } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { BarcodeScanner } from './BarcodeScanner';
import { NutritionLimitations } from './NutritionLimitations';
import { lookupBarcode, nutritionErrorCode, nutritionErrorInfo } from '@/lib/nutrition-api';
import type { ExternalFoodResponse } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

type Props = {
  onFound: (food: ExternalFoodResponse) => void;
  /** Código sem produto utilizável (Open Food Facts não conhece): abre o cadastro manual com o EAN. */
  onNotFound?: (ean: string) => void;
};

// Respostas da API para "o código é válido, mas não há produto utilizável": o OFF não conhece
// (404) ou conhece com dados que não passam nas regras do catálogo (422, "cadastre manualmente").
const NOT_FOUND_CODES = ['off_not_found', 'invalid_external_food'];

/**
 * Consulta por código de barras (Lote L3b; leitor pela câmera no N3). O código vem da câmera
 * (`BarcodeScanner`: `BarcodeDetector` ou `@zxing/browser`) ou da digitação, que continua
 * disponível. A API valida o EAN (dígito verificador do motor), procura no catálogo e, se não
 * achar, no Open Food Facts, com limite diário de consultas novas. Produto desconhecido oferece
 * o cadastro manual já com o código. A tela só manda o texto e exibe o resultado ou o erro.
 */
export function BarcodeLookup({ onFound, onNotFound }: Props) {
  const [ean, setEan] = useState('');
  const [consultado, setConsultado] = useState('');
  const [scanning, setScanning] = useState(false);
  const consulta = useMutation({
    mutationFn: (codigo: string) => lookupBarcode(codigo),
    onSuccess: onFound,
  });

  function consultar(codigo: string) {
    setConsultado(codigo);
    consulta.mutate(codigo);
  }

  function enviar(event: FormEvent) {
    event.preventDefault();
    const codigo = ean.trim();
    if (codigo) consultar(codigo);
  }

  function aoLer(codigo: string) {
    setScanning(false);
    setEan(codigo);
    consultar(codigo);
  }

  const erro = consulta.isError ? nutritionErrorInfo(consulta.error, 'Não foi possível consultar o código de barras.') : null;
  const naoAchado = consulta.isError && NOT_FOUND_CODES.includes(nutritionErrorCode(consulta.error) ?? '');
  const achado = consulta.isSuccess ? consulta.data : null;

  return (
    <Stack gap="sm">
      <Text size="sm" c={BRAND.textMuted}>
        Leia o código de barras da embalagem com a câmera ou digite os números. Produto que ainda não está no catálogo
        vem do Open Food Facts (base aberta e colaborativa); essas consultas novas têm um limite diário por usuário.
      </Text>
      <Button
        variant="filled"
        color="brand"
        leftSection={<IconCamera size={18} />}
        onClick={() => setScanning(true)}
        style={{ minHeight: 44 }}
      >
        Ler com a câmera
      </Button>
      <BarcodeScanner opened={scanning} onClose={() => setScanning(false)} onDetected={aoLer} />
      <form onSubmit={enviar}>
        <Group gap="sm" align="flex-end" wrap="wrap">
          <TextInput
            label="Código de barras (EAN)"
            placeholder="Ex.: 7891000100103"
            leftSection={<IconBarcode size={16} />}
            inputMode="numeric"
            autoComplete="off"
            value={ean}
            onChange={(event) => setEan(event.currentTarget.value)}
            style={{ flex: '1 1 220px' }}
            maxLength={32}
          />
          <Button type="submit" variant="light" color="brand" disabled={ean.trim().length === 0} loading={consulta.isPending}>
            Consultar
          </Button>
        </Group>
      </form>

      {erro && !naoAchado && erro.items.length > 0 && (
        <NutritionLimitations items={erro.items} title={erro.message} tone="error" />
      )}
      {erro && !naoAchado && erro.items.length === 0 && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {erro.message}
        </Alert>
      )}
      {erro && naoAchado && (
        <Alert color="yellow" icon={<IconAlertTriangle size={16} />} title="Produto não encontrado">
          <Stack gap="xs">
            <Text size="sm">{erro.message}</Text>
            {onNotFound && (
              <Button
                variant="light"
                color="brand"
                onClick={() => onNotFound(consultado)}
                style={{ minHeight: 44, alignSelf: 'flex-start' }}
              >
                Cadastrar com o código {consultado}
              </Button>
            )}
          </Stack>
        </Alert>
      )}
      {achado && (
        <Text size="sm" c="green">
          {achado.from_cache ? 'Produto já estava no catálogo' : 'Produto trazido do Open Food Facts'}: {achado.name}.
        </Text>
      )}
    </Stack>
  );
}
