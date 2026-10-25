"""Pruebas de --clasificar / --adoptar / --ignorar. Usan solo carpetas temporales: no tocan ninguna config real.
Ejecutar:  python3 -m unittest tests/test_catalogo.py -v   (o  python3 test_catalogo.py)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

AQUI = Path(__file__).resolve().parent
IEE = next(p for p in (AQUI / "iee.py", AQUI.parent / "iee.py") if p.exists())
spec = importlib.util.spec_from_file_location("iee", IEE)
iee = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iee)

CONFIG = """# Config de prueba (los comentarios deben sobrevivir)
proyectos_conocidos:
  - app-uno   # bot principal
  - app-dos

  # comentario dentro del bloque
raiz_esperados:
  - scripts
  - _inbox
  # NOTA: esta línea de cierre también debe sobrevivir
excluir_dirs:
  - node_modules
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name) / "raiz"
        self.raiz.mkdir()
        for n in ("app-uno", "app-dos", "scripts", "_inbox", "servicio-nuevo", "app.bak-2026", "con datos", "con-env", "normal", "Comercialización"):
            (self.raiz / n).mkdir()
        (self.raiz / "con datos" / "base.db").write_text("x")
        (self.raiz / "con-env" / ".env").write_text("A=1")
        (self.raiz / "servicio-nuevo" / "run.sh").write_text("echo")
        (self.raiz / "normal" / "a.py").write_text("print(1)")
        self.cfg = Path(self.tmp.name) / "iee.config.yml"
        self.cfg.write_text(CONFIG, encoding="utf-8")
        os.chmod(self.cfg, 0o640)

    def tearDown(self):
        self.tmp.cleanup()

    def correr(self, *args):
        r = subprocess.run([sys.executable, str(IEE), "--path", str(self.raiz), "--config", str(self.cfg), *args],
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def yaml_cfg(self):
        import yaml
        return yaml.safe_load(self.cfg.read_text(encoding="utf-8"))


class TestAdoptarIgnorar(Base):
    def test_adoptar_agrega_una_linea_y_conserva_todo_lo_demas(self):
        antes = self.cfg.read_text(encoding="utf-8").split("\n")
        rc, out = self.correr("--adoptar", "normal", "--motivo", "servicio de prueba")
        self.assertEqual(rc, 0, out)
        despues = self.cfg.read_text(encoding="utf-8").split("\n")
        self.assertEqual(len(despues), len(antes) + 1)
        nuevas = [l for l in despues if l not in antes]
        self.assertEqual(len(nuevas), 1)
        self.assertIn("- normal", nuevas[0])
        self.assertIn("servicio de prueba", nuevas[0])
        for l in antes:                       # nada se perdió: ni comentarios ni orden
            self.assertIn(l, despues)
        self.assertIn("normal", self.yaml_cfg()["proyectos_conocidos"])
        self.assertEqual(self.cfg.stat().st_mode & 0o777, 0o640)  # conserva permisos

    def test_adoptar_queda_dentro_del_bloque_correcto(self):
        self.correr("--adoptar", "normal")
        cfg = self.yaml_cfg()
        self.assertIn("normal", cfg["proyectos_conocidos"])
        self.assertNotIn("normal", cfg["raiz_esperados"])
        self.assertEqual(cfg["excluir_dirs"], ["node_modules"])

    def test_es_idempotente(self):
        self.correr("--adoptar", "normal")
        texto = self.cfg.read_text(encoding="utf-8")
        rc, out = self.correr("--adoptar", "normal")
        self.assertEqual(rc, 0)
        self.assertIn("ya está", out)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), texto)

    def test_carpeta_con_datos_exige_confirmacion(self):
        antes = self.cfg.read_text(encoding="utf-8")
        rc, out = self.correr("--adoptar", "con datos")
        self.assertEqual(rc, 2, out)
        self.assertIn("--confirmar-datos", out)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), antes)
        rc, out = self.correr("--adoptar", "con datos", "--confirmar-datos", "--motivo", "cliente X, respaldo autorizado")
        self.assertEqual(rc, 0, out)
        self.assertIn("con datos", self.yaml_cfg()["proyectos_conocidos"])   # nombre con espacio se recupera bien

    def test_carpeta_con_env_exige_confirmacion(self):
        rc, out = self.correr("--adoptar", "con-env")
        self.assertEqual(rc, 2, out)
        self.assertNotIn("con-env", self.yaml_cfg()["proyectos_conocidos"])

    def test_nombres_raros_y_con_acentos(self):
        rc, out = self.correr("--adoptar", "Comercialización")
        self.assertEqual(rc, 0, out)
        self.assertIn("Comercialización", self.yaml_cfg()["proyectos_conocidos"])

    def test_ignorar_va_a_raiz_esperados(self):
        rc, out = self.correr("--ignorar", "app.bak-2026", "--motivo", "copia vieja")
        self.assertEqual(rc, 0, out)
        cfg = self.yaml_cfg()
        self.assertIn("app.bak-2026", cfg["raiz_esperados"])
        self.assertNotIn("app.bak-2026", cfg["proyectos_conocidos"])
        self.assertIn("NOTA: esta línea de cierre", self.cfg.read_text(encoding="utf-8"))

    def test_nombres_invalidos_o_inexistentes_no_tocan_nada(self):
        antes = self.cfg.read_text(encoding="utf-8")
        for malo in ("../etc", "a/b", "no-existe", "..", "/root"):
            rc, out = self.correr("--adoptar", malo)
            self.assertEqual(rc, 1, f"{malo}: {out}")
            self.assertEqual(self.cfg.read_text(encoding="utf-8"), antes)

    def test_simular_no_escribe(self):
        antes = self.cfg.read_text(encoding="utf-8")
        rc, out = self.correr("--adoptar", "normal", "--simular")
        self.assertEqual(rc, 0)
        self.assertIn("SIMULACIÓN", out)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), antes)
        self.assertEqual(list(self.cfg.parent.glob("*.bak-iee-*")), [])

    def test_lista_en_linea_se_rechaza_sin_cambios(self):
        self.cfg.write_text("proyectos_conocidos: [app-uno, app-dos]\nraiz_esperados:\n  - scripts\n", encoding="utf-8")
        antes = self.cfg.read_text(encoding="utf-8")
        rc, out = self.correr("--adoptar", "normal")
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), antes)

    def test_clave_inexistente_se_crea(self):
        self.cfg.write_text("raiz_esperados:\n  - scripts\n", encoding="utf-8")
        rc, out = self.correr("--adoptar", "normal")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.yaml_cfg()["proyectos_conocidos"], ["normal"])

    def test_respaldo_y_rotacion(self):
        for n in ("normal", "servicio-nuevo", "Comercialización", "app.bak-2026", "scripts"):
            if n == "scripts":
                continue
            self.correr("--adoptar", n)
            time.sleep(1.05)
        baks = sorted(self.cfg.parent.glob("iee.config.yml.bak-iee-*"))
        self.assertLessEqual(len(baks), iee.BACKUPS_DE_CONFIG_MAX)
        self.assertGreaterEqual(len(baks), 1)

    def test_no_se_pueden_combinar(self):
        rc, out = self.correr("--adoptar", "normal", "--ignorar", "scripts")
        self.assertNotEqual(rc, 0)


class TestClasificar(Base):
    def test_clases(self):
        textos = {"crontab": f"*/15 * * * * {self.raiz}/servicio-nuevo/run.sh >> /var/log/x.log\n"}
        a = iee.clasificar_carpeta(self.raiz, "servicio-nuevo", iee.EXCLUIR_DIRS_BASE, textos)
        self.assertEqual(a["clase"], "activa")
        self.assertIn("crontab", a["referencias"])
        b = iee.clasificar_carpeta(self.raiz, "app.bak-2026", iee.EXCLUIR_DIRS_BASE, textos)
        self.assertEqual(b["clase"], "copia_o_prueba")
        c = iee.clasificar_carpeta(self.raiz, "normal", iee.EXCLUIR_DIRS_BASE, textos)
        self.assertEqual(c["clase"], "revisar")
        d = iee.clasificar_carpeta(self.raiz, "con datos", iee.EXCLUIR_DIRS_BASE, textos)
        self.assertEqual(d["datos"], {".db"})
        e = iee.clasificar_carpeta(self.raiz, "con-env", iee.EXCLUIR_DIRS_BASE, textos)
        self.assertTrue(e["env"])

    def test_no_confunde_prefijos(self):
        textos = {"crontab": f"{self.raiz}/app-uno-env/bin/python x.py\n"}
        self.assertEqual(iee.referencias_activas(self.raiz, "app-uno", textos), [])
        self.assertEqual(iee.referencias_activas(self.raiz, "app-uno-env", textos), ["crontab"])

    def test_comando_clasificar_no_modifica_ni_registra(self):
        antes = self.cfg.read_text(encoding="utf-8")
        rc, out = self.correr("--clasificar")
        self.assertEqual(rc, 0, out)
        self.assertIn("sin catalogar", out)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), antes)
        self.assertFalse((self.raiz / ".iee-history.json").exists())

    def test_ejemplo_de_ejemplo_json_sigue_igual(self):
        rc, out = self.correr("--json", "--no-history")
        self.assertEqual(rc, 0, out)
        self.assertIn("proyectos_sin_catalogar", json.loads(out))


if __name__ == "__main__":
    unittest.main(verbosity=2)
