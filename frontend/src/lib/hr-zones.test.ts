import { describe, expect, it } from 'vitest';

import { hrZoneSelectData, hrZonesHint, templateHrZonesPath, type TemplateHrZones } from './hr-zones';
import motorConstants from './motor-constants.generated.json';

// M98 C1: as faixas vêm prontas da API (dono `hr_zones`); aqui só o formato.
describe('hrZoneSelectData', () => {
  it('sem zonas: só os rótulos Z1–Z5 do dono', () => {
    const data = hrZoneSelectData([]);
    expect(data.map((d) => d.value)).toEqual(motorConstants.hr_zones.zone_numbers.map(String));
    expect(data.map((d) => d.label)).toEqual(['Z1', 'Z2', 'Z3', 'Z4', 'Z5']);
  });

  it('com zonas: a faixa de bpm que veio da API ao lado do rótulo', () => {
    const data = hrZoneSelectData([{ zone: 2, label: 'Z2', lower_bpm: 115, upper_bpm: 133 }]);
    expect(data[1]).toEqual({ value: '2', label: 'Z2 · 115–133 bpm' });
    expect(data[0].label).toBe('Z1');
  });
});

// Correção da C1: de quem são as zonas é decidido pelo backend; aqui só o
// caminho pedido e o texto exibido para cada `subject` que a API devolve.
describe('zonas do editor do Modelo', () => {
  const zona = { zone: 2, label: 'Z2', lower_bpm: 115, upper_bpm: 133 };
  const base = (subject: TemplateHrZones['subject'], zones = [zona]): TemplateHrZones => ({
    source: 'manual_max_hr',
    max_hr_bpm: 190,
    zones,
    subject,
  });

  it('pede pelo Modelo aberto; Modelo novo vai sem template_id', () => {
    expect(templateHrZonesPath(12)).toBe('/workout-plan-templates/hr-zones?template_id=12');
    expect(templateHrZonesPath(null)).toBe('/workout-plan-templates/hr-zones');
  });

  it('aluno do Modelo: mostra de quem são as zonas', () => {
    const data = base({ kind: 'assignee', user_id: 9, name: 'Ana', reason: null });
    expect(hrZonesHint(data)).toBe('Zonas de Ana.');
    expect(hrZonesHint({ ...data, zones: [] })).toContain('Ana ainda não tem zonas');
  });

  it('próprias zonas: sem aviso com zonas, dica do perfil sem zonas', () => {
    const data = base({ kind: 'self', user_id: 1, name: null, reason: null });
    expect(hrZonesHint(data)).toBeNull();
    expect(hrZonesHint({ ...data, zones: [] })).toContain('FCmáx no Perfil');
  });

  it('PT sem aluno vinculado: pede para atribuir; vários alunos: avisa', () => {
    const semAluno = base({ kind: 'none', user_id: null, name: null, reason: 'no_assignee' }, []);
    expect(hrZonesHint(semAluno)).toContain('Atribua o Modelo');
    const varios = base({ kind: 'none', user_id: null, name: null, reason: 'multiple_assignees' }, []);
    expect(hrZonesHint(varios)).toContain('mais de um aluno');
  });
});
