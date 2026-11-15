"""Regression tests for SPSS (.sav) dataset resolution, validation, hashing, and reporting."""
from __future__ import annotations

import builtins
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd
import pyreadstat

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from sav_utils import compute_staged_sav_name, resolve_sav_path, validate_sav_dataset  # noqa: E402
from generate_from_spec import compute_geometry, emit_headless_fit_vb, emit_plugin_vb  # noqa: E402


def make_minimal_spec(sav_file: str = "") -> dict:
    spec = json.loads((ROOT / "spec" / "template_spec.json").read_text(encoding="utf-8"))
    spec["sav_file"] = sav_file
    return spec


def make_valid_df(n: int = 30) -> pd.DataFrame:
    cols = [
        "X1_1", "X1_2", "X1_3",
        "X2_1", "X2_2", "X2_3",
        "M1_1", "M1_2", "M1_3",
        "Y1_1", "Y1_2", "Y1_3",
    ]
    data = {col: [float((i + idx) % 5 + 1) for i in range(n)] for idx, col in enumerate(cols)}
    return pd.DataFrame(data)


class TestDatasetIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.std = json.loads((ROOT / "spec" / "diagram_standard.json").read_text(encoding="utf-8"))

    def test_valid_sav_dataset_report_and_no_respondent_leak(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sav_path = Path(td) / "survey.sav"
            df = make_valid_df(25)
            pyreadstat.write_sav(df, str(sav_path), column_labels={c: f"Label {c}" for c in df.columns})
            spec = make_minimal_spec()
            report = validate_sav_dataset(sav_path, spec, source_label="runtime_arg")
            self.assertEqual(report["validation_status"], "passed")
            self.assertEqual(report["binding_status"], "configured")
            self.assertEqual(report["n_observations"], 25)
            self.assertEqual(report["n_variables"], 12)
            self.assertEqual(len(report["indicator_mapping"]), 12)
            self.assertTrue(report["staged_filename"].startswith("survey_"))
            self.assertTrue(report["staged_filename"].endswith(".sav"))
            self.assertEqual(len(report["sha256"]), 64)
            self.assertEqual(report["errors"], [])

    def test_missing_indicator_column_fails_without_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sav_path = Path(td) / "missing_col.sav"
            df = make_valid_df(15).drop(columns=["Y1_3"])
            df["Y1_EXTRA"] = 1.0  # same column count, wrong name - must NOT guess by position
            pyreadstat.write_sav(df, str(sav_path))
            spec = make_minimal_spec()
            report = validate_sav_dataset(sav_path, spec, source_label="runtime_arg")
            self.assertEqual(report["validation_status"], "failed")
            self.assertTrue(any("Y1_3" in e for e in report["errors"]))

    def test_invalid_dataset_path_fails(self) -> None:
        spec = make_minimal_spec()
        report = validate_sav_dataset(Path("Z:/nonexistent_dir/missing_file.sav"), spec, source_label="runtime_arg")
        self.assertEqual(report["validation_status"], "failed")
        self.assertTrue(any("not found" in e for e in report["errors"]))

    def test_missing_pyreadstat_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sav_path = Path(td) / "dummy.sav"
            sav_path.write_bytes(b"SAV_PLACEHOLDER")
            spec = make_minimal_spec()
            real_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "pyreadstat":
                    raise ImportError("Simulated missing pyreadstat")
                return real_import(name, *args, **kwargs)

            with mock.patch("builtins.__import__", side_effect=fake_import):
                report = validate_sav_dataset(sav_path, spec, source_label="runtime_arg")
            self.assertEqual(report["validation_status"], "failed")
            self.assertTrue(any("pyreadstat" in e for e in report["errors"]))

    def test_runtime_sav_resolution_priority_and_vb_consistency(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            sav_runtime = td_path / "runtime_only.sav"
            pyreadstat.write_sav(make_valid_df(20), str(sav_runtime))
            resolved, source = resolve_sav_path(str(sav_runtime), "data/ignored.sav", ROOT)
            self.assertEqual(source, "runtime_arg")
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.resolve(), sav_runtime.resolve())

            spec = make_minimal_spec(sav_file="")
            report = validate_sav_dataset(resolved, spec, source_label=source)
            self.assertEqual(report["validation_status"], "passed")
            staged_name = report["staged_filename"]

            g = compute_geometry(spec, self.std)
            vb_code = emit_plugin_vb(spec, g, self.std, staged_sav_name=staged_name)
            self.assertIn(f'Private Const SavFileName As String = "{staged_name}"', vb_code)

            headless_code = emit_headless_fit_vb(spec)
            for c in spec["constructs"]:
                for ind in c["indicators"]:
                    self.assertIn(ind["sav_var"], vb_code)
                    self.assertIn(ind["sav_var"], headless_code)

    def test_identical_filenames_produce_distinct_staged_names(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d1 = Path(td) / "a"
            d2 = Path(td) / "b"
            d1.mkdir()
            d2.mkdir()
            s1 = d1 / "data.sav"
            s2 = d2 / "data.sav"
            pyreadstat.write_sav(make_valid_df(10), str(s1))
            df2 = make_valid_df(10)
            df2["X1_1"] = df2["X1_1"] + 1.0
            pyreadstat.write_sav(df2, str(s2))

            name1, hash1 = compute_staged_sav_name(s1)
            name2, hash2 = compute_staged_sav_name(s2)
            self.assertNotEqual(hash1, hash2)
            self.assertNotEqual(name1, name2)

    def test_missing_values_and_constant_var_reported_not_modified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sav_path = Path(td) / "quality.sav"
            df = make_valid_df(20)
            df.loc[0, "X1_1"] = None
            df.loc[1, "X1_1"] = None
            df["X2_3"] = 3.0  # constant
            pyreadstat.write_sav(df, str(sav_path))
            spec = make_minimal_spec()
            report = validate_sav_dataset(sav_path, spec, source_label="runtime_arg")
            self.assertEqual(report["validation_status"], "passed")
            self.assertEqual(report["missing_value_counts"]["X1_1"], 2)
            self.assertTrue(any("missing value" in w for w in report["warnings"]))
            self.assertTrue(any("constant" in w for w in report["warnings"]))

    def test_cli_generate_fails_on_missing_sav_column(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sav_path = Path(td) / "bad.sav"
            pyreadstat.write_sav(make_valid_df(10).drop(columns=["M1_2"]), str(sav_path))
            rep_path = Path(td) / "ds_report.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "generate_from_spec.py"),
                    "--spec",
                    str(ROOT / "spec" / "template_spec.json"),
                    "--sav",
                    str(sav_path),
                    "--dataset-report",
                    str(rep_path),
                    "--validate-only",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue(rep_path.exists())
            rep = json.loads(rep_path.read_text(encoding="utf-8"))
            self.assertEqual(rep["validation_status"], "failed")
            self.assertTrue(any("M1_2" in e for e in rep["errors"]))


if __name__ == "__main__":
    unittest.main()
