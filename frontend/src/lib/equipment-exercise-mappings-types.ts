// Tipos para a tela "Equipamentos x Exercícios" (Fase 4, Etapa 17 da
// migração). Contrato confirmado lendo diretamente
// `C:\muscle_fit\api\app\gyms.py` e `muscle_group_equipment.py`.
//
// Associação GLOBAL (sem academia) entre equipamento do catálogo e exercício
// canônico — "este equipamento serve este exercício?". Não confundir com:
// - `gym_equipment_catalog` (tela /academias): academia -> equipamento, sem exercício.
// - `gym_equipment`: cache materializado, recalculado automaticamente a partir
//   do cruzamento desta tabela com `gym_equipment_catalog` — nunca editado
//   diretamente pela UI.
//
// Leitura (`GET`) exige apenas usuário autenticado (`_require_identity`).
// Escrita (`POST`/`DELETE`) exige admin (`_require_admin_identity`).

export type ApiListResponse<T> = { items: T[] };

// --- GET /muscle-groups/{muscle_group_id}/equipment -------------------------
// Confirmado em `api/app/muscle_group_equipment.py`: lê a view
// `vw_muscle_group_equipment_exercises` (modelo NOVO: `equipment_exercises` x
// `exercise_muscle_contributions` x `exercise_catalog`), filtrando
// `role = 'PRIMARY'` e agrupando por equipamento. Divergência relevante para a
// Desde o PR-05 a tela escreve direto em `equipment_exercises` (endpoints
// canônicos, ver `equipment-exercises-types.ts`), a mesma tabela que esta view
// lê — o badge "já associado" reflete a associação após invalidar a query.
// Os tipos `EquipmentExerciseMapping*` e as rotas `/equipment-exercise-mappings`
// saíram no COD-05 (2026-10-09).
export type MuscleGroupEquipmentItem = {
  equipment_id: number;
  equipment_name: string;
  exercise_count: number;
  max_stimulus: number | null;
};

export type MuscleGroupEquipmentResponse = {
  muscle_group_id: number;
  items: MuscleGroupEquipmentItem[];
};

// --- Catálogo de equipamentos (POST/PUT /equipment, admin-only) -------------
// Contrato confirmado lendo C:\muscle_fit\api\app\gyms.py (linhas ~444-639).
// `POST /equipment` faz `ON DUPLICATE KEY UPDATE` por `name` (UNIQUE) — criar
// com um nome já existente reativa/atualiza em vez de duplicar ou dar erro.
export type EquipmentCreateOrUpdateResponse = {
  status: 'ok';
  item: {
    id: number;
    name: string;
    notes: string | null;
    equipment_type_id: number | null;
    manufacturer: string | null;
    model: string | null;
    location: string | null;
    has_photo: boolean;
    photo_url: string | null;
    is_active: boolean;
    updated_at: string;
  };
};

// --- DELETE /equipment/{equipment_id} (admin-only) --------------------------
// Soft delete (`is_active = 0`), mesmo padrão de `/gyms/{id}`.
export type EquipmentDeleteResponse = {
  status: 'ok';
  id: number;
  is_active: false;
};

// --- DELETE /equipment/{equipment_id}/photo (admin-only) --------------------
export type EquipmentPhotoDeleteResponse = {
  status: 'ok';
  id: number;
  has_photo: false;
};
