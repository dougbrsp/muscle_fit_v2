"""CRUD de usuários (Parte 1 da migração multiusuário).

Reaproveita o mesmo padrão de upload de foto já usado em app.gyms (equipment)
em vez de duplicar a lógica: _save_photo/_read_photo/_delete_photo (S3,
bucket musclefit-uploads).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from . import athlete_hr_zones
from .auth import MIN_PASSWORD_LENGTH, generate_temp_password, hash_password
from .crypto_utils import encrypt_secret, is_encrypted
from .db import transaction
from .db_helpers import jsonable as _jsonable
from .domain import hr_zones
from .domain.nutrition import anthropometrics as anthro
from .gyms import _EXT_TO_CONTENT_TYPE, _delete_photo, _read_photo, _save_photo
from .observability import log_event
from .request_models import UserCreateIn, UserProfileUpdateIn, UserUpdateIn
from .security import Identity, get_current_identity, require_admin
from .timezones import is_valid_timezone, normalize_timezone
from .weight_units import WEIGHT_UNITS, normalize_weight_unit

router = APIRouter(prefix="/users", tags=["users"])
logger = logging.getLogger("fitness.users")

# Extensoes que _save_photo grava (gyms.PHOTO_CONTENT_TYPES).
_USER_PHOTO_EXTS = tuple(ext.lstrip(".") for ext in _EXT_TO_CONTENT_TYPE) + ("jpeg",)


def _user_photo_key_is_canonical(user_id: int, key: Any) -> bool:
    """SEG-01: a chave S3 da foto de perfil so pode ser `users/{id}.{ext}` do
    proprio usuario (a mesma que _save_photo grava). Qualquer outra coisa
    vinda do banco nao e lida nem apagada no bucket compartilhado."""
    if not isinstance(key, str):
        return False
    pattern = r"users/%d\.(?:%s)" % (int(user_id), "|".join(_USER_PHOTO_EXTS))
    return re.fullmatch(pattern, key) is not None

# Papéis atribuíveis pelo CRUD. `personal_trainer` existe no ENUM desde a 007 e
# em `require_admin_or_pt`; passou a ser atribuível no L0 do plano de nutrição
# (2026-10-15; o vínculo PT<->aluno fica para o L11).
_ALLOWED_ROLES = {"admin", "regular", "personal_trainer"}
_ROLE_ERROR = "role deve ser um de: " + ", ".join(f"'{r}'" for r in sorted(_ALLOWED_ROLES)) + "."
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(raw: Any) -> Optional[str]:
    """Valida `email` de forma simples (regex básica). Campo opcional:
    None/string vazia passam direto (permitem COALESCE preservar o valor já
    gravado em updates parciais). String não vazia que não bate com o
    formato mínimo de email retorna 400."""
    if raw in (None, ""):
        return None
    value = str(raw).strip()
    if not value:
        return None
    if not _EMAIL_RE.match(value):
        raise HTTPException(status_code=400, detail="email inválido.")
    return value


def _validate_weight_unit(raw: Any) -> Optional[str]:
    """Valida `preferred_weight_unit` (M3): unidade em que o usuário ANOTA a
    carga no Modo Treino. `None`/string vazia passam direto (deixam o COALESCE
    preservar o valor já gravado em updates parciais). Qualquer outro valor
    fora de {'kg','lb'} é 400 — e NUNCA silenciosamente trocado por 'kg', para
    um typo do cliente não virar "sua preferência sumiu".

    O banco continua gravando `exercise_sets.weight_kg` em QUILOS (D12): esta
    coluna é só de entrada/exibição (ver `app/weight_units.py`)."""
    if raw in (None, ""):
        return None
    value = str(raw).strip().lower()
    if value not in WEIGHT_UNITS:
        raise HTTPException(
            status_code=400,
            detail=f"preferred_weight_unit deve ser {' ou '.join(repr(u) for u in WEIGHT_UNITS)}.",
        )
    return value


def _validate_timezone(raw: Any) -> Optional[str]:
    """Valida `timezone` (migração 044, Novo Dashboard F0.1): nome IANA em
    que a data "hoje" do usuário é resolvida (`intelligence/dashboard/
    time_windows.py`). `None`/string vazia passam direto (COALESCE preserva o
    valor gravado em updates parciais). Nome desconhecido pelo `zoneinfo` é
    400 — nunca silenciosamente trocado pelo default."""
    if raw in (None, ""):
        return None
    value = str(raw).strip()
    if not is_valid_timezone(value):
        raise HTTPException(
            status_code=400,
            detail="timezone inválido: use um nome IANA (ex.: 'America/Sao_Paulo', 'America/Manaus', 'UTC').",
        )
    return value


def _profile_error(code: str, message: str) -> HTTPException:
    """400 da validação do perfil antropométrico com o código do motor."""
    return HTTPException(status_code=400, detail={"error": code, "message": message})


def _codes_list(enum_cls: Any) -> str:
    return ", ".join(member.value for member in enum_cls)


def _validate_sex(raw: Any) -> Optional[str]:
    """`sex` (nutrição L2a): grava SEMPRE o código canônico do motor
    (`anthropometrics.sex_code`; aceita os apelidos antigos como 'M'/'F').
    `None`/vazio -> não altera (COALESCE). Fora do vocabulário -> 400
    `invalid_sex`."""
    if raw is None or not str(raw).strip():
        return None
    code = anthro.sex_code(raw)
    if code is None:
        raise _profile_error(anthro.INVALID_SEX, f"sex inválido: use um dos códigos {_codes_list(anthro.Sex)}.")
    return code


def _validate_activity_level(raw: Any) -> Optional[str]:
    """`activity_level` (nutrição L2a): código canônico do motor ou 400
    `invalid_activity_level`. `None`/vazio -> não altera (COALESCE)."""
    if raw is None or not str(raw).strip():
        return None
    code = anthro.activity_level_code(raw)
    if code is None:
        raise _profile_error(
            anthro.INVALID_ACTIVITY_LEVEL,
            f"activity_level inválido: use um dos códigos {_codes_list(anthro.ActivityLevel)}.",
        )
    return code


def _validate_height_cm(raw: Any) -> Optional[float]:
    """`height_cm` (nutrição L2a): dentro de `anthropometrics.HEIGHT_CM_RANGE`
    (`height_is_valid`) ou 400 `invalid_height_cm`. `None` -> não altera."""
    if raw is None:
        return None
    if not anthro.height_is_valid(raw):
        low, high = anthro.HEIGHT_CM_RANGE
        raise _profile_error(anthro.INVALID_HEIGHT, f"height_cm deve estar entre {low:g} e {high:g} cm.")
    return float(raw)


def _anthropometric_set(height_cm: Optional[float], activity_level: Optional[str]) -> tuple[str, tuple]:
    """Trecho extra do SET para as colunas da 074, só com o que veio preenchido.
    `None` = não altera (o mesmo que o COALESCE dos demais campos), e assim
    salvar o resto do perfil não depende da 074 aplicada."""
    candidates = (("height_cm", height_cm), ("activity_level", activity_level))
    present = [(column, value) for column, value in candidates if value is not None]
    return "".join(f", {column} = %s" for column, _ in present), tuple(value for _, value in present)


# M98 C1 (migração 096): FCmáx manual, fonte do fallback das zonas de FC.
MAX_HR_FIELD = athlete_hr_zones.MAX_HR_FIELD


def _max_hr_set(payload: Any) -> tuple[str, tuple]:
    """Trecho extra do SET para `max_hr_bpm_manual`, só quando o campo VEIO no
    payload (`model_fields_set`). Diferente dos demais (COALESCE), `null`
    explícito LIMPA a FCmáx: sem isso o usuário não teria como voltar a "sem
    zonas". Ausente = não altera, e salvar o resto do perfil não depende da 096.
    A faixa aceita é do dono (`hr_zones.validate_manual_max_hr`); fora dela, 400."""
    if MAX_HR_FIELD not in payload.model_fields_set:
        return "", ()
    try:
        value = hr_zones.validate_manual_max_hr(getattr(payload, MAX_HR_FIELD))
    except hr_zones.HrZonesError as exc:
        raise _profile_error("invalid_max_hr_bpm_manual", str(exc)) from exc
    return f", {MAX_HR_FIELD} = %s", (value,)


def _public_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Serializa uma linha de `users` para resposta, removendo segredos
    (password_hash, chave crua/"criptografada" do Intervals) e expondo
    apenas `intervals_configured`."""
    result = {key: _jsonable(value) for key, value in row.items() if key != "password_hash"}
    result["has_photo"] = bool(result.pop("photo_path", None))
    # TINYINT(1) vem do pymysql como int (0/1) — expõe como bool real, como
    # o plano de "usuário avançado" exige (`GET /users/me` -> `true`/`false`).
    result["is_advanced_user"] = bool(result.get("is_advanced_user"))
    # M3: sempre presente e sempre válida na resposta, mesmo para uma linha
    # anterior à migração 040 (coluna ausente) — o cliente nunca precisa
    # tratar "sem preferência".
    result["preferred_weight_unit"] = normalize_weight_unit(row.get("preferred_weight_unit"))
    # F0.1: idem para o fuso (migração 044) — linha anterior à migração ou
    # valor corrompido cai no default, nunca em `null`.
    result["timezone"] = normalize_timezone(row.get("timezone"))
    # Nutrição L2a: código canônico do motor, normalizado também na leitura
    # (valor antigo como 'M' -> 'male'; desconhecido -> null) enquanto a 077 não
    # tiver convertido a linha. Linha anterior à 074 -> null.
    result["sex"] = anthro.sex_code(row.get("sex"))
    result["height_cm"] = _jsonable(row.get("height_cm"))
    result["activity_level"] = anthro.activity_level_code(row.get("activity_level"))
    # M98 C1: linha anterior à 096 -> null.
    result[MAX_HR_FIELD] = _jsonable(row.get(MAX_HR_FIELD))
    result["intervals_configured"] = bool(row.get("intervals_athlete_id")) and bool(
        row.get("intervals_api_key_encrypted")
    )
    result.pop("intervals_api_key_encrypted", None)
    return result


def _encrypt_intervals_api_key(raw: Any) -> Optional[str]:
    """Criptografa `intervals_api_key` (string crua vinda do payload) antes
    de persistir em `intervals_api_key_encrypted`. `None`/vazio passa direto
    (permite COALESCE preservar o valor já gravado quando o campo não vem no
    payload de update)."""
    if raw in (None, ""):
        return None
    return encrypt_secret(str(raw))


def migrate_plaintext_intervals_keys() -> int:
    """Migração defensiva e idempotente (Parte 2): antes desta entrega,
    `users.intervals_api_key_encrypted` podia ter sido gravado em TEXTO PURO
    (Parte 1 criou a coluna como staging temporário, sem criptografia real -
    ver histórico em `create_user`/`update_my_profile`/`update_user` acima).
    Se algum teste/uso manual já tiver gravado uma chave nesse formato antigo,
    esta função tenta descriptografar cada linha não-nula existente; se
    falhar (ou seja, não está no formato Fernet, logo é texto puro antigo),
    criptografa de verdade e regrava. Chamada no startup do servidor (ver
    `app/main.py`), nunca lança - uma falha aqui não pode derrubar o boot da
    API. Roda em toda inicialização, mas linhas já criptografadas são
    ignoradas (`is_encrypted` retorna True e nada é regravado), então é
    seguro rodar repetidamente."""
    migrated = 0
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, intervals_api_key_encrypted FROM users "
                    "WHERE intervals_api_key_encrypted IS NOT NULL AND intervals_api_key_encrypted != ''"
                )
                rows = cur.fetchall()

                for row in rows:
                    current_value = row["intervals_api_key_encrypted"]
                    if is_encrypted(current_value):
                        continue

                    # Não está em formato Fernet válido: assume texto puro
                    # gravado antes desta entrega, criptografa de verdade.
                    cur.execute(
                        "UPDATE users SET intervals_api_key_encrypted = %s WHERE id = %s",
                        (encrypt_secret(current_value), row["id"]),
                    )
                    migrated += 1
    except Exception:
        import logging

        logging.getLogger("fitness.users").exception(
            "Falha ao migrar intervals_api_key_encrypted em texto puro para criptografado."
        )

    return migrated


def require_session_identity(identity: Identity = Depends(get_current_identity)) -> Identity:
    """Exige uma sessão de usuário real (login), rejeitando token mestre e
    token de dispositivo — usada em endpoints de perfil pessoal (/users/me)
    que não fazem sentido para o Zepp/MCP."""
    if identity.source != "session":
        raise HTTPException(
            status_code=403,
            detail="Este endpoint exige uma sessão de usuário autenticado via login.",
        )
    return identity


def _require_self_or_admin(identity: Identity, user_id: int) -> None:
    if identity.role == "admin" or identity.user_id == user_id:
        return
    raise HTTPException(status_code=403, detail="Sem permissão para alterar a foto deste usuário.")


@router.get("")
def list_users(identity: Identity = Depends(require_admin)):
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users ORDER BY username")
            rows = cur.fetchall()

    return {"items": [_public_row(row) for row in rows]}


@router.post("")
def create_user(payload: UserCreateIn, identity: Identity = Depends(require_admin)):
    item = payload.model_dump()
    username = str(item.get("username") or "").strip()
    password = str(item.get("password") or "")
    role = str(item.get("role") or "regular")

    if not username:
        raise HTTPException(status_code=400, detail="username é obrigatório.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"password deve ter ao menos {MIN_PASSWORD_LENGTH} caracteres.")
    if role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail=_ROLE_ERROR)

    password_hash = hash_password(password)

    intervals_api_key = _encrypt_intervals_api_key(item.get("intervals_api_key"))
    email = _validate_email(item.get("email"))
    sex = _validate_sex(item.get("sex"))

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="username já está em uso.")

            cur.execute(
                """
                INSERT INTO users (
                  username, password_hash, full_name, email, birth_date, sex,
                  role, intervals_athlete_id, is_advanced_user, intervals_api_key_encrypted
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    username,
                    password_hash,
                    item.get("full_name"),
                    email,
                    item.get("birth_date"),
                    sex,
                    role,
                    item.get("intervals_athlete_id"),
                    bool(item.get("is_advanced_user", False)),
                    intervals_api_key,
                ),
            )
            user_id = cur.lastrowid
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()

    return {"status": "ok", "item": _public_row(row)}


@router.get("/me")
def get_my_profile(identity: Identity = Depends(require_session_identity)):
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (identity.user_id,))
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    return {"item": _public_row(row)}


@router.get("/me/hr-zones")
def get_my_hr_zones(identity: Identity = Depends(require_session_identity)):
    """Zonas de FC do usuário logado resolvidas pelo dono (`hr_zones`), prontas
    para exibir (perfil). A leitura e a precedência ficam em
    `athlete_hr_zones.load_athlete_zones`; o editor do Modelo usa
    `GET /workout-plan-templates/hr-zones`, que decide DE QUEM são as zonas."""
    with transaction() as conn:
        with conn.cursor() as cur:
            athlete = athlete_hr_zones.load_athlete_zones(cur, identity.user_id)

    if athlete is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    return athlete_hr_zones.zones_payload(athlete)


@router.put("/me")
def update_my_profile(payload: UserProfileUpdateIn, identity: Identity = Depends(require_session_identity)):
    item = payload.model_dump()
    # NAO permite alterar role/username/is_active da própria conta aqui.
    email = _validate_email(item.get("email"))
    preferred_weight_unit = _validate_weight_unit(item.get("preferred_weight_unit"))
    timezone_name = _validate_timezone(item.get("timezone"))
    sex = _validate_sex(item.get("sex"))
    anthropometric_sql, anthropometric_params = _anthropometric_set(
        _validate_height_cm(item.get("height_cm")), _validate_activity_level(item.get("activity_level"))
    )
    max_hr_sql, max_hr_params = _max_hr_set(payload)
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE id = %s", (identity.user_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Usuário não encontrado.")

            cur.execute(
                f"""
                UPDATE users
                SET full_name = COALESCE(%s, full_name),
                    email = COALESCE(%s, email),
                    birth_date = COALESCE(%s, birth_date),
                    sex = COALESCE(%s, sex),
                    intervals_athlete_id = COALESCE(%s, intervals_athlete_id),
                    is_advanced_user = COALESCE(%s, is_advanced_user),
                    preferred_weight_unit = COALESCE(%s, preferred_weight_unit),
                    timezone = COALESCE(%s, timezone),
                    intervals_api_key_encrypted = COALESCE(%s, intervals_api_key_encrypted){anthropometric_sql}{max_hr_sql}
                WHERE id = %s
                """,
                (
                    item.get("full_name"),
                    email,
                    item.get("birth_date"),
                    sex,
                    item.get("intervals_athlete_id"),
                    item.get("is_advanced_user"),
                    preferred_weight_unit,
                    timezone_name,
                    _encrypt_intervals_api_key(item.get("intervals_api_key")),
                    *anthropometric_params,
                    *max_hr_params,
                    identity.user_id,
                ),
            )
            cur.execute("SELECT * FROM users WHERE id = %s", (identity.user_id,))
            row = cur.fetchone()

    return {"status": "ok", "item": _public_row(row)}


@router.put("/{user_id}")
def update_user(user_id: int, payload: UserUpdateIn, identity: Identity = Depends(require_admin)):
    item = payload.model_dump()
    role = item.get("role")
    if role is not None and role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail=_ROLE_ERROR)
    email = _validate_email(item.get("email"))
    preferred_weight_unit = _validate_weight_unit(item.get("preferred_weight_unit"))
    timezone_name = _validate_timezone(item.get("timezone"))
    # Mesmo perfil antropométrico do PUT /users/me (UserUpdateIn herda os campos).
    sex = _validate_sex(item.get("sex"))
    anthropometric_sql, anthropometric_params = _anthropometric_set(
        _validate_height_cm(item.get("height_cm")), _validate_activity_level(item.get("activity_level"))
    )
    max_hr_sql, max_hr_params = _max_hr_set(payload)

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Usuário não encontrado.")

            cur.execute(
                f"""
                UPDATE users
                SET full_name = COALESCE(%s, full_name),
                    email = COALESCE(%s, email),
                    birth_date = COALESCE(%s, birth_date),
                    sex = COALESCE(%s, sex),
                    role = COALESCE(%s, role),
                    is_active = COALESCE(%s, is_active),
                    intervals_athlete_id = COALESCE(%s, intervals_athlete_id),
                    is_advanced_user = COALESCE(%s, is_advanced_user),
                    preferred_weight_unit = COALESCE(%s, preferred_weight_unit),
                    timezone = COALESCE(%s, timezone),
                    intervals_api_key_encrypted = COALESCE(%s, intervals_api_key_encrypted){anthropometric_sql}{max_hr_sql}
                WHERE id = %s
                """,
                (
                    item.get("full_name"),
                    email,
                    item.get("birth_date"),
                    sex,
                    role,
                    item.get("is_active"),
                    item.get("intervals_athlete_id"),
                    item.get("is_advanced_user"),
                    preferred_weight_unit,
                    timezone_name,
                    _encrypt_intervals_api_key(item.get("intervals_api_key")),
                    *anthropometric_params,
                    *max_hr_params,
                    user_id,
                ),
            )
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()

    return {"status": "ok", "item": _public_row(row)}


@router.delete("/{user_id}")
def deactivate_user(user_id: int, identity: Identity = Depends(require_admin)):
    if identity.user_id == user_id:
        raise HTTPException(status_code=400, detail="Você não pode desativar a própria conta.")

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET is_active = 0 WHERE id = %s", (user_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    return {"status": "ok", "id": user_id, "is_active": False}


@router.post("/{user_id}/reset-password")
def reset_user_password(user_id: int, identity: Identity = Depends(require_admin)):
    """Reset administrativo de senha (dívida técnica: antes desta rota, a
    única saída para um usuário sem acesso era um `UPDATE` manual no banco).

    Regras:
    - admin-only (`require_admin`), e um admin NÃO reseta a própria senha por
      aqui - para isso existe `POST /auth/change-password`;
    - a senha nova é GERADA PELO SERVIDOR e devolvida uma única vez no corpo
      da resposta; o chamador não escolhe a senha e ela nunca é logada nem
      persistida em claro;
    - marca `must_change_password = 1` (o usuário é obrigado a trocar no
      primeiro login), zera `failed_login_attempts` e limpa `locked_until`;
    - revoga TODAS as sessões ativas do usuário: trocar a senha sem derrubar
      as sessões abertas deixaria um invasor logado.
    """
    if identity.user_id == user_id:
        raise HTTPException(
            status_code=400,
            detail="Você não pode resetar a própria senha por aqui — use a troca de senha em 'Meu perfil'.",
        )

    new_password = generate_temp_password()
    password_hash = hash_password(new_password)

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                # Só leitura até aqui: nada a perder no rollback.
                raise HTTPException(status_code=404, detail="Usuário não encontrado.")

            cur.execute(
                """
                UPDATE users
                SET password_hash = %s,
                    must_change_password = 1,
                    failed_login_attempts = 0,
                    locked_until = NULL
                WHERE id = %s
                """,
                (password_hash, user_id),
            )

            cur.execute(
                "UPDATE user_sessions SET revoked_at = CURRENT_TIMESTAMP "
                "WHERE user_id = %s AND revoked_at IS NULL",
                (user_id,),
            )
            revoked_sessions = int(cur.rowcount or 0)

    # Auditoria: só ids numéricos e contagem - nunca a senha, nunca dado
    # pessoal (regra de conteúdo de observability.log_event).
    log_event(
        "admin.password_reset",
        actor_user_id=identity.user_id,
        target_user_id=user_id,
        actor_source=identity.source,
        revoked_sessions=revoked_sessions,
    )

    return {
        "status": "ok",
        "id": user_id,
        "username": row["username"],
        # Mostrada UMA vez: não há como recuperá-la depois (o banco guarda
        # apenas o hash bcrypt).
        "temporary_password": new_password,
        "must_change_password": True,
        "revoked_sessions": revoked_sessions,
    }


@router.post("/{user_id}/photo")
def upload_user_photo(
    user_id: int,
    file: UploadFile = File(...),
    identity: Identity = Depends(get_current_identity),
):
    _require_self_or_admin(identity, user_id)

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Usuário não encontrado.")

            relative_path = _save_photo("users", user_id, "users", file)

            cur.execute("UPDATE users SET photo_path = %s WHERE id = %s", (relative_path, user_id))
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()

    return {"status": "ok", "item": _public_row(row)}


@router.get("/{user_id}/photo")
def get_user_photo(user_id: int, identity: Identity = Depends(get_current_identity)):
    _require_self_or_admin(identity, user_id)

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT photo_path FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()

    if not row or not row.get("photo_path"):
        raise HTTPException(status_code=404, detail="Este usuário não possui foto.")

    if not _user_photo_key_is_canonical(user_id, row["photo_path"]):
        logger.warning("photo_path nao canonico para user_id=%s; leitura recusada.", user_id)
        raise HTTPException(status_code=404, detail="Este usuário não possui foto.")

    return _read_photo(row["photo_path"])


@router.delete("/{user_id}/photo")
def delete_user_photo(user_id: int, identity: Identity = Depends(get_current_identity)):
    _require_self_or_admin(identity, user_id)

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT photo_path FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Usuário não encontrado.")

            if row.get("photo_path"):
                if _user_photo_key_is_canonical(user_id, row["photo_path"]):
                    _delete_photo(row["photo_path"])
                else:
                    # Nunca apaga objeto fora de users/{id}.*; apenas limpa a coluna.
                    logger.warning("photo_path nao canonico para user_id=%s; objeto S3 preservado.", user_id)

            cur.execute("UPDATE users SET photo_path = NULL WHERE id = %s", (user_id,))

    return {"status": "ok", "id": user_id, "has_photo": False}
