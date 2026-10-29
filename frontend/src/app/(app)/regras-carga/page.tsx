import { AdminOnlyRoute } from '@/components/AdminOnlyRoute';
import { RegrasCargaClient } from './RegrasCargaClient';

// Só admin: as regras são globais (valem para todos os usuários ao publicar).
// O backend também barra (`require_admin` em `next_set_load_rules_admin.py`).
export default function RegrasCargaPage() {
  return (
    <AdminOnlyRoute>
      <RegrasCargaClient />
    </AdminOnlyRoute>
  );
}
