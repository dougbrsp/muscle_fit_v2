// Zonas de FC do usuário (M98 C1) — prontas do backend, `GET /users/me/hr-zones`
// (dono `api/app/domain/hr_zones.py`: precedência Intervals → FCmáx manual →
// sem zonas, e a faixa de bpm de cada zona). Aqui só se busca e se formata:
// nenhum percentual nem limite de zona mora no frontend.

import { useQuery } from '@tanstack/react-query';

import { apiFetch } from './api';
import { HR_ZONE_OPTIONS } from './cardio-prescription';

export type HrZoneSource = 'intervals' | 'manual_max_hr' | 'none';

export type HrZoneRange = {
  zone: number;
  label: string;
  lower_bpm: number;
  upper_bpm: number;
};

export type AthleteHrZones = {
  source: HrZoneSource;
  max_hr_bpm: number | null;
  zones: HrZoneRange[];
};

export const HR_ZONES_QUERY_KEY = ['my-hr-zones'] as const;

/** Zonas do usuário logado. Falha da chamada = sem zonas (o seletor mostra só Z1–Z5). */
export function useMyHrZones() {
  return useQuery({
    queryKey: HR_ZONES_QUERY_KEY,
    queryFn: () => apiFetch<AthleteHrZones>('/users/me/hr-zones'),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

export type HrZonesSubjectKind = 'self' | 'assignee' | 'none';

/** De quem são as zonas do editor do Modelo — decidido pelo backend
 * (`api/app/athlete_hr_zones.py`, regra de autorização por papel). */
export type HrZonesSubject = {
  kind: HrZonesSubjectKind;
  user_id: number | null;
  name: string | null;
  reason: 'no_template' | 'no_assignee' | 'multiple_assignees' | null;
};

export type TemplateHrZones = AthleteHrZones & { subject: HrZonesSubject };

/** Caminho das zonas do editor do Modelo. `templateId` null = Modelo novo. */
export function templateHrZonesPath(templateId: number | null): string {
  return templateId === null
    ? '/workout-plan-templates/hr-zones'
    : `/workout-plan-templates/hr-zones?template_id=${templateId}`;
}

/**
 * Zonas que o editor do Modelo mostra. O backend escolhe de quem são (admin:
 * as próprias; aluno: as próprias; personal trainer: as do aluno atribuído ao
 * Modelo). A chave começa com `HR_ZONES_QUERY_KEY`: salvar a FCmáx no perfil
 * invalida as duas.
 */
export function useTemplateHrZones(templateId: number | null, enabled = true) {
  return useQuery({
    queryKey: [...HR_ZONES_QUERY_KEY, 'template', templateId] as const,
    queryFn: () => apiFetch<TemplateHrZones>(templateHrZonesPath(templateId)),
    enabled,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

/** Aviso do editor quando não há faixa de bpm para mostrar (só texto). */
export function hrZonesHint(data: TemplateHrZones | undefined): string | null {
  if (!data) return null;
  const { subject } = data;
  if (subject.kind === 'none') {
    if (subject.reason === 'multiple_assignees') {
      return 'Modelo atribuído a mais de um aluno: as faixas de bpm das zonas não são exibidas.';
    }
    return 'Atribua o Modelo a um aluno para ver as faixas de bpm das zonas dele.';
  }
  if (data.zones.length > 0) {
    return subject.kind === 'assignee' && subject.name ? `Zonas de ${subject.name}.` : null;
  }
  if (subject.kind === 'assignee') {
    return `${subject.name ?? 'O aluno'} ainda não tem zonas de FC (FCmáx no perfil ou Intervals.icu).`;
  }
  return 'Informe sua FCmáx no Perfil para ver a faixa de bpm de cada zona.';
}

/** Opções do seletor de zona: "Z2 · 115–133 bpm" quando há zonas; senão só "Z2". */
export function hrZoneSelectData(zones: ReadonlyArray<HrZoneRange> | null | undefined) {
  const byZone = new Map((zones ?? []).map((z) => [z.zone, z]));
  return HR_ZONE_OPTIONS.map(({ zone, label }) => {
    const range = byZone.get(zone);
    return {
      value: String(zone),
      label: range ? `${label} · ${range.lower_bpm}–${range.upper_bpm} bpm` : label,
    };
  });
}
