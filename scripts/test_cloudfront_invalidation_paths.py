"""Testes de scripts/cloudfront_invalidation_paths.py (M68). stdlib pura:

    python3 -m unittest discover -s scripts -p "test_cloudfront_invalidation_paths.py"
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cloudfront_invalidation_paths as m  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TF_PATH = os.path.join(REPO, "infra", "cloudfront.tf")

# Raiz real do bucket musclefit-frontend-assets em 2026-10-04 (s3 ls).
REAL_ROOT = {
    "404/", "_next/", "academias/", "agente-cardio/", "bodymap/", "cardio/",
    "dashboard/", "diagnostico-volume/", "dominio-exercicios/",
    "equipamentos-exercicios/", "esqueci-senha/", "evolucao-corporal/",
    "exercicios/", "grupos-musculares/", "indicadores-fisicos/", "insights/",
    "intervals-icu/", "metas-ativacao/", "modelos-treino/", "modo-treino/",
    "perfil/", "planos-treino/", "recuperacao-muscular/", "redefinir-senha/",
    "registro-exercicios/", "relatorios/", "tipos-treinamento/", "treinos/",
    "usuarios/", "volume-grupamentos/", "404.html", "apple-touch-icon.png",
    "icon-192.png", "icon-512.png", "icon.svg", "index.html", "index.txt",
    "logo.svg", "manifest.json",
}

PROTECTED = ["equipment/", "exercise-cards/", "gyms/"]

# Keys de upload realistas que NUNCA podem casar com um path do deploy.
UPLOAD_KEYS = [
    "/equipment/82.png", "/equipment/1.jpg", "/gyms/3.webp",
    "/exercise-cards/12/v3/card.webp", "/exercise-cards/12/v3/thumb.webp",
]


def matches(path: str, key: str) -> bool:
    """Semantica de invalidacao CloudFront: '*' so no final = prefixo."""
    if path.endswith("*"):
        return key.startswith(path[:-1])
    return key == path


class ProtectedFromTf(unittest.TestCase):
    def test_real_cloudfront_tf(self):
        with open(TF_PATH, encoding="utf-8") as f:
            prot = m.protected_prefixes_from_tf(f.read())
        for p in m.REQUIRED_PROTECTED:
            self.assertIn(p, prot)

    def test_missing_required_behavior_fails(self):
        tf = 'ordered_cache_behavior {\n  path_pattern = "/gyms/*"\n}\n'
        with self.assertRaises(m.InvalidationError):
            m.protected_prefixes_from_tf(tf)

    def test_leading_wildcard_pattern_fails(self):
        tf = "".join(
            f'ordered_cache_behavior {{\n  path_pattern = "{p}"\n}}\n'
            for p in ("/equipment/*", "/gyms/*", "/exercise-cards/*", "*.jpg")
        )
        with self.assertRaises(m.InvalidationError):
            m.protected_prefixes_from_tf(tf)


class ComputePaths(unittest.TestCase):
    def setUp(self):
        self.paths = m.compute_paths(REAL_ROOT, PROTECTED)

    def test_no_upload_key_is_invalidated(self):
        for path in self.paths:
            for key in UPLOAD_KEYS:
                self.assertFalse(matches(path, key), f"{path} casa {key}")
            self.assertFalse(path.startswith(("/equipment/", "/gyms/", "/exercise-cards/")))
        self.assertNotIn("/*", self.paths)

    def test_every_frontend_url_is_covered(self):
        urls = ["/", "/index.html", "/index.txt", "/404.html", "/manifest.json",
                "/dashboard/index.html", "/dashboard/treinos/index.html",
                "/exercicios/index.html", "/equipamentos-exercicios/index.html",
                "/esqueci-senha/index.html", "/evolucao-corporal/index.html",
                "/grupos-musculares/index.html", "/planos-treino/placeholder/index.html",
                "/modo-treino/index.txt", "/volume-grupamentos/index.html"]
        for url in urls:
            self.assertTrue(any(matches(p, url) for p in self.paths), url)

    def test_hashed_next_assets_are_not_invalidated(self):
        self.assertFalse(any(matches(p, "/_next/static/chunks/a1b2.js") for p in self.paths))

    def test_shared_prefixes_get_longer_wildcard(self):
        self.assertIn("/exercic*", self.paths)  # exercicios vs exercise-cards
        self.assertIn("/equipa*", self.paths)   # equipamentos vs equipment
        self.assertIn("/gr*", self.paths)       # grupos-musculares vs gyms
        self.assertIn("/a*", self.paths)

    def test_short_and_stable(self):
        self.assertLessEqual(len(self.paths), 25)
        self.assertEqual(self.paths, sorted(self.paths))
        self.assertIn("/", self.paths)

    def test_new_route_is_covered(self):
        paths = m.compute_paths(REAL_ROOT | {"zumba/"}, PROTECTED)
        self.assertTrue(any(matches(p, "/zumba/index.html") for p in paths))

    def test_file_that_prefixes_protected_uses_exact_path(self):
        paths = m.compute_paths({"index.html", "gym"}, PROTECTED)
        self.assertIn("/gym", paths)
        self.assertFalse(any(matches(p, "/gyms/3.webp") for p in paths))

    def test_frontend_entry_shadowed_by_uploads_fails(self):
        with self.assertRaises(m.InvalidationError):
            m.compute_paths({"index.html", "gyms/"}, PROTECTED)

    def test_unexpected_name_fails(self):
        with self.assertRaises(m.InvalidationError):
            m.compute_paths({"index.html", "a b/"}, PROTECTED)


class Verify(unittest.TestCase):
    def test_rejects_global_wildcard_and_uploads(self):
        for bad in (["/*"], ["/e*"], ["/g*"], ["/equipment/82.png"], ["/exercise*"], ["/a*b"]):
            with self.assertRaises(m.InvalidationError, msg=bad):
                m.verify_paths(bad, PROTECTED)


class Batches(unittest.TestCase):
    """Hotfix M68 (run 37227083220): cota de 15 paths com wildcard em andamento."""

    def _wild(self, batch):
        return sum(1 for p in batch if p.endswith("*"))

    def test_real_root_becomes_two_batches(self):
        paths = m.compute_paths(REAL_ROOT, PROTECTED)
        self.assertEqual(len(paths), 19)
        batches = m.batch_paths(paths)
        self.assertEqual(len(batches), 2)
        self.assertEqual([self._wild(b) for b in batches], [15, 3])
        self.assertEqual(batches[0][0], "/")
        # nada perdido nem duplicado, ordem estavel
        self.assertEqual(sorted(p for b in batches for p in b), paths)
        self.assertEqual([p for b in batches for p in b], paths)

    def test_never_more_than_15_wildcards(self):
        for n in range(0, 61):
            paths = ["/"] + [f"/w{i:03d}*" for i in range(n)]
            batches = m.batch_paths(paths)
            self.assertTrue(batches)
            for b in batches:
                self.assertLessEqual(self._wild(b), m.MAX_WILDCARDS_PER_BATCH)
                self.assertTrue(b)
            self.assertEqual([p for b in batches for p in b], paths)
            self.assertEqual(len(batches), max(1, -(-n // 15)))

    def test_exact_paths_do_not_count_and_go_first(self):
        paths = ["/", "/gym"] + [f"/w{i:02d}*" for i in range(15)]
        batches = m.batch_paths(paths)
        self.assertEqual(len(batches), 1)
        self.assertIn("/", batches[0])
        self.assertIn("/gym", batches[0])

    def test_stable(self):
        paths = m.compute_paths(REAL_ROOT, PROTECTED)
        self.assertEqual(m.batch_paths(paths), m.batch_paths(list(paths)))

    def test_custom_limit(self):
        batches = m.batch_paths(["/", "/a*", "/b*", "/c*"], max_wildcards=2)
        self.assertEqual(batches, [["/", "/a*", "/b*"], ["/c*"]])
        with self.assertRaises(m.InvalidationError):
            m.batch_paths(["/a*"], max_wildcards=0)


class Cli(unittest.TestCase):
    def test_union_of_out_dir_and_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            os.makedirs(os.path.join(out, "dashboard"))
            os.makedirs(os.path.join(out, "_next", "static"))
            open(os.path.join(out, "index.html"), "w").close()
            listing = os.path.join(tmp, "bucket.json")
            with open(listing, "w") as f:
                json.dump({"CommonPrefixes": [{"Prefix": "rota-removida/"}],
                           "Contents": [{"Key": "index.html"}]}, f)
            empty = os.path.join(tmp, "empty.json")
            open(empty, "w").close()

            import io
            from contextlib import redirect_stderr, redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                rc = m.main(["--out-dir", out, "--bucket-listing", listing,
                             "--cloudfront-tf", TF_PATH])
            self.assertEqual(rc, 0)
            self.assertEqual(buf.getvalue().split(), ["/", "/d*", "/i*", "/r*"])

            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                rc = m.main(["--out-dir", out, "--bucket-listing", empty,
                             "--cloudfront-tf", TF_PATH])
            self.assertEqual(rc, 0)
            self.assertEqual(buf.getvalue().split(), ["/", "/d*", "/i*"])

            batches_file = os.path.join(tmp, "batches.txt")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = m.main(["--out-dir", out, "--bucket-listing", listing,
                             "--cloudfront-tf", TF_PATH, "--batches-out", batches_file])
            self.assertEqual(rc, 0)
            with open(batches_file, encoding="utf-8") as f:
                self.assertEqual(f.read(), "/ /d* /i* /r*\n")


if __name__ == "__main__":
    unittest.main()
