"""Regression tests for geometry collision detection, VB.NET generation, and optional x86 compilation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from generate_from_spec import (  # noqa: E402
    build_plan,
    compute_geometry,
    emit_headless_fit_vb,
    emit_plugin_vb,
    run_checks,
)
from text_to_spec import parse_text_to_spec  # noqa: E402


def _has_amos_toolchain() -> bool:
    vbc = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "vbc.exe"
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    amos_dll = Path(pf86) / "IBM" / "SPSS" / "Amos" / "23" / "AmosGraphics.dll"
    return vbc.exists() and amos_dll.exists()


class TestGeometryAndVb(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.std = json.loads((ROOT / "spec" / "diagram_standard.json").read_text(encoding="utf-8"))

    def test_zero_collision_for_template_and_all_presets(self) -> None:
        json_specs = [
            ROOT / "spec" / "template_spec.json",
            ROOT / "presets" / "sustainable_business.json",
        ]
        for sp_path in json_specs:
            spec = json.loads(sp_path.read_text(encoding="utf-8"))
            g = compute_geometry(spec, self.std)
            plan = build_plan(spec, g, self.std)
            probs = run_checks(plan, g, spec["diagram"].get("goodness_of_fit_caption", True))
            self.assertEqual(probs, [], f"Collision problems in {sp_path.name}: {probs}")

        txt_presets = [
            ROOT / "spec" / "template_model.txt",
            ROOT / "presets" / "nmax_bisdig.txt",
            ROOT / "presets" / "green_transparency_advocacy.txt",
            ROOT / "presets" / "digital_ecosystem_national.txt",
        ]
        for txt_path in txt_presets:
            spec = parse_text_to_spec(txt_path.read_text(encoding="utf-8"), out_hint=txt_path.stem)
            g = compute_geometry(spec, self.std)
            plan = build_plan(spec, g, self.std)
            probs = run_checks(plan, g, spec["diagram"].get("goodness_of_fit_caption", True))
            self.assertEqual(probs, [], f"Collision problems in {txt_path.name}: {probs}")

    def test_collision_detection_catches_overlap(self) -> None:
        spec = json.loads((ROOT / "spec" / "template_spec.json").read_text(encoding="utf-8"))
        g = compute_geometry(spec, self.std)
        plan = build_plan(spec, g, self.std)
        # Force an overlap between the first two shapes
        plan.shapes[1].cx = plan.shapes[0].cx
        plan.shapes[1].cy = plan.shapes[0].cy
        probs = run_checks(plan, g, True)
        self.assertTrue(any("overlap:" in p for p in probs))

    def test_vb_generation_preserves_invariants_and_diagnostics(self) -> None:
        spec = json.loads((ROOT / "spec" / "template_spec.json").read_text(encoding="utf-8"))
        g = compute_geometry(spec, self.std)
        vb = emit_plugin_vb(spec, g, self.std, staged_sav_name="sample_deadbeef.sav")
        self.assertIn('Private Const SavFileName As String = "sample_deadbeef.sav"', vb)
        self.assertIn("pd.SetDataFile(1, cDatabaseFormat.mmSPSS, savPath", vb)
        self.assertIn("AMOS SEM dataset binding failed", vb)
        self.assertIn("AMOS rejected dataset binding.", vb)
        self.assertIn("Searched paths:", vb)
        self.assertIn("File exists:", vb)
        self.assertIn("Return \"Result\"", vb)

        headless = emit_headless_fit_vb(spec)
        self.assertIn("Sem.GenerateDefaultCovariances(False)", headless)
        self.assertIn('Sem.AStructure("X1 <--> X2")', headless)
        self.assertIn('Sem.AStructure("M1 = X1 + X2 + (1) d1")', headless)
        self.assertIn("RUNTIME_ERROR=", headless)

    @unittest.skipUnless(_has_amos_toolchain(), "Requires local Windows .NET 4.0 x86 vbc.exe and IBM SPSS Amos 23")
    def test_local_amos23_x86_compile_and_reflection(self) -> None:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "scripts" / "amos_pipeline.ps1"),
                "-SpecFile",
                "spec\\template_model.txt",
                "-SkipFit",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"Pipeline failed:\n{proc.stdout}\n{proc.stderr}")
        exec_rep = json.loads((ROOT / "plan" / "execution_report.json").read_text(encoding="utf-8"))
        self.assertEqual(exec_rep["statuses"]["dll_compilation"], "passed")
        self.assertEqual(exec_rep["statuses"]["plugin_contract_verification"], "passed")
        self.assertEqual(exec_rep["statuses"]["statistical_estimation"], "skipped")


if __name__ == "__main__":
    unittest.main()
