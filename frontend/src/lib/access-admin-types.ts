// Contrato da API /access (RBAC por grupos, ADR 0011). Só transporte: quem
// decide permissão é o backend; a tela reflete o catálogo.

export type AccessAction = string;
export type AccessScope = string;

export type AccessResource = {
  key: string;
  label: string;
  domain: string;
  grantable: boolean;
  warning: string | null;
};

export type AccessCatalog = {
  resources: AccessResource[];
  actions: AccessAction[];
  scopes: AccessScope[];
  scope_warnings: Record<string, string | null | undefined>;
};

export type AccessGrant = { resource: string; action: AccessAction; scope: AccessScope };

export type AccessGroup = {
  id: number;
  group_key: string;
  name: string;
  description: string | null;
  is_system: boolean;
  editable: boolean;
  member_count: number;
  grants: AccessGrant[];
};

export type CreateGroupPayload = { group_key: string; name: string; description?: string | null };
export type UpdateGroupPayload = { name: string; description?: string | null };

export type AccessMember = {
  user_id: number;
  username: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
  created_at: string | null;
  created_by_user_id: number | null;
};

export type AddMemberResponse = { added?: boolean; already_member?: boolean };

export type TrainerLinkStatus = 'ativo' | 'encerrado';

/** Vínculo treinador–aluno (lote 5A, `/access/links`). */
export type TrainerLink = {
  id: number;
  trainer_user_id: number;
  student_user_id: number;
  status: TrainerLinkStatus;
  created_by_user_id: number | null;
  created_at: string | null;
  ended_at: string | null;
  trainer_username: string;
  trainer_full_name: string | null;
  student_username: string;
  student_full_name: string | null;
};

export type TrainerLinkFilters = { status?: TrainerLinkStatus | null };
export type CreateTrainerLinkPayload = { trainer_user_id: number; student_user_id: number };

export type AccessAuditEntry = {
  id: number;
  actor_user_id: number | null;
  action: string;
  target_type: string | null;
  target_id: number | string | null;
  detalhe: unknown;
  created_at: string;
};
