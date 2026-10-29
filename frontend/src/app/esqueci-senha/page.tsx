import { EsqueciSenhaClient } from './EsqueciSenhaClient';

// Rota pública estática (export): não vive no grupo `(app)` porque não há
// sessão aqui — mesmo padrão do login em `src/app/page.tsx`.
export default function EsqueciSenhaPage() {
  return <EsqueciSenhaClient />;
}
