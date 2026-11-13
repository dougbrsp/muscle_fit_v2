import { PermissionRoute } from '@/components/AdminOnlyRoute';
import { EquipamentosExerciciosClient } from './EquipamentosExerciciosClient';

// Só admin (decisão do usuário em 2026-10-13); os demais vão para
// "Preferências de Treino".
export default function EquipamentosExerciciosPage() {
  return (
    <PermissionRoute resource="admin.catalogos">
      <EquipamentosExerciciosClient />
    </PermissionRoute>
  );
}
