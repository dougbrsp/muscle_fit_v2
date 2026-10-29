// Tipos e helpers da tela Usuários (Fase 4, Etapa 23) — CRUD de usuários da
// plataforma, admin-only. Contrato confirmado lendo diretamente
// C:\muscle_fit\api\app\users.py (não copiado do legado sem checar, pois o
// backend novo divergiu em vários pontos: roles suportados, soft-delete etc.).
// O reset administrativo de senha (POST /users/{id}/reset-password) passou a
// existir — ver `ResetPasswordResponse` no fim deste arquivo.

export type ApiListResponse<T> = { items: T[] };

// Valores de `role` aceitos por POST/PUT /users (`_ALLOWED_ROLES` em
// users.py). `personal_trainer` (ENUM da 007, `require_admin_or_pt`) passou a
// ser atribuível por este CRUD no L0 do plano de nutrição (2026-10-15).
export const USER_ROLES = ['admin', 'regular', 'personal_trainer'] as const;
export type UserRole = (typeof USER_ROLES)[number];

export const ROLE_OPTIONS: { value: UserRole; label: string }[] = [
  { value: 'admin', label: 'Administrador' },
  { value: 'regular', label: 'Usuário comum' },
  { value: 'personal_trainer', label: 'Personal trainer' },
];

export function isUserRole(value: string | null | undefined): value is UserRole {
  return value != null && (USER_ROLES as readonly string[]).includes(value);
}

export function roleLabel(role: string): string {
  return ROLE_OPTIONS.find((option) => option.value === role)?.label || role;
}

/**
 * Item retornado por GET /users e pelos `item` de POST/PUT /users(/{id})
 * (`_public_row` em `api/app/users.py`): todas as colunas de `users` exceto
 * `password_hash`, mais os campos derivados `has_photo`/`intervals_configured`.
 * Não existe `GET /users/{id}` — a tela de edição parte sempre da linha já
 * carregada pela listagem.
 */
export type UserAdminItem = {
  id: number;
  username: string;
  full_name: string | null;
  email: string | null;
  birth_date: string | null;
  /** Código do motor (`male`/`female`/`other`), canônico na resposta (Nutrição L2a); `null` = não informado. */
  sex: string | null;
  /** Perfil antropométrico (migração 074); opcionais: só a tela "Meu perfil" edita. */
  height_cm?: number | null;
  activity_level?: string | null;
  role: string;
  is_active: boolean;
  must_change_password?: boolean;
  intervals_athlete_id: string | null;
  has_photo: boolean;
  intervals_configured: boolean;
  /** Fuso IANA (migração 044). Sempre presente em `_public_row`. */
  timezone?: string;
};

export type UserAdminListResponse = ApiListResponse<UserAdminItem>;

export type UserAdminMutationResponse = { status: string; item: UserAdminItem };

/** Payload de POST /users (admin). `username`/`password` obrigatórios. */
export type CreateUserPayload = {
  username: string;
  password: string;
  role: UserRole;
  full_name?: string | null;
  email?: string | null;
};

/**
 * Payload de PUT /users/{id} (admin). Todos os campos são opcionais — o
 * backend faz `COALESCE` com o valor já gravado para o que não vier no
 * payload (nunca envie `null` para um campo que não deve ser alterado, senão
 * ele é sobrescrito).
 */
export type UpdateUserPayload = {
  full_name?: string | null;
  email?: string | null;
  birth_date?: string | null;
  /** Código do motor (`SEX_OPTIONS` de `nutrition-constants`); a API recusa outro valor com 400 `invalid_sex`. */
  sex?: string | null;
  role?: UserRole;
  is_active?: boolean;
};

/**
 * Resposta de POST /users/{id}/reset-password (admin). A senha é GERADA pelo
 * servidor e devolvida UMA única vez — o banco guarda só o hash bcrypt, então
 * não há como recuperá-la depois. O chamador não escolhe a senha (a rota não
 * aceita corpo).
 */
export type ResetPasswordResponse = {
  status: string;
  id: number;
  username: string;
  temporary_password: string;
  must_change_password: boolean;
  revoked_sessions: number;
};

// As opções de sexo NÃO moram aqui: código e rótulo são do motor
// (`domain/nutrition/anthropometrics.Sex` + `labels.SEX_LABELS`) e chegam por
// `SEX_OPTIONS` de `@/lib/nutrition-constants` (Nutrição L2a; guarda `vocabulario_sexo`).
