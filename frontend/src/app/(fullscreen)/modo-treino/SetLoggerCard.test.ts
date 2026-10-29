import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { MEASURE_VALUES } from '@/lib/set-measures';
import { theme } from '@/theme';
import { SetLoggerCard } from './SetLoggerCard';

type CardProps = Parameters<typeof SetLoggerCard>[0];

function render(props: Partial<CardProps>): string {
  return renderToStaticMarkup(
    createElement(
      MantineProvider,
      { theme },
      createElement(SetLoggerCard, {
        exerciseName: 'Prancha',
        setLabel: 'Série 1 de 3',
        defaultReps: 10,
        defaultWeight: 40,
        defaultRpe: 8,
        saving: false,
        onSave: () => {},
        showMuscleMap: false,
        ...props,
      }),
    ),
  );
}

describe('SetLoggerCard — medidas de série (Lote T5)', () => {
  it('reps (ausente): o card de sempre, sem cronômetro de execução', () => {
    const html = render({});
    expect(html).not.toContain('data-testid="execution-timer"');
    expect(html).not.toContain('data-testid="measured-set-fields"');
    expect(html).toContain('aria-label="Diminuir Reps"');
  });

  it('tempo: cronômetro regressivo, campo "Segundos", sem reps, carga opcional', () => {
    const html = render({
      measure: MEASURE_VALUES.duration,
      durationSecondsTarget: 60,
      defaultReps: null,
      defaultWeight: null,
      defaultDurationSeconds: 60,
    });
    expect(html).toContain('data-testid="execution-timer"');
    expect(html).toContain('Tempo restante');
    expect(html).toContain('01:00');
    expect(html).toContain('Iniciar');
    expect(html).toContain('aria-label="Diminuir Segundos"');
    expect(html).not.toContain('aria-label="Diminuir Reps"');
    expect(html).toContain('opcional');
    expect(html).toContain('Alvo: 60 s');
  });

  it('máximo de reps sem janela: cronômetro livre e reps obrigatórias', () => {
    const html = render({ measure: MEASURE_VALUES.max_reps, defaultReps: null, defaultWeight: null });
    expect(html).toContain('Cronômetro');
    expect(html).toContain('aria-label="Diminuir Reps"');
    expect(html).not.toContain('aria-label="Diminuir Segundos"');
    expect(html).toContain('Alvo: reps máx.');
  });
});

// ---------------------------------------------------------------------------
// Novo visual — tela de Execução (PR2/3)
// ---------------------------------------------------------------------------

const PRE_FATIGUED = {
  muscle_group_id: 7,
  muscle_group_name: 'Antebraço',
  band: 'pre_fatigued',
  band_label: 'Pré-fatigado',
} as unknown as NonNullable<CardProps['currentGroupFatigue']>;

const EVALUATION = {
  model: 'test',
  category: 'HOLD',
  category_label: 'Manter',
  note: 'Mantenha os cotovelos próximos ao corpo.',
  raw_text: '',
} as NonNullable<CardProps['aiEvaluation']>;

function count(html: string, needle: string): number {
  return html.split(needle).length - 1;
}

describe('SetLoggerCard — execução (novo visual)', () => {
  it('mostra "SÉRIE X DE Y" e nenhum widget de progressão de séries', () => {
    const html = render({ setLabel: 'Série 2 de 4' });
    expect(html).toContain('data-testid="set-badge"');
    expect(html).toContain('SÉRIE 2 DE 4');
    expect(html).not.toContain('role="progressbar"');
    expect(html).not.toMatch(/stepper|set-progress|series-dots/i);
  });

  it('alvo só com os campos existentes (sem "-" no lugar da carga)', () => {
    const html = render({ repsTarget: '10-12', rpeTarget: '9' });
    expect(html).toContain('Alvo: 10-12 reps · RPE 9');
    expect(html).not.toContain('Alvo: 10-12 reps · -');
  });

  it('fadiga: um único chip no hero, com a faixa e o rótulo do backend', () => {
    const html = render({ currentGroupFatigue: PRE_FATIGUED });
    expect(count(html, 'data-testid="muscle-fatigue-chip"')).toBe(1);
    expect(html).toContain('Pré-fatigado');
    expect(html).toContain('aria-label="Fadiga atual do grupo Antebraço: Pré-fatigado"');
    // a linha textual antiga ("Fadiga atual do grupo: …") não volta
    expect(html).not.toContain('Fadiga atual do grupo: Pré-fatigado');
  });

  it('fadiga indisponível (null) vira "Fadiga indisponível"; carregando (undefined) esconde', () => {
    const unavailable = render({ currentGroupFatigue: null });
    expect(count(unavailable, 'data-testid="muscle-fatigue-chip"')).toBe(1);
    expect(unavailable).toContain('Fadiga indisponível');
    expect(unavailable).not.toContain('Descansado');
    expect(render({})).not.toContain('data-testid="muscle-fatigue-chip"');
  });

  it('a fadiga por série anterior continua no histórico, sem duplicar o chip', () => {
    const html = render({
      currentGroupFatigue: PRE_FATIGUED,
      previousSessionSets: [
        { set_number: 1, reps: 12, weight_kg: 15, rpe: 9, group_fatigue: { ...PRE_FATIGUED, band_label: 'Fatigado' } },
      ] as unknown as CardProps['previousSessionSets'],
    });
    expect(count(html, 'data-testid="muscle-fatigue-chip"')).toBe(1);
    expect(html).toContain('1ª série - 12 x 15kg@RPE9 - Fatigado');
  });

  it('"Dica da IA" só com o toggle ligado e feedback real', () => {
    expect(render({ perSetAiEnabled: true, aiEvaluation: EVALUATION })).toContain('data-testid="ai-tip"');
    expect(render({ perSetAiEnabled: false, aiEvaluation: EVALUATION })).not.toContain('data-testid="ai-tip"');
    expect(render({ perSetAiEnabled: true, aiEvaluation: null })).not.toContain('data-testid="ai-tip"');
    expect(render({ perSetAiEnabled: true, aiEvaluation: { ...EVALUATION, note: '  ' } })).not.toContain(
      'data-testid="ai-tip"',
    );
  });

  it('histórico vazio: "Ainda não há execução comparável"; null esconde o card', () => {
    const empty = render({ previousSessionSets: [] });
    expect(empty).toContain('Treino anterior');
    expect(empty).toContain('Ainda não há execução comparável');
    expect(render({ previousSessionSets: null })).not.toContain('data-testid="previous-session"');
  });

  it('Trocar e Pular presentes e visíveis no hero (com Encerrar)', () => {
    const html = render({ onSubstituteClick: () => {}, onSkipExercise: () => {}, onFinishExercise: () => {} });
    expect(html).toContain('data-testid="execution-swap"');
    expect(html).toContain('aria-label="Trocar equipamento/exercício"');
    expect(html).toContain('data-testid="execution-skip"');
    expect(html).toContain('aria-label="Pular exercício"');
    expect(html).toContain('>Trocar<');
    expect(html).toContain('>Pular<');
    expect(html).toContain('aria-label="Encerrar exercício"');
  });

  it('PR4: título compacto — nome completo no DOM/title, equipamento e fadiga numa linha', () => {
    const longName = 'Elevação de Joelhos na Estação Vertical com Pegada Pronada';
    const html = render({ exerciseName: longName, equipmentName: 'Barra W/EZ', currentGroupFatigue: PRE_FATIGUED });
    expect(html).toContain(`aria-label="Exercício: ${longName}"`);
    expect(html).toMatch(new RegExp(`<h2[^>]*title="${longName}"[^>]*>${longName}</h2>`));
    expect(html).toContain('Barra W/EZ');
    expect(count(html, 'data-testid="muscle-fatigue-chip"')).toBe(1);
  });

  it('PR4: Trocar / Pular / Encerrar ficam na linha do CTA, explícitos', () => {
    const html = render({ onSubstituteClick: () => {}, onSkipExercise: () => {}, onFinishExercise: () => {} });
    const bar = html.slice(html.indexOf('data-testid="workout-action-bar"'));
    expect(bar).toContain('data-testid="execution-swap"');
    expect(bar).toContain('data-testid="execution-skip"');
    expect(bar).toContain('data-testid="execution-finish-exercise"');
    expect(bar).toContain('data-testid="set-save"');
  });

  it('PR4: unidade kg/lb é um sufixo tocável do campo Carga (sem linha própria)', () => {
    const html = render({ weightUnit: 'kg', onWeightUnitChange: () => {} });
    expect(html).toContain('data-testid="weight-unit-toggle"');
    expect(html).toContain('aria-label="Unidade da carga: kg. Tocar para anotar em lb"');
    expect(html).toContain('aria-label="Diminuir Carga (kg)"');
    // Sem quem trate a troca, a unidade aparece só como texto.
    expect(render({ weightUnit: 'lb' })).not.toContain('weight-unit-toggle');
  });

  it('CTA no rodapé sticky com o rótulo de quem chama', () => {
    const html = render({ saveLabel: 'Finalizar série' });
    expect(html).toContain('data-testid="workout-action-bar"');
    expect(html).toContain('Finalizar série');
    expect(html).toContain('data-testid="set-save"');
  });

  it('erro ao salvar aparece junto do CTA', () => {
    const html = render({ saveError: 'Não foi possível salvar a série.' });
    expect(html).toContain('role="alert"');
    expect(html).toContain('Não foi possível salvar a série.');
  });

  it('variantes avançadas usam o mesmo invólucro (hero + card da série + rodapé)', () => {
    for (const variant of ['advancedWarmup', 'advancedFeeler', 'techniqueApplication'] as const) {
      const html = render({ variant, setLabel: 'Aquecimento 1/2', perSetAiEnabled: true, aiEvaluation: EVALUATION });
      expect(html).toContain('data-testid="execution-hero"');
      expect(html).toContain('data-testid="set-card"');
      expect(html).toContain('data-testid="workout-action-bar"');
      expect(html).toContain('Concluir');
      expect(html).toContain('AQUECIMENTO 1/2');
      expect(html).not.toContain('data-testid="ai-tip"');
    }
  });
});

describe('M102: série unilateral (E/D)', () => {
  it('trabalho de exercício unilateral mostra reps e RPE por lado, carga única', () => {
    const html = render({ unilateral: true });
    expect(html).toContain('data-testid="unilateral-set-fields"');
    for (const label of ['Reps E', 'Reps D', 'RPE E', 'RPE D']) expect(html).toContain(label);
  });

  it('backoff também; aquecimento, técnica e exercício bilateral ficam com valor único', () => {
    const marker = 'data-testid="unilateral-set-fields"';
    expect(render({ unilateral: true, setType: 'backoff' })).toContain(marker);
    expect(render({ unilateral: true, setType: 'warmup' })).not.toContain(marker);
    expect(render({ unilateral: true, variant: 'techniqueApplication' })).not.toContain(marker);
    expect(render({ unilateral: true, variant: 'advancedWorking' })).not.toContain(marker);
    expect(render({})).not.toContain(marker);
  });
});
