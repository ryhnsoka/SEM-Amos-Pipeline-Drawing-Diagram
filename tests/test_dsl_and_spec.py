"""Regression tests for SEM text DSL parsing and JSON spec validation."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from text_to_spec import parse_text_to_spec  # noqa: E402
from generate_from_spec import validate_spec  # noqa: E402


class TestDslAndSpec(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.std = json.loads((ROOT / "spec" / "diagram_standard.json").read_text(encoding="utf-8"))

    def test_valid_text_parsing_and_presets(self) -> None:
        for rel in (
            "spec/template_model.txt",
            "presets/nmax_bisdig.txt",
            "presets/green_transparency_advocacy.txt",
            "presets/digital_ecosystem_national.txt",
        ):
            txt = (ROOT / rel).read_text(encoding="utf-8")
            spec = parse_text_to_spec(txt, out_hint=Path(rel).stem)
            errs = validate_spec(spec, self.std, check_sav=False)
            self.assertEqual(errs, [], f"Unexpected errors for {rel}: {errs}")

    def test_invalid_hypothesis_syntax_raises(self) -> None:
        bad_txt = """
        COV: none
        H1: JustOneConstructWithoutArrow
        """
        with self.assertRaises(ValueError):
            parse_text_to_spec(bad_txt)

    def test_missing_indicators_raises(self) -> None:
        bad_txt = """
        COV: none
        H1: A -> B
        A: *A1
        B: *B1, B2
        """
        with self.assertRaises(ValueError):
            parse_text_to_spec(bad_txt)

    def test_duplicate_indicator_definition_raises(self) -> None:
        bad_txt = """
        COV: none
        H1: A -> B
        A: *A1, A2
        A: *A3, A4
        """
        with self.assertRaises(ValueError):
            parse_text_to_spec(bad_txt)

    def test_multiple_reference_indicators_raises(self) -> None:
        bad_txt = """
        COV: none
        H1: A -> B
        A: *A1, *A2
        B: *B1, B2
        """
        with self.assertRaises(ValueError):
            parse_text_to_spec(bad_txt)

    def test_duplicate_construct_id_and_bad_ref_row_in_json(self) -> None:
        spec = json.loads((ROOT / "spec" / "template_spec.json").read_text(encoding="utf-8"))
        spec["constructs"][1]["id"] = spec["constructs"][0]["id"]
        spec["constructs"][0]["ref_row"] = 99
        errs = validate_spec(spec, self.std, check_sav=False)
        self.assertTrue(any("Duplicate construct id" in e for e in errs))
        self.assertTrue(any("ref_row=99 out of range" in e for e in errs))

    def test_covariance_policies(self) -> None:
        base = """
        H1: X1 -> Y1
        H2: X2 -> Y1
        X1: *X1_1, X1_2
        X2: *X2_1, X2_2
        Y1: *Y1_1, Y1_2
        """
        spec_all = parse_text_to_spec("COV: all\n" + base)
        self.assertEqual(spec_all["diagram"]["covariance_policy"], "all")
        self.assertEqual(len(spec_all["covariances"]), 1)

        spec_none = parse_text_to_spec("COV: none\n" + base)
        self.assertEqual(spec_none["diagram"]["covariance_policy"], "none")
        self.assertEqual(spec_none["covariances"], [])

        spec_exp = parse_text_to_spec("COV: X1 <-> X2\n" + base)
        self.assertEqual(spec_exp["diagram"]["covariance_policy"], "explicit")
        self.assertEqual(len(spec_exp["covariances"]), 1)

        # Omitting COV: must NOT silently add covariances and must emit a warning
        spec_omitted = parse_text_to_spec(base)
        self.assertEqual(spec_omitted["diagram"]["covariance_policy"], "unspecified")
        self.assertFalse(spec_omitted["diagram"]["enforce_full_exogenous_covariances"])
        self.assertEqual(spec_omitted["covariances"], [])
        self.assertTrue(spec_omitted.get("warnings"))
        self.assertEqual(validate_spec(spec_omitted, self.std, check_sav=False), [])

    def test_json_covariance_none_conflict(self) -> None:
        spec = json.loads((ROOT / "spec" / "template_spec.json").read_text(encoding="utf-8"))
        spec["diagram"]["covariance_policy"] = "none"
        errs = validate_spec(spec, self.std, check_sav=False)
        self.assertTrue(any("covariance_policy='none' conflicts" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
