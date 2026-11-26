'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  NumberInput,
  Paper,
  Select,
  Stack,
  Text,
  TextInput,
  Textarea,
  Title,
  VisuallyHidden,
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { IconAlertTriangle, IconPhoto, IconSparkles, IconTrash } from '@tabler/icons-react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type MutableRefObject } from 'react';
import { FavoriteShortcuts } from './FavoriteShortcuts';
import { FoodRegistrationFlow } from './FoodRegistrationFlow';
import { FoodSearch } from './FoodSearch';
import { MealItemsTable, TotalsBadges, TotalsLine } from './MealCard';
import { QuickEntryItemRow } from './QuickEntryItemRow';
import { useScannerOpen } from '@/lib/barcode-scanner-open';
import {
  MEAL_ITEMS_LIMIT,
  MEAL_ITEM_LOW_CONFIDENCE_NOTICE,
  MEAL_PHOTO_EVIDENCE_OPTIONS,
  MEAL_PHOTO_UNCERTAINTY_OPTIONS,
  MEAL_SLOT_OPTIONS,
  MEAL_PHOTO,
  UNIT_OPTIONS,
  nutritionLabel,
  nutritionNotice,
} from '@/lib/nutrition-constants';
import {
  createMeal,
  fetchFood,
  fetchLastMealTime,
  foodKeys,
  lastMealTimeKey,
  nutritionErrorInfo,
  previewMeal,
  updateMeal,
} from '@/lib/nutrition-api';
import { editorItemFromFavorite } from '@/lib/nutrition-favorites';
import { addRegisteredFood } from '@/lib/nutrition-food-registration';
import { invalidateFoodQueries } from '@/lib/nutrition-food-queries';
import {
  chooseCandidate,
  chooseEstimate,
  editorFormFromMeal,
  editorItemFromFood,
  editorItemQuick,
  itemHasSource,
  mealCreatePayload,
  mealPreviewPayload,
  mealPayload,
  photoMetaView,
  quantidade,
  unitUsesPortion,
  type MealEditorForm,
  type MealEditorItem,
} from '@/lib/nutrition-meal-form';
import type { Meal, MealPreviewPayload, NutritionLimitation } from '@/lib/nutrition-types';
import { usePresentationActive } from '@/lib/presentation-slots';
import { BackToCloseHere } from '@/mobile/screens/telas/BackToCloseHere';
import { BottomSheet } from '@/mobile/ui/Sheets';
import { BRAND } from '@/theme';

const ATRASO_DO_PREVIEW_MS = 400;

type Props = {
  date: string;
  /** Refeição em edição ou `null` (nova). */
  meal: Meal | null;
  /** Formulário inicial de uma refeição nova (leitura por IA, L5b). */
  initialForm?: MealEditorForm | null;
  onSaved: (meal: Meal) => void;
  onCancel: () => void;
};

function PortionSelect({
  item,
  foodId,
  onChange,
}: {
  item: MealEditorItem;
  foodId: number;
  onChange: (portionId: number | null) => void;
}) {
  const detalhe = useQuery({
    queryKey: foodKeys.detail(foodId),
    queryFn: () => fetchFood(foodId),
    retry: false,
    staleTime: 60_000,
  });
  const opcoes = (detalhe.data?.portions ?? []).map((porcao) => ({
    value: String(porcao.id),
    label: `${porcao.label} (${porcao.amount} ${detalhe.data?.basis ?? item.basis})`,
  }));
  return (
    <Select
      label="Porção"
      placeholder="Escolha uma porção"
      data={opcoes}
      value={item.portion_id != null ? String(item.portion_id) : null}
      onChange={(valor) => onChange(valor ? Number(valor) : null)}
      clearable
      w={220}
      nothingFoundMessage="Alimento sem porção cadastrada"
    />
  );
}

const ESTIMATIVA = 'estimativa-da-ia';

/** Origem do item lido por IA: um dos candidatos do catálogo ou a estimativa da IA. */
function SourceSelect({ item, onChange }: { item: MealEditorItem; onChange: (item: MealEditorItem) => void }) {
  const opcoes = [
    ...item.choices.map((candidato) => ({
      value: String(candidato.food_id),
      label: candidato.brand ? `${candidato.name} (${candidato.brand})` : candidato.name,
    })),
    ...(item.estimate ? [{ value: ESTIMATIVA, label: `Estimativa da IA: ${item.estimate.name}` }] : []),
  ];
  const valor = item.food_id != null ? String(item.food_id) : item.usesEstimate && item.estimate ? ESTIMATIVA : null;
  return (
    <Select
      label="Alimento"
      placeholder="Escolha o alimento"
      data={opcoes}
      value={valor}
      onChange={(novo) => {
        if (novo === ESTIMATIVA) onChange(chooseEstimate(item));
        const candidato = item.choices.find((c) => String(c.food_id) === novo);
        if (candidato) onChange(chooseCandidate(item, candidato));
      }}
      allowDeselect={false}
      error={valor === null ? 'Escolha o alimento' : undefined}
      style={{ flex: '1 1 260px' }}
    />
  );
}

type Pendentes = MutableRefObject<Map<string, number | string>>;

function ItemRow({
  item,
  pendentes,
  sincronizar,
  onChange,
  onRemove,
  tentativasInvalidas,
}: {
  item: MealEditorItem;
  pendentes: Pendentes;
  /** Avisa o pai de que o mapa de pendentes mudou (booleano: só re-renderiza quando vira/desvira). */
  sincronizar: () => void;
  onChange: (item: MealEditorItem) => void;
  onRemove: () => void;
  /** Conta os Salvar barrados por quantidade inválida; a cada aumento, o campo inválido mostra o erro. */
  tentativasInvalidas: number;
}) {
  // Texto em edição da quantidade: só vai ao `form` (e ao preview) no blur/Enter; o ref deixa o Salvar ler o valor ainda em foco.
  const [rascunho, setRascunho] = useState<number | string | null>(null);
  const [erroQuantidade, setErroQuantidade] = useState(false);
  useEffect(() => {
    const valor = pendentes.current.get(item.key);
    if (tentativasInvalidas > 0 && valor !== undefined && quantidade(valor) === null) setErroQuantidade(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tentativasInvalidas]);
  function confirmarQuantidade() {
    const valor = pendentes.current.get(item.key);
    if (valor === undefined) return;
    if (quantidade(valor) === null) {
      setErroQuantidade(true);
      return;
    }
    pendentes.current.delete(item.key);
    sincronizar();
    setRascunho(null);
    onChange({ ...item, quantity: valor });
  }
  if (item.quick) return <QuickEntryItemRow item={item} draft={item.quick} onChange={onChange} onRemove={onRemove} />;
  const meta = photoMetaView(item);
  const daLeitura = item.choices.length > 0 || (item.id === null && item.estimate !== null);
  // Item estimado não tem porção: só g, ml e medidas caseiras (a API confere de novo).
  const unidades = item.food_id == null ? UNIT_OPTIONS.filter((opcao) => !unitUsesPortion(opcao.value)) : UNIT_OPTIONS;
  return (
    <Stack gap={2}>
      <Group gap="xs" align="flex-end" wrap="wrap">
        {daLeitura ? (
          <SourceSelect
            item={item}
            onChange={(novo) => {
              // A troca redefine a quantidade: o texto em edição (inclusive inválido) perde a vez.
              pendentes.current.delete(item.key);
              sincronizar();
              setRascunho(null);
              setErroQuantidade(false);
              onChange(novo);
            }}
          />
        ) : (
          <Text size="sm" fw={600} c={BRAND.textPrimary} style={{ flex: '1 1 200px' }}>
            {item.label}
          </Text>
        )}
        {item.originLabel && (
          <Badge
            size="xs"
            variant="light"
            color={item.usesEstimate ? 'yellow' : 'brand'}
            leftSection={<IconSparkles size={10} />}
            mb={8}
          >
            {item.originLabel}
          </Badge>
        )}
        {item.lowConfidence && (
          <Badge size="xs" variant="light" color="yellow" leftSection={<IconAlertTriangle size={10} />} mb={8} data-testid="item-baixa-confianca">
            {nutritionNotice(MEAL_ITEM_LOW_CONFIDENCE_NOTICE)}
          </Badge>
        )}
        <NumberInput
          label="Quantidade"
          value={rascunho ?? item.quantity}
          onChange={(valor) => {
            pendentes.current.set(item.key, valor);
            sincronizar();
            setRascunho(valor);
            setErroQuantidade(false);
          }}
          onBlur={confirmarQuantidade}
          onKeyDown={(e) => {
            if (e.key === 'Enter') confirmarQuantidade();
          }}
          enterKeyHint="done"
          error={erroQuantidade ? 'Informe a quantidade' : undefined}
          min={0}
          decimalScale={3}
          w={120}
        />
        <Select
          label="Unidade"
          data={[...unidades]}
          value={item.unit}
          onChange={(valor) => valor && onChange({ ...item, unit: valor, portion_id: unitUsesPortion(valor) ? item.portion_id : null })}
          allowDeselect={false}
          w={190}
        />
        {unitUsesPortion(item.unit) && item.food_id != null && (
          <PortionSelect
            item={item}
            foodId={item.food_id}
            onChange={(portionId) => onChange({ ...item, portion_id: portionId })}
          />
        )}
        <ActionIcon variant="subtle" color="red" aria-label={`Remover ${item.label}`} onClick={onRemove} mb={4}>
          <IconTrash size={16} />
        </ActionIcon>
      </Group>
      {meta && (
        <Group gap="xs" data-testid="item-foto-meta" aria-label={`Leitura da foto: ${item.label}`}>
          {meta.evidence && (
            <Badge size="xs" variant="outline" color="gray" data-testid="item-evidencia">
              {nutritionLabel(MEAL_PHOTO_EVIDENCE_OPTIONS, meta.evidence)}
            </Badge>
          )}
          {meta.gramsLow != null && meta.gramsHigh != null && (
            <Text size="xs" c={BRAND.textMuted} data-testid="item-faixa">
              {meta.gramsLow}–{meta.gramsHigh} g
            </Text>
          )}
          {meta.uncertainty && meta.uncertainty !== MEAL_PHOTO.uncertainty_none && (
            <Text size="xs" c={BRAND.textMuted} data-testid="item-incerteza">
              {nutritionLabel(MEAL_PHOTO_UNCERTAINTY_OPTIONS, meta.uncertainty)}
            </Text>
          )}
        </Group>
      )}
      {item.notices.length > 0 && (
        <Text size="xs" c={BRAND.textMuted}>
          {item.notices.map((aviso) => aviso.label).join(' ')}
        </Text>
      )}
    </Stack>
  );
}

/**
 * Editor de refeição: busca no catálogo (`FoodSearch`), quantidade, unidade e
 * porção; o preview é uma chamada à API (motor `meal_math`, E9) e a gravação
 * congela os valores por item (E4). A tela não calcula nada.
 */
export function MealEditor({ date, meal, initialForm = null, onSaved, onCancel }: Props) {
  const [form, setForm] = useState<MealEditorForm>(() =>
    meal
      ? editorFormFromMeal(meal)
      : (initialForm ?? { local_date: date, meal_slot: null, eaten_at: '', notes: '', items: [], ai: null }),
  );
  const proximaChave = useRef(0);
  const pendentes: Pendentes = useRef(new Map());
  // Refeição nova (sem edição nem leitura por IA) abre com a ÚLTIMA hora registrada pela pessoa, quando a API
  // responde. A hora digitada (mesmo apagada) nunca é sobrescrita; trocar o Dia não mexe na hora.
  const horaTocada = useRef(false);
  const querHoraAnterior = meal === null && initialForm === null;
  // Cadastro de alimento sem sair do registro: o rascunho (`form`) mora AQUI, fora do modal/folha, que só
  // desmonta o fluxo ao fechar. Fechar sem salvar não muda nada; salvar acrescenta o item.
  const [cadastro, setCadastro] = useState<{ nome: string } | null>(null);
  // Avisos do cadastro (Atwater, Open Food Facts) e erro de "usar o existente" ficam na tela depois de o fluxo fechar.
  const [avisosCadastro, setAvisosCadastro] = useState<NutritionLimitation[]>([]);
  const [erroExistente, setErroExistente] = useState<string | null>(null);
  // Entrega (de uso único) do alimento salvo que está na etapa final do cadastro; nula fora dela.
  const entregaPendente = useRef<(() => void) | null>(null);
  // Estrela marcada com a refeição no teto de itens: favoritou, mas não coube na lista.
  const [avisoTeto, setAvisoTeto] = useState<string | null>(null);
  const noCelular = usePresentationActive();
  // Esc com o leitor de código aberto fecha só o leitor, não o cadastro por baixo.
  const leitorAberto = useScannerOpen();
  const queryClient = useQueryClient();
  // Rever a foto da análise: object URL criado aqui e revogado ao fechar o editor (salvar ou cancelar desmontam).
  const fotos = form.photos ?? [];
  const [fotoUrls, setFotoUrls] = useState<string[]>([]);
  const [verFoto, setVerFoto] = useState(false);
  useEffect(() => {
    const urls = fotos.map((f) => URL.createObjectURL(f));
    setFotoUrls(urls);
    return () => urls.forEach((u) => URL.revokeObjectURL(u));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.photos]);

  /** Payload de gravação sobre o form com as quantidades ainda em foco (blur/clique fora de ordem) já aplicadas. */
  function formEfetivo(): MealEditorForm {
    return {
      ...form,
      items: form.items.map((i) => {
        const v = pendentes.current.get(i.key);
        return v !== undefined && quantidade(v) !== null ? { ...i, quantity: v } : i;
      }),
    };
  }
  function montarCorpo() {
    const efetivo = formEfetivo();
    return meal ? mealPayload(efetivo) : mealCreatePayload(efetivo);
  }
  const corpoPreview: MealPreviewPayload | null = mealPreviewPayload(form, meal?.id);
  const [previewDebounced] = useDebouncedValue(corpoPreview ? JSON.stringify(corpoPreview) : null, ATRASO_DO_PREVIEW_MS);
  const preview = useQuery({
    queryKey: ['nutrition-meal-preview', previewDebounced],
    queryFn: () => previewMeal(JSON.parse(previewDebounced as string) as MealPreviewPayload),
    enabled: previewDebounced !== null,
    retry: false,
    placeholderData: keepPreviousData, // o bloco "Calculado pelo motor" não some durante o recálculo
  });

  const ultimaHora = useQuery({
    queryKey: lastMealTimeKey,
    queryFn: fetchLastMealTime,
    enabled: querHoraAnterior,
    retry: false, // falhou: o campo fica vazio, sem erro na tela
    staleTime: 0,
  });
  // Só vale a resposta buscada DEPOIS de montar: o valor em cache pode ser de antes da última gravação.
  const horaAnterior = ultimaHora.isFetchedAfterMount && !ultimaHora.isError ? (ultimaHora.data?.time ?? null) : null;
  useEffect(() => {
    if (!querHoraAnterior || horaAnterior === null || horaTocada.current) return;
    setForm((atual) => (atual.eaten_at === '' ? { ...atual, eaten_at: horaAnterior } : atual));
  }, [querHoraAnterior, horaAnterior]);

  const salvar = useMutation({
    mutationFn: () => {
      const corpoSalvar = montarCorpo();
      if (!corpoSalvar) throw new Error('Refeição incompleta.');
      return meal ? updateMeal(meal.id, corpoSalvar) : createMeal(corpoSalvar);
    },
    onSuccess: (salva) => {
      void queryClient.invalidateQueries({ queryKey: lastMealTimeKey });
      onSaved(salva);
    },
  });

  // Quantidade inválida ainda em foco: o erro aparece no campo e nada é gravado.
  const [tentativasInvalidas, setTentativasInvalidas] = useState(0);
  const [temPendente, setTemPendente] = useState(false);
  const [mostrarPendencias, setMostrarPendencias] = useState(false);
  // Só dados exatos (form): com pendente o botão fica habilitado e o tentarSalvar decide.
  const corpoForm = meal ? mealPayload(form) : mealCreatePayload(form);
  const sincronizar = () => setTemPendente(pendentes.current.size > 0);
  function tentarSalvar() {
    const chaves = new Set(form.items.map((i) => i.key));
    if (Array.from(pendentes.current).some(([k, v]) => chaves.has(k) && quantidade(v) === null)) {
      setTentativasInvalidas((n) => n + 1);
      return;
    }
    // Incompleta por outro motivo: os avisos da tela já dizem o que falta.
    if (montarCorpo() === null) {
      setMostrarPendencias(true);
      return;
    }
    salvar.mutate();
  }

  function adicionarCadastrado(alimento: { id: number; name: string; basis: string; warnings?: NutritionLimitation[] }) {
    setAvisosCadastro(alimento.warnings ?? []);
    setErroExistente(null);
    proximaChave.current += 1;
    const chave = `novo-${proximaChave.current}`;
    setForm((atual) => addRegisteredFood(atual, alimento, chave, MEAL_ITEMS_LIMIT));
    setCadastro(null);
    void invalidateFoodQueries(queryClient);
  }

  /** EAN já cadastrado (409): busca o alimento existente e o adiciona. */
  async function usarExistente(foodId: number) {
    try {
      adicionarCadastrado(await queryClient.fetchQuery({ queryKey: foodKeys.detail(foodId), queryFn: () => fetchFood(foodId) }));
    } catch (erro) {
      // Sem o alimento não há o que adicionar: o fluxo segue aberto, agora com o motivo.
      setErroExistente(nutritionErrorInfo(erro, 'Não foi possível abrir o alimento existente.').message);
    }
  }

  function atualizarItem(chave: string, item: MealEditorItem) {
    setForm((atual) => ({ ...atual, items: atual.items.map((existente) => (existente.key === chave ? item : existente)) }));
  }

  const erroPreview = preview.isError ? nutritionErrorInfo(preview.error, 'Não foi possível calcular a refeição.') : null;
  const erroSalvar = salvar.isError ? nutritionErrorInfo(salvar.error, 'Não foi possível salvar a refeição.') : null;
  const semOrigem = form.items.some((item) => !itemHasSource(item));
  const faltaQuantidade = form.items.length > 0 && mealPreviewPayload(formEfetivo(), meal?.id) === null && !semOrigem && (!temPendente || mostrarPendencias);

  function fecharCadastro() {
    // Fechou (X, Esc, voltar) na etapa final: o alimento já foi salvo, então entra na refeição como em "Concluir",
    // só sem favoritar nem promover.
    const entregar = entregaPendente.current;
    entregaPendente.current = null;
    entregar?.();
    setCadastro(null);
    setErroExistente(null);
  }

  const fluxoDeCadastro = (
    <Stack gap="sm">
      {erroExistente && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
          {erroExistente}
        </Alert>
      )}
      <FoodRegistrationFlow
        initialName={cadastro?.nome}
        onSaved={adicionarCadastrado}
        onFound={adicionarCadastrado}
        onUseExisting={usarExistente}
        onPendingDelivery={(entregar) => {
          entregaPendente.current = entregar;
        }}
        onCancel={fecharCadastro}
      />
    </Stack>
  );

  return (
    <Paper withBorder radius="md" p="md" bg={BRAND.bgElevated}>
      {/* Voltar do celular fecha só a folha (o editor e o rascunho continuam); montado sempre, para fechar por código desfazer a entrada do histórico. */}
      {noCelular ? <BackToCloseHere open={cadastro !== null} onClose={fecharCadastro} /> : null}
      {noCelular ? (
        <BottomSheet
          opened={cadastro !== null}
          onClose={fecharCadastro}
          title="Cadastrar novo alimento"
          closeOnClickOutside={false}
          closeOnEscape={!leitorAberto}
        >
          {cadastro !== null ? fluxoDeCadastro : null}
        </BottomSheet>
      ) : (
        <Modal
          opened={cadastro !== null}
          onClose={fecharCadastro}
          title="Cadastrar novo alimento"
          size="lg"
          centered
          closeOnClickOutside={false}
          closeOnEscape={!leitorAberto}
        >
          {cadastro !== null ? fluxoDeCadastro : null}
        </Modal>
      )}
      <Stack gap="sm">
        <Title order={4} c={BRAND.textPrimary}>
          {meal ? 'Editar refeição' : form.ai ? 'Refeição lida pela IA' : 'Nova refeição'}
        </Title>
        {form.ai && (
          <Alert color="brand" variant="light" icon={<IconSparkles size={16} />}>
            <Text size="sm">Confira os alimentos e as quantidades antes de salvar.</Text>
            {form.ai.notes && <Text size="sm">{form.ai.notes}</Text>}
            {form.ai.mainUncertainty && form.ai.mainUncertainty !== MEAL_PHOTO.uncertainty_none && (
              <Text size="sm" data-testid="principal-incerteza">
                Principal incerteza: {nutritionLabel(MEAL_PHOTO_UNCERTAINTY_OPTIONS, form.ai.mainUncertainty)}
              </Text>
            )}
            {form.ai.limitations.map((aviso) => (
              <Text key={aviso.code} size="sm">
                {aviso.label}
              </Text>
            ))}
          </Alert>
        )}
        {fotoUrls.length > 0 && (
          <Stack gap="xs">
            <Group>
              <Button
                variant="light"
                color="brand"
                size="xs"
                leftSection={<IconPhoto size={14} />}
                aria-expanded={verFoto}
                onClick={() => setVerFoto((aberta) => !aberta)}
              >
                {verFoto ? (fotoUrls.length > 1 ? 'Ocultar fotos' : 'Ocultar foto') : fotoUrls.length > 1 ? 'Ver fotos' : 'Ver foto'}
              </Button>
            </Group>
            {verFoto &&
              fotoUrls.map((url, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  key={url}
                  src={url}
                  alt={fotoUrls.length > 1 ? `Foto ${i + 1} de ${fotoUrls.length} do prato enviada para análise` : 'Foto do prato enviada para análise'}
                  style={{ maxWidth: '100%', maxHeight: 360, objectFit: 'contain', borderRadius: 8 }}
                />
              ))}
          </Stack>
        )}
        <Group gap="sm" align="flex-end" wrap="wrap">
          <Select
            label="Refeição"
            placeholder="Escolha"
            data={[...MEAL_SLOT_OPTIONS]}
            value={form.meal_slot}
            onChange={(valor) => setForm((atual) => ({ ...atual, meal_slot: valor }))}
            w={220}
          />
          <TextInput
            type="time"
            label="Hora (opcional)"
            value={form.eaten_at}
            onChange={(e) => {
              const valor = e.currentTarget.value;
              horaTocada.current = true;
              setForm((atual) => ({ ...atual, eaten_at: valor }));
            }}
            w={140}
          />
          <TextInput
            type="date"
            label="Dia"
            value={form.local_date}
            onChange={(e) => {
              const valor = e.currentTarget.value;
              if (!valor) return;
              setForm((atual) => ({ ...atual, local_date: valor }));
            }}
            w={170}
          />
        </Group>

        <FavoriteShortcuts
          disabled={form.items.length >= MEAL_ITEMS_LIMIT}
          onPick={(favorito) => {
            proximaChave.current += 1;
            const novo = editorItemFromFavorite(favorito, `novo-${proximaChave.current}`);
            setForm((atual) => ({ ...atual, items: [...atual.items, novo] }));
          }}
        />

        <FoodSearch
          isAdmin={false}
          selectedId={null}
          onRegister={(termo) => setCadastro({ nome: termo })}
          registerDisabled={form.items.length >= MEAL_ITEMS_LIMIT}
          onQuickEntry={() => {
            proximaChave.current += 1;
            const novo = editorItemQuick(`novo-${proximaChave.current}`);
            setForm((atual) => ({ ...atual, items: [...atual.items, novo] }));
          }}
          onFavorited={(alimento) => {
            // Estrela marcada na busca: o alimento já é favorito e entra na refeição com a porção padrão.
            // Já está na lista (selecionado antes): não duplica.
            if (form.items.some((i) => i.food_id === alimento.id)) return;
            if (form.items.length >= MEAL_ITEMS_LIMIT) {
              setAvisoTeto(alimento.name);
              return;
            }
            setAvisoTeto(null);
            proximaChave.current += 1;
            const novo = editorItemFromFood(alimento, `novo-${proximaChave.current}`);
            setForm((atual) =>
              atual.items.some((i) => i.food_id === alimento.id) ? atual : { ...atual, items: [...atual.items, novo] },
            );
          }}
          onSelect={(alimento) => {
            if (form.items.length >= MEAL_ITEMS_LIMIT) return;
            proximaChave.current += 1;
            const novo = editorItemFromFood(alimento, `novo-${proximaChave.current}`);
            setForm((atual) => ({ ...atual, items: [...atual.items, novo] }));
          }}
        />

        {avisoTeto && (
          <Alert
            color="yellow"
            variant="light"
            icon={<IconAlertTriangle size={16} />}
            withCloseButton
            closeButtonLabel="Dispensar aviso"
            onClose={() => setAvisoTeto(null)}
          >
            <Text size="sm">
              {`${avisoTeto} foi adicionado aos preferidos, mas a refeição já tem o máximo de ${MEAL_ITEMS_LIMIT} itens.`}
            </Text>
          </Alert>
        )}

        {avisosCadastro.length > 0 && (
          <Alert
            color="yellow"
            variant="light"
            icon={<IconAlertTriangle size={16} />}
            title="Confira o alimento cadastrado"
            withCloseButton
            closeButtonLabel="Dispensar avisos"
            onClose={() => setAvisosCadastro([])}
          >
            {avisosCadastro.map((aviso) => (
              <Text key={aviso.code} size="sm">
                {aviso.label}
              </Text>
            ))}
          </Alert>
        )}

        <Stack gap="xs">
          {form.items.length === 0 && (
            <Text size="sm" c="dimmed">
              Busque e clique num alimento para adicioná-lo à refeição.
            </Text>
          )}
          {form.items.map((item) => (
            <ItemRow
              key={item.key}
              item={item}
              pendentes={pendentes}
              sincronizar={sincronizar}
              tentativasInvalidas={tentativasInvalidas}
              onChange={(novo) => atualizarItem(item.key, novo)}
              onRemove={() => {
                pendentes.current.delete(item.key);
                sincronizar();
                setForm((atual) => ({ ...atual, items: atual.items.filter((i) => i.key !== item.key) }));
              }}
            />
          ))}
        </Stack>

        <Textarea
          label="Anotação"
          value={form.notes}
          onChange={(e) => {
            const valor = e.currentTarget.value;
            setForm((atual) => ({ ...atual, notes: valor }));
          }}
          maxLength={1000}
          autosize
          minRows={1}
        />

        {semOrigem && (
          <Text size="sm" c="yellow">
            Escolha o alimento de cada item marcado (ou a estimativa da IA, quando houver).
          </Text>
        )}
        {form.meal_slot === null && (corpoPreview !== null || mostrarPendencias) && (
          <Text size="sm" c="yellow">
            Escolha a refeição para salvar. O cálculo abaixo não depende dela.
          </Text>
        )}
        {faltaQuantidade && (
          <Text size="sm" c="yellow">
            Informe a quantidade de cada alimento para calcular.
          </Text>
        )}
        {erroPreview && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {erroPreview.message}
            {erroPreview.items.map((item) => (
              <Text key={item.code} size="sm">
                {item.label}
              </Text>
            ))}
          </Alert>
        )}
        {preview.data && corpoPreview && (
          <Stack gap="xs" aria-busy={preview.isPlaceholderData} style={preview.isPlaceholderData ? { opacity: 0.7 } : undefined}>
            {preview.isPlaceholderData && <VisuallyHidden role="status">Recalculando…</VisuallyHidden>}
            <Group gap="xs">
              <Text size="sm" fw={600} c={BRAND.textPrimary}>
                Calculado pelo motor
              </Text>
              <TotalsBadges totals={preview.data.totals} items={preview.data.items} />
            </Group>
            <MealItemsTable items={preview.data.items} />
            <TotalsLine totals={preview.data.totals} />
          </Stack>
        )}
        {erroSalvar && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {erroSalvar.message}
            {erroSalvar.items.map((item) => (
              <Text key={item.code} size="sm">
                {item.label}
              </Text>
            ))}
          </Alert>
        )}
        <Group>
          <Button color="brand" onClick={tentarSalvar} loading={salvar.isPending} disabled={!temPendente && corpoForm === null}>
            Salvar refeição
          </Button>
          <Button variant="subtle" color="gray" onClick={onCancel}>
            Cancelar
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}
