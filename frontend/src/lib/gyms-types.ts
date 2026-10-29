// Tipos e helpers da tela Academias — CRUD de academias, upload de logo e
// associação de equipamentos do catálogo global (`gym_equipment_catalog`).
// Contratos confirmados lendo C:\muscle_fit\api\app\gyms.py (não copiados do
// legado sem checar, pois o backend novo pode ter divergido).

import type {
  EquipmentRecommendAllResponse,
  EquipmentRecommendGroup,
  EquipmentRecommendItem,
} from './equipment-recommend';

export type ApiListResponse<T> = { items: T[] };

export type Gym = {
  id: number;
  owner_user_id: number | null;
  name: string;
  usual_weekdays: number[] | null;
  is_recurring: boolean;
  notes?: string | null;
  opens_at: string | null;
  closes_at: string | null;
  has_logo: boolean;
  // URL pública estática (CloudFront + S3), null quando has_logo é false —
  // ver api/app/gyms.py::_row_jsonable. Substitui o fluxo antigo de
  // GET /gyms/{id}/logo autenticado + Object URL.
  logo_url: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type Equipment = {
  id: number;
  name: string;
  notes?: string | null;
  equipment_type_id?: number | null;
  manufacturer?: string | null;
  model?: string | null;
  location?: string | null;
  // Segurança (migração 032; PR-07). Ausentes/null = não cadastrado: o RPE
  // máximo seguro desconhecido é tratado como 9 (ver lib/training-safety.ts).
  max_safe_rpe?: number | null;
  load_increment_kg?: number | null; // menor incremento de carga TOTAL
  safety_notes?: string | null;
  has_photo: boolean;
  // URL pública estática (CloudFront + S3), null quando has_photo é false —
  // ver api/app/gyms.py::_row_jsonable. Substitui o fluxo antigo de
  // GET /equipment/{id}/photo autenticado + Object URL.
  photo_url: string | null;
  is_active: boolean;
  updated_at: string;
};

export type GymEquipmentCatalogLink = {
  id: number;
  gym_id: number;
  gym_name: string;
  equipment_id: number;
  equipment_name: string;
  has_photo: boolean;
  photo_url: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

// Item/grupo/resposta de `GET /gym-equipment/recommend-all`: tipo ÚNICO em
// `equipment-recommend.ts` (PR-08, G20) — os nomes abaixo são aliases mantidos
// para os consumidores existentes.
export type RecommendItem = EquipmentRecommendItem;
export type RecommendGroup = EquipmentRecommendGroup;
export type RecommendAllResponse = EquipmentRecommendAllResponse;

export const WEEKDAYS = [
  { value: 1, label: 'Segunda' },
  { value: 2, label: 'Terça' },
  { value: 3, label: 'Quarta' },
  { value: 4, label: 'Quinta' },
  { value: 5, label: 'Sexta' },
  { value: 6, label: 'Sábado' },
  { value: 7, label: 'Domingo' },
] as const;

export function weekdayLabels(days: number[] | null): string {
  if (!days || !days.length) return 'Sem frequência padrão definida';
  return days
    .slice()
    .sort((a, b) => a - b)
    .map((d) => WEEKDAYS.find((w) => w.value === d)?.label || String(d))
    .join(', ');
}

// Converte "HH:MM:SS" (ou "HH:MM") vindo do backend para o formato aceito
// por <input type="time">, que é sempre "HH:MM".
export function toTimeInputValue(value: string | null | undefined): string {
  if (!value) return '';
  return value.slice(0, 5);
}
