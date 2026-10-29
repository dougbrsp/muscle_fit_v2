# Pipeline do mapa corporal (gera `api/app/assets/bodymap/v1/`)

Gera, **uma vez e offline**, a arte do mapa corporal (frente + costas) e as
regiões por slug que o app usa para destacar músculos de forma determinística:
rodapé do card ilustrado (`CARD_LAYOUT_V2`), `GET /workout-plans/{id}/muscle-map.png`
e a web (`frontend/public/bodymap/v1/regions.json`). Em tempo de execução nada
daqui roda: o backend compõe só com o Pillow (`api/app/body_map_render.py`).

Pacote atual: **v1**, "caminho 3" do protótipo de 2026-10-03, aprovado pelo
usuário no mesmo dia com todas as fronteiras como estão. A IA rodou só duas
vezes (arte + segmentação); o resto é determinístico e reproduz o pacote
versionado **byte a byte** (arte WebP, `regions.json` e as 18 máscaras) a partir
dos intermediários do protótipo.

## Requisitos

Só no host, num venv à parte — **nunca** em `api/requirements.txt` nem nas
imagens Docker:

```bash
python -m venv .venv-bodymap
.venv-bodymap/Scripts/python -m pip install -r tools/bodymap/requirements-tools.txt   # Windows
# .venv-bodymap/bin/python -m pip install -r tools/bodymap/requirements-tools.txt     # Linux/macOS
```

Pasta de trabalho (imagens da OpenAI, intermediários, máscaras brutas):
`BODYMAP_WORK` ou, por padrão, `api/scripts/calibration-out/bodymap-work/`
(ignorada pelo git). Rode os scripts de dentro de `tools/bodymap/`.

## Passo a passo

| # | Script | Entrada → saída | Custo |
|---|---|---|---|
| 1 | `oai.py gen --name base --prompt-file prompts/base.txt --quality high` | prompt → `base.png` (gpt-image-1.5, 1536x1024, fundo transparente) | ~US$ 0,22 |
| 2 | `oai.py edit --name segment --prompt-file prompts/segment.txt --image <work>/base.png --quality medium --input-fidelity high` | arte → `segment.png` (mesma figura em cores chapadas, uma por grupo) | ~US$ 0,13 |
| 3 | `segment_components.py` | registra a edição na arte (escala + translação por metade) e separa os componentes coloridos (k-means em Lab) → `segment.components.npy`, `segment.atlas.png` (numerado) | — |
| 4 | `segment_grey.py` | componentes que a IA deixou cinza (ids ≥ 1001) → `segment.grey.npy`, `segment.grey_atlas.png` | — |
| 5 | **rotulagem humana** em `assignment.json` | olhando os dois atlas: componente → slug, sementes `(x, y, raio)` para regiões que ficaram cinza, `_rest` para o corpo sem destaque | ~1 h |
| 6 | `build_masks.py` | componentes + rotulagem → `masks-raw/<slug>.png`, com as fronteiras assentadas nos sulcos da arte (watershed) e `segment.final_overlay.png` para conferir | — |
| 7 | `vectorize.py` | máscaras → `regions.json` (paths SVG por slug, potrace) + IoU vetor × raster | — |
| 8 | `export_assets.py` | `regions.json` + `base.png` → `api/app/assets/bodymap/v1/` (máscaras 1 bit rasterizadas dos paths, WebP q90, `manifest.json` com caixas das vistas, `lum_lo/hi`, `K0/K1/dim` e sha256) | — |
| 9 | `preview.py` | prancha com 6 combinações pelo compositor real do backend → `<work>/preview.png` | — |

Depois do passo 8:

1. copie `api/app/assets/bodymap/v1/regions.json` para
   `frontend/public/bodymap/v1/regions.json` (o teste
   `api/tests/unit/test_body_map_render.py` exige os dois iguais quando o
   arquivo do frontend existe);
2. `cd api && python -m pytest tests/unit/test_body_map_render.py tests/unit/test_muscle_map.py tests/unit/test_exercise_card_composer.py`;
3. arte nova = pasta nova (`v2/`), versão nova no manifest e `LAYOUT_VERSION`
   novo no card (`api/app/exercise_card_composer.py`).

**Custo total de uma regeneração: ~US$ 0,35 de OpenAI + a rotulagem manual.**
`oai.py` registra cada chamada em `<work>/custos.csv` e recusa passar do teto
(`--budget`, padrão US$ 3).

## Por que a rotulagem é manual

A IA não respeita uma paleta de 17 cores: no protótipo, cerca de metade das
regiões veio em outra cor e a dorsal e a lombar vieram cinza. Por isso a
segmentação só fornece **formas** (componentes) e a atribuição componente →
slug é humana, feita uma vez e versionada (`assignment.json`). A segmentação
também não é repetível (2ª rodada: fronteiras a 3–3,6 px de mediana, outra
paleta): gere uma vez e congele. Trocar a arte base exige refazer os passos
2–8.

## Reprodutibilidade (conferida em 2026-10-03)

Com `base.png` = `base_v2_high.png` e `segment.png` =
`path3_segment_medium.png` do protótipo (na máquina de desenvolvimento, fora
do git: `api/scripts/calibration-out/bodymap-20261003-211252/`):

- passo 3 reproduz `components.npy` do protótipo exatamente;
- passos 6–8, a partir do `grey.npy` do protótipo, reproduzem
  `bodymap-base.webp`, `regions.json` e as 18 máscaras **byte a byte**
  (sha256 iguais aos de `manifest.json`);
- o passo 4 foi **reconstruído** depois (no protótipo foi feito inline): nas
  mesmas entradas acha os mesmos 17 componentes cinza, com IoU de ~0,92 na
  união e ~0,8 por componente, e a numeração pode mudar. Numa regeneração
  isso não importa: a rotulagem é refeita a partir do atlas da própria
  execução.

Os caminhos 1 (uma edição por região) e 2 (reestilizar os polígonos antigos),
descartados no protótipo, não estão aqui; os scripts e as métricas deles ficam
na pasta do protótipo. Custo total do protótipo (3 caminhos): US$ 1,92.
