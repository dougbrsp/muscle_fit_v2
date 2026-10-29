'use client';

import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  Divider,
  Group,
  Loader,
  NumberInput,
  PasswordInput,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { IconAlertTriangle, IconCheck, IconLock, IconUpload, IconUser } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'next/navigation';
import { ChangeEvent, FormEvent, useEffect, useRef, useState } from 'react';
import { apiFetch, apiFetchBlob, ApiError } from '@/lib/api';
import { MANUAL_MAX_HR_LIMITS } from '@/lib/cardio-prescription';
import { HR_ZONES_QUERY_KEY } from '@/lib/hr-zones';
import { ACTIVITY_LEVEL_OPTIONS, NUTRITION_FORM_RANGES, SEX_OPTIONS } from '@/lib/nutrition-constants';
import { refreshAfterPasswordChange } from '@/lib/password-change';
import { saveStoredWeightUnit } from '@/lib/use-weight-unit';
import { normalizeTimezone, TIMEZONE_OPTIONS } from '@/lib/timezones';
import { normalizeWeightUnit, type WeightUnit } from '@/lib/weight-units';
import { BRAND } from '@/theme';
import { DevicesCard } from './DevicesCard';
import { PasswordChangeCard } from './PasswordChangeCard';

/**
 * Item retornado por GET/PUT /users/me (`_public_row` em `api/app/users.py`):
 * todas as colunas de `users` exceto `password_hash`, mais os campos
 * derivados `has_photo`/`intervals_configured`. `intervals_api_key_encrypted`
 * nunca é exposta pela API.
 */
type ProfileUser = {
  id: number;
  username: string;
  full_name: string | null;
  email: string | null;
  birth_date: string | null;
  // Nutrição L2a: códigos do motor (`domain/nutrition/anthropometrics`), já
  // canônicos na resposta; `null` = não informado.
  sex: string | null;
  height_cm: number | null;
  activity_level: string | null;
  // M98 C1 (migração 096): FCmáx informada pelo usuário (bpm); `null` = não
  // informada. Fonte do fallback das zonas de FC (dono `hr_zones`).
  max_hr_bpm_manual?: number | null;
  role: string;
  is_active: boolean;
  intervals_athlete_id: string | null;
  // M3: unidade em que o usuário anota a carga no Modo Treino. O banco
  // continua gravando `exercise_sets.weight_kg` em QUILOS — isto é só
  // entrada/exibição (migração 040).
  preferred_weight_unit: WeightUnit;
  // F0.1 (migração 044): fuso IANA em que o dashboard resolve "hoje".
  timezone: string;
  has_photo: boolean;
  intervals_configured: boolean;
};

const weightUnitOptions = [
  { value: 'kg', label: 'Quilos (kg)' },
  { value: 'lb', label: 'Libras (lb)' },
];

// Faixa de sanidade da altura: dono `anthropometrics.HEIGHT_CM_RANGE`, via JSON gerado.
const [HEIGHT_MIN_CM, HEIGHT_MAX_CM] = NUTRITION_FORM_RANGES.height_cm;

const MIN_PASSWORD_LENGTH = 8;

export function PerfilClient() {
  const qc = useQueryClient();
  const photoInputRef = useRef<HTMLInputElement>(null);
  const passwordCardRef = useRef<HTMLDivElement>(null);

  // Login com `must_change_password: true` redireciona para
  // `/perfil?troque-senha=1` (ver `src/app/LoginClient.tsx`) — mostra um
  // aviso e leva o usuário direto até o card de troca de senha.
  const searchParams = useSearchParams();
  const forcePasswordChange = searchParams.get('troque-senha') === '1';

  const profileQuery = useQuery({
    queryKey: ['my-profile'],
    queryFn: () => apiFetch<{ item: ProfileUser }>('/users/me'),
  });
  const item = profileQuery.data?.item;

  // --- Dados do perfil -----------------------------------------------
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [birthDate, setBirthDate] = useState('');
  const [sex, setSex] = useState<string | null>(null);
  const [heightCm, setHeightCm] = useState<number | ''>('');
  const [activityLevel, setActivityLevel] = useState<string | null>(null);
  const [maxHr, setMaxHr] = useState<number | ''>('');
  const [intervalsAthleteId, setIntervalsAthleteId] = useState('');
  const [intervalsApiKey, setIntervalsApiKey] = useState('');
  const [weightUnit, setWeightUnit] = useState<WeightUnit>('kg');
  const [timezone, setTimezone] = useState<string>(normalizeTimezone(null));
  const [profileSaved, setProfileSaved] = useState(false);

  useEffect(() => {
    if (!item) return;
    setFullName(item.full_name || '');
    setEmail(item.email || '');
    setBirthDate(item.birth_date ? item.birth_date.slice(0, 10) : '');
    setSex(item.sex || null);
    setHeightCm(item.height_cm ?? '');
    setActivityLevel(item.activity_level || null);
    setMaxHr(item.max_hr_bpm_manual ?? '');
    setIntervalsAthleteId(item.intervals_athlete_id || '');
    setWeightUnit(normalizeWeightUnit(item.preferred_weight_unit));
    setTimezone(normalizeTimezone(item.timezone));
  }, [item]);

  useEffect(() => {
    if (forcePasswordChange && item) {
      passwordCardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [forcePasswordChange, item]);

  const saveProfileMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ status: string; item: ProfileUser }>('/users/me', {
        method: 'PUT',
        body: JSON.stringify({
          full_name: fullName || null,
          email: email || null,
          birth_date: birthDate || null,
          // `null` = não alterar (COALESCE no backend), como os demais campos.
          sex: sex || null,
          // Nutrição L2a: só vão quando preenchidos (ausente = não alterar). Um
          // backend anterior ao L2a (`extra="forbid"`) segue aceitando o resto.
          ...(heightCm === '' ? {} : { height_cm: heightCm }),
          ...(activityLevel ? { activity_level: activityLevel } : {}),
          // M98 C1: só vai quando mudou; vazio = `null`, que LIMPA a FCmáx
          // (diferente dos campos com COALESCE). A faixa é validada pelo dono.
          ...((maxHr === '' ? null : maxHr) !== (item?.max_hr_bpm_manual ?? null)
            ? { max_hr_bpm_manual: maxHr === '' ? null : maxHr }
            : {}),
          intervals_athlete_id: intervalsAthleteId || null,
          preferred_weight_unit: weightUnit,
          timezone,
          // string vazia é tratada como "não alterar" pelo backend (COALESCE
          // com None) — então só envia quando o usuário digitou algo novo.
          intervals_api_key: intervalsApiKey || null,
        }),
      }),
    onSuccess: () => {
      setIntervalsApiKey('');
      setProfileSaved(true);
      // Espelho local lido pelo Modo Treino antes de `GET /users/me` voltar.
      saveStoredWeightUnit(weightUnit);
      qc.invalidateQueries({ queryKey: ['my-profile'] });
      // Faixa de bpm das zonas no editor do Modelo (FCmáx manual, M98 C1).
      qc.invalidateQueries({ queryKey: HR_ZONES_QUERY_KEY });
      // Header do app-shell usa esta queryKey (GET /auth/me) para o nome
      // exibido — precisa refletir imediatamente se full_name mudou.
      qc.invalidateQueries({ queryKey: ['current-user'] });
    },
  });

  function handleSaveProfile(event: FormEvent) {
    event.preventDefault();
    setProfileSaved(false);
    saveProfileMutation.mutate();
  }

  // --- Foto de perfil ---------------------------------------------------
  // GET /users/{id}/photo exige sessão autenticada (cookie httpOnly), então
  // um <img src> direto para a API não funcionaria sem enviar credenciais —
  // em vez disso, busca via apiFetchBlob e usa um object URL.
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;

    async function loadPhoto() {
      if (!item?.id || !item.has_photo) {
        setPhotoUrl(null);
        return;
      }
      try {
        const blob = await apiFetchBlob(`/users/${item.id}/photo`);
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setPhotoUrl(objectUrl);
      } catch {
        if (!cancelled) setPhotoUrl(null);
      }
    }

    loadPhoto();

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [item?.id, item?.has_photo]);

  const uploadPhotoMutation = useMutation({
    mutationFn: async (file: File) => {
      if (!item?.id) throw new Error('Perfil ainda não carregado.');
      const form = new FormData();
      form.append('file', file);
      return apiFetch<{ status: string; item: ProfileUser }>(`/users/${item.id}/photo`, {
        method: 'POST',
        body: form,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-profile'] });
    },
  });

  function handlePhotoSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (file) uploadPhotoMutation.mutate(file);
  }

  // --- Troca de senha -----------------------------------------------
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState(false);

  const changePasswordMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ status: string }>('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      }),
    onSuccess: () => {
      setCurrentPassword('');
      setNewPassword('');
      setPasswordSuccess(true);
      // O backend zerou `must_change_password`: refaz `/auth/me` e o que
      // falhou com 403 enquanto a troca estava pendente.
      void refreshAfterPasswordChange(qc);
    },
  });

  function handleChangePassword(event: FormEvent) {
    event.preventDefault();
    setPasswordSuccess(false);
    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setPasswordError(`A nova senha deve ter no mínimo ${MIN_PASSWORD_LENGTH} caracteres.`);
      return;
    }
    setPasswordError(null);
    changePasswordMutation.mutate();
  }

  return (
    <Stack gap="xl" maw={640}>
      <div>
        <Title order={1} c={BRAND.textPrimary}>
          Meu perfil
        </Title>
        <Text c={BRAND.textMuted} size="md">
          Dados pessoais, foto, integração com o Intervals.icu e dispositivos pareados.
        </Text>
      </div>

      {forcePasswordChange && !passwordSuccess && (
        <Alert color="yellow" icon={<IconLock />} title="Troca de senha obrigatória">
          Você entrou com uma senha temporária. Antes de continuar, digite essa senha temporária no campo
          “Senha temporária” abaixo e defina sua nova senha.
        </Alert>
      )}

      {profileQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando perfil...</Text>
        </Group>
      )}

      {profileQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar perfil">
          {profileQuery.error instanceof ApiError ? profileQuery.error.message : 'Não foi possível consultar a Fitness API.'}
        </Alert>
      )}

      {item && (
        <>
          <Card withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
            <Stack gap="md">
              <Group>
                <Avatar size={72} radius="xl" src={photoUrl ?? undefined} color="brand">
                  {!photoUrl && <IconUser size={32} />}
                </Avatar>
                <Stack gap={4}>
                  <Text fw={700} c={BRAND.textPrimary}>
                    {item.username}
                  </Text>
                  <Badge variant="light" color="brand" size="sm" w="fit-content">
                    {item.role === 'admin' ? 'Administrador' : 'Usuário'}
                  </Badge>
                  <Button
                    size="xs"
                    variant="light"
                    color="brand"
                    leftSection={<IconUpload size={14} />}
                    onClick={() => photoInputRef.current?.click()}
                    loading={uploadPhotoMutation.isPending}
                  >
                    Trocar foto
                  </Button>
                  <input ref={photoInputRef} type="file" accept="image/*" hidden onChange={handlePhotoSelected} />
                </Stack>
              </Group>

              {uploadPhotoMutation.isError && (
                <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao enviar foto">
                  {uploadPhotoMutation.error instanceof ApiError
                    ? uploadPhotoMutation.error.message
                    : 'Não foi possível enviar a foto.'}
                </Alert>
              )}

              <form onSubmit={handleSaveProfile}>
                <Stack gap="md">
                  <TextInput
                    label="Nome completo"
                    value={fullName}
                    onChange={(event) => setFullName(event.currentTarget.value)}
                  />
                  <TextInput
                    label="Email"
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.currentTarget.value)}
                  />
                  <TextInput
                    label="Data de nascimento"
                    type="date"
                    value={birthDate}
                    onChange={(event) => setBirthDate(event.currentTarget.value)}
                  />
                  <Select
                    label="Unidade da carga"
                    description="Como você anota o peso no Modo Treino. O histórico é sempre guardado em quilos e convertido na tela."
                    data={weightUnitOptions}
                    value={weightUnit}
                    onChange={(value) => setWeightUnit(normalizeWeightUnit(value))}
                    allowDeselect={false}
                  />
                  <Select
                    label="Fuso horário"
                    description="Define em que dia os treinos e as janelas do dashboard (7 dias, ciclo) são contados."
                    data={TIMEZONE_OPTIONS}
                    value={timezone}
                    onChange={(value) => setTimezone(normalizeTimezone(value))}
                    allowDeselect={false}
                    searchable
                  />

                  <Divider label="Plano nutricional" labelPosition="left" color={BRAND.border} />
                  <Text size="sm" c={BRAND.textMuted}>
                    Sexo, altura, nível de atividade e data de nascimento são necessários para calcular o plano
                    nutricional.
                  </Text>
                  <Select label="Sexo" data={SEX_OPTIONS} value={sex} onChange={setSex} clearable />
                  <NumberInput
                    label="Altura (cm)"
                    min={HEIGHT_MIN_CM}
                    max={HEIGHT_MAX_CM}
                    decimalScale={1}
                    value={heightCm}
                    onChange={(value) => setHeightCm(value === '' ? '' : Number(value))}
                  />
                  <Select
                    label="Nível de atividade"
                    description="Quanto você se movimenta no dia a dia."
                    data={ACTIVITY_LEVEL_OPTIONS}
                    value={activityLevel}
                    onChange={setActivityLevel}
                    clearable
                  />

                  <Divider label="Cardio" labelPosition="left" color={BRAND.border} />
                  <NumberInput
                    label="FCmáx (bpm)"
                    description="Usada para mostrar a faixa de bpm de cada zona (Z1–Z5) no cardio do Modelo. Deixe em branco se não souber."
                    min={MANUAL_MAX_HR_LIMITS.min}
                    max={MANUAL_MAX_HR_LIMITS.max}
                    allowDecimal={false}
                    allowNegative={false}
                    value={maxHr}
                    onChange={(value) => setMaxHr(value === '' ? '' : Number(value))}
                  />

                  <Divider label="Intervals.icu" labelPosition="left" color={BRAND.border} />

                  <TextInput
                    label="Athlete ID"
                    placeholder="ex.: i123456"
                    value={intervalsAthleteId}
                    onChange={(event) => setIntervalsAthleteId(event.currentTarget.value)}
                  />
                  <PasswordInput
                    label={
                      item.intervals_configured
                        ? 'API Key (configurado ✓ — deixe em branco para manter)'
                        : 'API Key'
                    }
                    placeholder={item.intervals_configured ? '••••••••' : 'Cole sua API Key do Intervals.icu'}
                    value={intervalsApiKey}
                    onChange={(event) => setIntervalsApiKey(event.currentTarget.value)}
                  />

                  {saveProfileMutation.isError && (
                    <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao salvar">
                      {saveProfileMutation.error instanceof ApiError
                        ? saveProfileMutation.error.message
                        : 'Não foi possível salvar o perfil.'}
                    </Alert>
                  )}
                  {profileSaved && !saveProfileMutation.isPending && (
                    <Alert color="green" icon={<IconCheck />}>
                      Perfil atualizado.
                    </Alert>
                  )}

                  <Button type="submit" color="brand" loading={saveProfileMutation.isPending}>
                    Salvar perfil
                  </Button>
                </Stack>
              </form>
            </Stack>
          </Card>

          <DevicesCard />

          <PasswordChangeCard
            cardRef={passwordCardRef}
            forced={forcePasswordChange}
            minLength={MIN_PASSWORD_LENGTH}
            currentPassword={currentPassword}
            newPassword={newPassword}
            onCurrentPasswordChange={setCurrentPassword}
            onNewPasswordChange={setNewPassword}
            onSubmit={handleChangePassword}
            validationError={passwordError}
            error={changePasswordMutation.error}
            pending={changePasswordMutation.isPending}
            success={passwordSuccess}
          />

          <Text size="xs" c={BRAND.textMuted}>
            Por segurança, contas só podem ser desativadas por um administrador — não é possível desativar a
            própria conta por aqui.
          </Text>
        </>
      )}
    </Stack>
  );
}
