// Build do artefato estatico usado pelo baseline E2E: `next build` (output: export)
// com a API apontada para um host FALSO (http://mock-api.test), que so existe
// como alvo de `page.route` nos testes. Assim nenhuma chamada sai para a rede.
// Multiplataforma (sem sintaxe de env inline do shell).
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

export const MOCK_API_BASE_URL = 'http://mock-api.test';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const result = spawnSync('npx', ['next', 'build'], {
  cwd: root,
  stdio: 'inherit',
  shell: true,
  env: { ...process.env, NEXT_PUBLIC_API_BASE_URL: MOCK_API_BASE_URL, NEXT_TELEMETRY_DISABLED: '1' },
});
process.exit(result.status ?? 1);
