"""`app/muscle_map.render_muscle_map_png` e `GET /workout-plans/{id}/muscle-map.png`
com o mapa corporal v1 (desde 2026-10-03): mesma assinatura e contrato (PNG
RGB; 2 = principal, 1 = secundario), arte nova.

Rodar: cd api && python -m pytest tests/unit/test_muscle_map.py
"""

from __future__ import annotations

import inspect
import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import body_map_render as bm
from app import muscle_map


def _open(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _orange(image: Image.Image) -> float:
    """Soma de (vermelho - azul): 0 no grafite e no fundo, alta no #FF9500."""
    red, _, blue = image.convert("RGB").split()
    hist_r, hist_b = red.histogram(), blue.histogram()
    return sum(v * c for v, c in enumerate(hist_r)) - sum(v * c for v, c in enumerate(hist_b))


def test_assinatura_preservada():
    params = inspect.signature(muscle_map.render_muscle_map_png).parameters
    assert list(params) == ["slug_intensity", "scale"]
    assert params["scale"].default == 2.0


def test_devolve_png_rgb_com_fundo_do_painel():
    image = _open(muscle_map.render_muscle_map_png({"chest": 2, "triceps": 1}))
    assert (image.format, image.mode) == ("PNG", "RGB")
    w, h = bm.native_size()
    assert image.width == 420  # a mesma largura do PNG antigo (scale 2)
    pad = muscle_map.PADDING_PER_SCALE * 2
    assert abs(image.height - (2 * pad + (420 - 2 * pad) * h / w)) <= 1
    assert image.getpixel((0, 0)) == image.getpixel((image.width - 1, image.height - 1)) == (20, 20, 20)


@pytest.mark.parametrize("scale, width", [(1.0, 210), (2.0, 420), (3.0, 630)])
def test_scale_multiplica_a_largura(scale, width):
    assert _open(muscle_map.render_muscle_map_png({}, scale)).width == width


@pytest.mark.parametrize("scale", [0, -1, float("nan"), float("inf")])
def test_scale_invalido_e_recusado(scale):
    with pytest.raises(ValueError):
        muscle_map.render_muscle_map_png({}, scale)


def test_principal_acende_mais_que_secundario():
    nada = _orange(_open(muscle_map.render_muscle_map_png({})))
    sec = _orange(_open(muscle_map.render_muscle_map_png({"chest": 1})))
    pri = _orange(_open(muscle_map.render_muscle_map_png({"chest": 2})))
    assert nada < sec < pri
    # intensidade acima de 2 = principal; 0 e negativa = sem destaque
    assert muscle_map.render_muscle_map_png({"chest": 3}) == muscle_map.render_muscle_map_png({"chest": 2})
    assert muscle_map.render_muscle_map_png({"chest": 0, "triceps": -1}) == muscle_map.render_muscle_map_png({})


def test_slug_desconhecido_ou_sem_regiao_e_ignorado():
    vazio = muscle_map.render_muscle_map_png({})
    assert muscle_map.render_muscle_map_png({"knees": 2, "head": 1, "nao-existe": 2}) == vazio
    assert muscle_map.render_muscle_map_png({"abductors": 2}) == muscle_map.render_muscle_map_png({"adductor": 2})


def test_deterministico():
    entrada = {"quadriceps": 2, "gluteal": 1, "calves": 1}
    assert muscle_map.render_muscle_map_png(entrada) == muscle_map.render_muscle_map_png(dict(reversed(entrada.items())))


def test_poligonos_antigos_sairam():
    for nome in ("ANTERIOR_POLYGONS", "POSTERIOR_POLYGONS", "VIEWBOX_W"):
        assert not hasattr(muscle_map, nome)


# ---------------------------------------------------------------------------
# Rota (sem banco: services falsos)
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    from app import services, workout_plans
    from app.security import Identity, get_current_identity

    planos = {5: {"id": 5, "exercises": [{"muscle_primary": "Peitoral", "muscle_secondary": "Tríceps"}]}}
    monkeypatch.setattr(services, "get_workout_plan", lambda plan_id, user_id: planos.get(plan_id))
    monkeypatch.setattr(services, "resolve_plan_muscle_slugs", lambda exercises: {"chest": 2, "triceps": 1})
    app = FastAPI()
    app.include_router(workout_plans.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=1, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def test_rota_devolve_o_png_do_mapa_novo(client):
    resposta = client.get("/workout-plans/5/muscle-map.png")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "image/png"
    assert resposta.content == muscle_map.render_muscle_map_png({"chest": 2, "triceps": 1})


def test_rota_plano_inexistente_404(client):
    assert client.get("/workout-plans/999/muscle-map.png").status_code == 404
