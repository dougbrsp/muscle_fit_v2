import { userLookupLabel } from './user-lookup';

/** Admin/PT abrem o plano com o PRÓPRIO usuário pré-selecionado (o seletor troca); assim o campo do dia do check-in aparece. */
export function planoInicial(user: { id: number; username: string; full_name: string | null }): { id: number; label: string } {
  return { id: user.id, label: userLookupLabel(user) };
}
