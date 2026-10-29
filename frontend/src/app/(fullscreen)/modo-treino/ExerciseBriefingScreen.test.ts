import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { buildBriefingSections, groupMusclesByRole, parseExecutionDescription } from '@/lib/exercise-briefing';
import type { ExerciseBriefingResponse } from '@/lib/exercise-briefing-types';
import parity from '@/lib/training-goal.parity.json';
import type { DeloadPrescription } from '@/lib/deload';
import { theme } from '@/theme';
import { BriefingInstructions, ExerciseBriefingView } from './ExerciseBriefingScreen';

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
      MantineProvider,
      { theme },
      createElement(ExerciseBriefingView, {
        data: BRIEFING,
        exerciseName: 'Nome da ficha',
        onStart: () => {},
        ...props,
      }),
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

