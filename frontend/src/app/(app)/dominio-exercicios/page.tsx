import { AdminOnlyRoute } from '@/components/AdminOnlyRoute';
import { DominioExerciciosClient } from './DominioExerciciosClient';

// Só admin (decisão do usuário em 2026-10-13); os demais vão para
// "Preferências de Treino", onde dão a nota por par exercício+equipamento.
export default function DominioExerciciosPage() {
  return (
    <AdminOnlyRoute>
      <DominioExerciciosClient />
    </AdminOnlyRoute>
  );
}
