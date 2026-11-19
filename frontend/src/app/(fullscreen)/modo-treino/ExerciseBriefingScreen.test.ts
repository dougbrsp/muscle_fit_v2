import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { buildBriefingSections, groupMusclesByRole, parseExecutionDescription } from '@/lib/exercise-briefing';
import type { ExerciseBriefingResponse } from '@/lib/exercise-briefing-types';
import parity from '@/lib/training-goal.parity.json';
import type { DeloadPrescription } from '@/lib/deload';
import { exerciseFormPracticeField, formPracticeCreateField } from '@/lib/form-practice';
import { theme } from '@/theme';
import { BriefingInstructions, ExerciseBriefingView, resolveFormPracticeState } from './ExerciseBriefingScreen';

type ViewProps = Parameters<typeof ExerciseBriefingView>[0];

const BRIEFING: ExerciseBriefingResponse = {
  plan_id: 3,
  workout_plan_exercise_id: 777,
  sequence: 1,
  exercise: {
    id: 42,
    name: 'Elevação de Joelhos na Estação Vertical',
    catalog_name: 'Elevação de Joelhos na Estação Vertical',
    description: null,
    execution_description: [
      'Preparação:',
      '- Suba na estação, encoste as costas e ajuste a pegada. Mantenha os ombros baixos.',
      'Execução:',
      '- Eleve os joelhos, mantenha o tronco firme e desça controlado.',
      'Atenção:',
      '- Evite impulso, mantenha o pescoço neutro.',
      '- Pare se sentir dor lombar.',
    ].join('\n'),
    execution_description_reviewed: true,
    is_unilateral: false,
    exercise_card: null,
  },
  equipment: { gym_equipment_id: 5, equipment_id: 9, name: 'Estação vertical', photo_url: 'https://cdn/eq/9.webp' },
  muscles: [
    { muscle_group_id: 1, name: 'Abdômen', role: 'PRIMARY', stimulus_weight: 1 },
    { muscle_group_id: 2, name: 'Flexores do quadril', role: 'SECONDARY', stimulus_weight: 0.5 },
  ],
  prescription: {
    sets_count: 2,
    reps_target: '12-15',
    load_target: null,
    suggested_load_kg: 42.5,
    rpe_target: '8',
    effective_rpe_target: 8,
    effective_rpe_ceiling: null,
    failure_allowed: false,
    advanced_technique: null,
    advanced_technique_applications: null,
    rest_seconds: 75,
    status: 'FROZEN',
    warnings: [],
  },
  last_execution: null,
  performance: {
    status: 'INSUFFICIENT_DATA',
    reasons: [],
    comparable: false,
    scope: null,
    comparable_exposures: 0,
    equipment_name: null,
    load_convention: null,
    last_local_date: null,
  },
  adjustment: { decision: 'HOLD', label: 'HOLD', reason: 'Última execução dentro da faixa.', source: 'engine_decision' },
  trend: { status: 'unknown', label: 'Primeira vez', reason: '', source: 'none' },
  track: null,
  requires_user_choice: false,
};

function render(props: Partial<ViewProps> = {}): string {
  return renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client: new QueryClient() },
      createElement(
        MantineProvider,
        { theme },
        createElement(ExerciseBriefingView, {
          data: BRIEFING,
          exerciseName: 'Nome da ficha',
          onStart: () => {},
          ...props,
        }),
      ),
    ),
  );
}

/** HTML do elemento com `data-testid`, sem os SVGs dos ícones (cortado a 3000 caracteres). */
function section(html: string, testId: string): string {
  const start = html.indexOf(`data-testid="${testId}"`);
  expect(start).toBeGreaterThan(-1);
  return html.slice(start).replace(/<svg[\s\S]*?<\/svg>/g, '<svg/>').slice(0, 3000);
}

describe('ExerciseBriefingView — novo visual (PR1/3)', () => {
  it('hero: foto do equipamento, equipamento e nome inteiro do exercício', () => {
    const html = render();
    expect(html).toContain('src="https://cdn/eq/9.webp"');
    expect(html).toContain('Estação vertical');
    expect(html).toContain('Elevação de Joelhos na Estação Vertical');
    expect(html).not.toContain('Nome da ficha');
  });

  it('4 métricas da prescrição: séries x reps, carga, esforço e descanso', () => {
    const metrics = section(render(), 'briefing-metrics');
    expect(metrics).toContain('Séries x reps');
    expect(metrics).toContain('2 x 12-15');
    expect(metrics).toContain('42,5 kg');
    expect(metrics).toContain('RPE 8');
    expect(metrics).toContain('75s');
  });

  it('carga em lb quando a preferência é libras', () => {
    const load = section(render({ weightUnit: 'lb' }), 'briefing-load');
    expect(load).toContain('93,7 lb');
    expect(load).not.toContain('42,5 kg');
  });

  it('"Sem comparação" só quando performance.comparable é falso', () => {
    expect(render()).toContain('data-testid="briefing-no-comparison"');
    const comparable = render({
      data: {
        ...BRIEFING,
        performance: { ...(BRIEFING.performance as NonNullable<ExerciseBriefingResponse['performance']>), status: 'PROGRESSING', comparable: true, scope: 'equipment' },
      },
    });
    expect(comparable).not.toContain('briefing-no-comparison');
    expect(comparable).not.toContain('Sem comparação');
    expect(comparable).toContain('data-testid="briefing-performance"');
  });

  it('"Ajuste de hoje": HOLD aparece como "Manter", com a justificativa expansível', () => {
    const strip = section(render(), 'briefing-adjustment');
    expect(strip).toContain('Ajuste de hoje');
    expect(strip).toContain('Manter');
    expect(strip).not.toContain('>HOLD<');
    expect(strip).toContain('aria-expanded="false"');
    expect(strip).toContain('Última execução dentro da faixa.');
  });

  it('PR4: instruções técnicas numa gaveta — na tela só o botão "Como executar"', () => {
    const html = render();
    const howTo = section(html, 'briefing-how-to');
    expect(howTo).toContain('data-testid="briefing-instructions-open"');
    expect(howTo).toContain('aria-haspopup="dialog"');
    expect(howTo).toContain('Como executar');
    expect(howTo).toContain('Preparação · Execução · Atenção · Músculos');
    // Fechada, a gaveta não desenha os passos na página (nada a rolar).
    expect(html).not.toContain('data-testid="briefing-instructions"');
    expect(html).not.toContain('Eleve os joelhos');
  });

  it('gaveta: Preparação, Execução, Atenção e Músculos, íntegros e na ordem', () => {
    const html = renderToStaticMarkup(
      createElement(
        MantineProvider,
        { theme },
        createElement(BriefingInstructions, {
          sections: buildBriefingSections(parseExecutionDescription(BRIEFING.exercise.execution_description)),
          muscleGroups: groupMusclesByRole(BRIEFING.muscles),
          exerciseName: BRIEFING.exercise.name ?? '',
        }),
      ),
    );
    expect(section(html, 'briefing-section-preparation')).toContain(
      'Suba na estação, encoste as costas e ajuste a pegada. Mantenha os ombros baixos.',
    );
    expect(section(html, 'briefing-section-execution')).toContain('Eleve os joelhos, mantenha o tronco firme e desça controlado.');
    const attention = section(html, 'briefing-section-attention');
    expect(attention).toContain('Evite impulso, mantenha o pescoço neutro.');
    expect(attention).toContain('Pare se sentir dor lombar.');
    const muscles = section(html, 'briefing-section-muscles');
    expect(muscles).toContain('<strong>Primário:</strong> Abdômen');
    expect(muscles).toContain('<strong>Secundário:</strong> Flexores do quadril');
    expect(html.indexOf('briefing-section-preparation')).toBeLessThan(html.indexOf('briefing-section-execution'));
    expect(html.indexOf('briefing-section-execution')).toBeLessThan(html.indexOf('briefing-section-attention'));
  });

  it('Atenção: o cuidado de segurança fica SEMPRE visível na tela, em 1 linha', () => {
    const page = render();
    expect(page).toContain('role="note" data-testid="briefing-attention-line"');
    const line = section(page, 'briefing-attention-line');
    expect(line).toContain('Atenção: ');
    expect(line).toContain('Evite impulso, mantenha o pescoço neutro.');
    expect(line.slice(0, line.indexOf('</div>'))).not.toMatch(/hidden/);
  });

  it('PR4: título compacto com o nome COMPLETO acessível (DOM e title), sem card de destaque', () => {
    const longName = 'Elevação de Joelhos na Estação Vertical com Pegada Pronada';
    const html = render({ data: { ...BRIEFING, exercise: { ...BRIEFING.exercise, name: longName } } });
    const title = section(html, 'briefing-title');
    expect(html).toContain(`aria-label="Exercício: ${longName}" data-testid="briefing-title"`);
    expect(title).toMatch(new RegExp(`<h2[^>]*title="${longName}"[^>]*>${longName}</h2>`));
    // Equipamento e fadiga na mesma linha, abaixo do nome.
    expect(title).toContain('Estação vertical');
  });

  it('"Como estamos" sem execuções comparáveis e primeira vez', () => {
    const card = section(render(), 'briefing-how-we-are');
    expect(card).toContain('Como estamos');
    expect(card).toContain('Ainda não há duas execuções comparáveis');
    expect(card).toContain('Primeira vez que você faz este exercício por aqui.');
  });

  it('ações: Iniciar exercício + Trocar e Pular explícitos, e o próximo exercício', () => {
    const html = render({ onSwap: () => {}, onSkip: () => {}, nextExercise: { name: 'Puxada alta', muscle: 'Costas' } });
    const bar = section(html, 'workout-action-bar');
    expect(bar).toContain('data-testid="briefing-start"');
    expect(bar).toContain('Iniciar exercício');
    expect(bar).toContain('Trocar exercício');
    expect(bar).toContain('Pular exercício');
    const next = section(html, 'briefing-next');
    expect(next).toContain('Puxada alta');
    expect(next).toContain('Costas');
  });

  it('sem fluxo de troca disponível, o botão não aparece; último exercício avisa', () => {
    const html = render({ onSkip: () => {}, nextExercise: null });
    expect(html).not.toContain('briefing-swap');
    expect(html).toContain('briefing-skip');
    expect(section(html, 'briefing-next')).toContain('Último exercício da ficha');
  });

  it('fadiga: chip com o rótulo do backend; sem leitura, "Fadiga indisponível"; carregando, oculto', () => {
    const reading = {
      muscle_group_id: 1,
      muscle_group_name: 'Abdômen',
      prior_sets: 2,
      level: 1,
      band: 'pre_fatigued' as const,
      band_label: 'Pré-fatigado',
    };
    expect(section(render({ groupFatigue: reading }), 'muscle-fatigue-chip')).toContain('Pré-fatigado');
    const unavailable = render({ groupFatigue: null });
    expect(unavailable).toContain('Fadiga indisponível');
    expect(unavailable).not.toContain('Descansado');
    expect(render()).not.toContain('muscle-fatigue-chip');
  });

  it('erro no briefing: o CTA continua disponível', () => {
    const html = render({ data: undefined, isError: true });
    expect(html).toContain('Não foi possível carregar o briefing');
    expect(html).toContain('data-testid="briefing-start"');
    expect(html).toContain('Nome da ficha');
  });

  it('exercício trocado: hero e métricas do exercício efetivo, sem comparação nem ajuste do par original', () => {
    const html = render({
      weightUnit: 'kg',
      substitution: {
        exerciseName: 'Abdominal na máquina',
        equipmentName: 'Máquina de abdominal',
        equipmentPhotoUrl: null,
        sameExercise: false,
        setsCount: 3,
        repsTarget: '10-12',
        loadTarget: '30',
        rpeTarget: '8',
      },
    });
    expect(html).toContain('Abdominal na máquina');
    expect(html).toContain('Máquina de abdominal');
    expect(section(html, 'briefing-metrics')).toContain('3 x 10-12');
    expect(section(html, 'briefing-load')).toContain('30 kg');
    expect(html).toContain('data-testid="briefing-substitution"');
    expect(html).not.toContain('briefing-adjustment');
    expect(html).not.toContain('briefing-no-comparison');
    expect(html).not.toContain('briefing-how-we-are');
    expect(html).not.toContain('briefing-section-preparation');
  });
});

describe('ExerciseBriefingView — M97 F3 (objetivo e deload)', () => {
  const prescription = parity.deload_prescription as DeloadPrescription;

  it('sessão de deload: bloco "Deload hoje" com a prescrição pronta do backend', () => {
    const html = render({
      data: { ...BRIEFING, deload: { active: true, source: 'manual', period: null, prescription } },
    });
    const block = section(html, 'briefing-deload');
    expect(block).toContain('Deload hoje:');
    expect(block).toContain(`RPE ${prescription.target_rpe}`);
    expect(block).toContain('carga de trabalho');
  });

  it('sem deload (ou exercício trocado), nenhum bloco', () => {
    expect(render()).not.toContain('briefing-deload');
    expect(
      render({ data: { ...BRIEFING, deload: { active: false, source: null, period: null, prescription: null } } }),
    ).not.toContain('briefing-deload');
  });

  it('badge do objetivo no título; técnica escondida quando o objetivo não a permite', () => {
    const comTecnica = { ...BRIEFING, prescription: { ...BRIEFING.prescription, advanced_technique: 'drop_set' } };
    const html = render({ data: comTecnica, goalBadge: createElement('span', { 'data-testid': 'goal-x' }, 'G') });
    expect(section(html, 'briefing-title')).toContain('data-testid="goal-x"');
    expect(html).toContain('Técnica avançada:');
    expect(render({ data: comTecnica, hideTechnique: true })).not.toContain('Técnica avançada:');
  });
});

describe('fabricante por academia (#188)', () => {
  const equipment = BRIEFING.equipment as NonNullable<ExerciseBriefingResponse['equipment']>;

  it('mostra o fabricante efetivo e indica o padrão do catálogo', () => {
    const html = render({
      data: { ...BRIEFING, equipment: { ...equipment, manufacturer: 'Matrix', manufacturer_source: 'catalog' } },
    });
    expect(html).toContain('Fabricante: Matrix (padrão do catálogo)');
    expect(html).not.toContain('Editar fabricante');
  });

  it('só oferece edição com o indicador do servidor e vínculo no catálogo da academia', () => {
    const base = { ...equipment, manufacturer: 'Hammer', manufacturer_source: 'gym' as const, can_edit_manufacturer: true };
    expect(render({ data: { ...BRIEFING, equipment: { ...base, gym_equipment_catalog_id: 7 } } })).toContain(
      'Editar fabricante',
    );
    expect(render({ data: { ...BRIEFING, equipment: { ...base, gym_equipment_catalog_id: null } } })).not.toContain(
      'Editar fabricante',
    );
  });

  it('API antiga (sem os campos) não mostra a linha do fabricante', () => {
    expect(render()).not.toContain('briefing-manufacturer');
  });
});

/** Tag de abertura do elemento com `data-testid` (os atributos vêm antes do testid). */
function openTag(html: string, testId: string): string {
  const at = html.indexOf(`data-testid="${testId}"`);
  expect(at).toBeGreaterThan(-1);
  return html.slice(html.lastIndexOf('<', at), html.indexOf('>', at) + 1);
}

describe('ExerciseBriefingView — aprimoramento de técnica (#196)', () => {
  it('sem handler, a ação não existe', () => {
    expect(render()).not.toContain('briefing-form-practice"');
  });

  it('inativa: botão aria-pressed=false e nenhum indicador', () => {
    const html = render({ onToggleFormPractice: () => {} });
    const btn = openTag(html, 'briefing-form-practice');
    expect(btn).toContain('aria-pressed="false"');
    expect(btn).not.toContain('disabled');
    expect(html).not.toContain('briefing-form-practice-on');
  });

  it('ativa: aria-pressed=true e indicador textual com rótulo e ajuda', () => {
    const html = render({ onToggleFormPractice: () => {}, formPractice: true });
    expect(openTag(html, 'briefing-form-practice')).toContain('aria-pressed="true"');
    const on = section(html, 'briefing-form-practice-on');
    expect(on).toContain('Aprimoramento de técnica');
    expect(on).toContain('não conta para a progressão');
  });

  it('travada: desabilitada e ainda mostra o estado', () => {
    const html = render({ onToggleFormPractice: () => {}, formPractice: true, formPracticeLocked: true });
    const btn = openTag(html, 'briefing-form-practice');
    expect(btn).toContain('disabled');
    expect(btn).toContain('aria-pressed="true"');
    expect(html).toContain('briefing-form-practice-on');
  });
});

describe('resolveFormPracticeState (#196)', () => {
  const extras = [{ localId: -1, originalWorkoutPlanExerciseId: 10 }] as never;
  const base = { midExerciseExtras: [], loggedRow: null, loggedExercises: [], pendingIds: [] as number[] };

  it('sem linha: vale a marcação pendente e não trava', () => {
    expect(resolveFormPracticeState({ ...base, planExerciseId: 10, pendingIds: [10] })).toEqual({ itemId: 10, marked: true, locked: false, onServer: false });
    expect(resolveFormPracticeState({ ...base, planExerciseId: 10 })).toEqual({ itemId: 10, marked: false, locked: false, onServer: false });
  });

  it('com linha só de aquecimento: vale a linha e NÃO trava (#213)', () => {
    const loggedRow = { form_practice: false, sets: [{ set_type: 'warmup' }, { set_type: 'feeler' }] };
    const r = resolveFormPracticeState({ ...base, planExerciseId: 10, pendingIds: [10], loggedRow });
    expect(r).toEqual({ itemId: 10, marked: false, locked: false, onServer: true });
  });

  it('com série de trabalho na linha: trava', () => {
    const loggedRow = { form_practice: true, sets: [{ set_type: 'warmup' }, { set_type: 'working' }] };
    expect(resolveFormPracticeState({ ...base, planExerciseId: 10, loggedRow })).toEqual({ itemId: 10, marked: true, locked: true, onServer: true });
  });

  it('resposta do servidor decide marca e travamento, e vence a linha local', () => {
    const loggedRow = { id: 10, form_practice: false, sets: [{ set_type: 'warmup' }] };
    const open = resolveFormPracticeState({ ...base, planExerciseId: 10, loggedRow, serverStates: { 10: { exerciseId: 10, marked: true, locked: false } } });
    expect(open).toEqual({ itemId: 10, marked: true, locked: false, onServer: true });
    const locked = resolveFormPracticeState({ ...base, planExerciseId: 10, loggedRow, serverStates: { 10: { exerciseId: 10, marked: false, locked: true } } });
    expect(locked).toEqual({ itemId: 10, marked: false, locked: true, onServer: true });
  });

  it('continuação: herda a marca do item original e nunca nasce travada', () => {
    const rows = [{ workout_plan_exercise_id: 10, form_practice: true }];
    expect(resolveFormPracticeState({ ...base, planExerciseId: -1, midExerciseExtras: extras, loggedExercises: rows })).toEqual({ itemId: 10, marked: true, locked: false, onServer: false });
    expect(resolveFormPracticeState({ ...base, planExerciseId: -1, midExerciseExtras: extras, pendingIds: [10] })).toEqual({ itemId: 10, marked: true, locked: false, onServer: false });
  });

  it('estado do servidor do exercício original não trava a continuação (achado 1)', () => {
    const rows = [{ id: 10, workout_plan_exercise_id: 10, form_practice: true }];
    const serverStates = { 10: { exerciseId: 10, marked: true, locked: true } };
    // (a) continuação sem linha: ignora o estado, herda a marca, destravada
    expect(resolveFormPracticeState({ ...base, planExerciseId: -1, midExerciseExtras: extras, loggedExercises: rows, serverStates }))
      .toEqual({ itemId: 10, marked: true, locked: false, onServer: false });
    // (b) o mesmo estado com a linha do exercício 10: travado
    expect(resolveFormPracticeState({ ...base, planExerciseId: 10, loggedRow: rows[0], serverStates }))
      .toEqual({ itemId: 10, marked: true, locked: true, onServer: true });
    // (c) estado de outro exercício: usa a linha 11
    const row11 = { id: 11, form_practice: false, sets: [] };
    expect(resolveFormPracticeState({ ...base, planExerciseId: -1, midExerciseExtras: extras, loggedRow: row11, serverStates }))
      .toEqual({ itemId: 10, marked: false, locked: false, onServer: true });
  });

  it('(d) desmarcar a continuação antes de criá-la: a criação leva form_practice false', () => {
    const rows = [{ id: 10, workout_plan_exercise_id: 10, form_practice: true }];
    const r = resolveFormPracticeState({ ...base, planExerciseId: -1, midExerciseExtras: extras, loggedExercises: rows, continuationChoices: { [-1]: false } });
    expect(r).toEqual({ itemId: 10, marked: false, locked: false, onServer: false });
    expect(formPracticeCreateField(r.marked)).toEqual({ form_practice: false });
  });

  it('criado e sem linha local ainda: vale o estado do servidor para o id atual (#213)', () => {
    const open = resolveFormPracticeState({ ...base, planExerciseId: 10, currentExerciseId: 10, serverStates: { 10: { exerciseId: 10, marked: true, locked: false } } });
    expect(open).toEqual({ itemId: 10, marked: true, locked: false, onServer: true });
    // o 2º aquecimento leva true, não false
    expect(exerciseFormPracticeField(open)).toEqual({ exercise_form_practice: true });
    const locked = resolveFormPracticeState({ ...base, planExerciseId: 10, currentExerciseId: 10, serverStates: { 10: { exerciseId: 10, marked: true, locked: true } } });
    expect(locked).toEqual({ itemId: 10, marked: true, locked: true, onServer: true });
  });

  it('continuação não criada nunca trava; criada vale o estado dela', () => {
    const rows = [{ id: 10, workout_plan_exercise_id: 10, form_practice: true }];
    const original = { 10: { exerciseId: 10, marked: true, locked: true } };
    expect(resolveFormPracticeState({ ...base, planExerciseId: -1, midExerciseExtras: extras, loggedExercises: rows, serverStates: original }))
      .toEqual({ itemId: 10, marked: true, locked: false, onServer: false });
    const own = { 10: { exerciseId: 11, marked: false, locked: false } };
    expect(resolveFormPracticeState({ ...base, planExerciseId: -1, currentExerciseId: 11, midExerciseExtras: extras, loggedExercises: rows, serverStates: own }))
      .toEqual({ itemId: 10, marked: false, locked: false, onServer: true });
  });
});
