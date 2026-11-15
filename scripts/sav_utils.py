"""SPSS (.sav) dataset resolution, hashing, preflight validation, and reporting.

No respondent-level data is ever written to the generated report.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_staged_sav_name(sav_path: Path, sha256_hex: str | None = None) -> tuple[str, str]:
    digest = sha256_hex or compute_file_sha256(sav_path)
    staged_name = f"{sav_path.stem}_{digest[:8]}{sav_path.suffix}"
    return staged_name, digest


def resolve_sav_path(
    cli_sav: str | None,
    spec_sav: str | None,
    root_dir: Path,
    spec_dir: Path | None = None,
) -> tuple[Path | None, str]:
    """Resolve effective .sav path with priority: runtime CLI > spec.sav_file > None.

    Returns (resolved_path_or_none, source_label).
    """
    raw = ""
    source = "none"
    if cli_sav and cli_sav.strip():
        raw = cli_sav.strip()
        source = "runtime_arg"
    elif spec_sav and spec_sav.strip():
        raw = spec_sav.strip()
        source = "spec"

    if not raw:
        return None, "none"

    candidate = Path(raw)
    if candidate.is_absolute():
        return (candidate.resolve() if candidate.exists() else candidate), source

    if source == "runtime_arg":
        cwd_cand = Path.cwd() / candidate
        if cwd_cand.exists():
            return cwd_cand.resolve(), source
        root_cand = root_dir / candidate
        if root_cand.exists():
            return root_cand.resolve(), source
        return cwd_cand, source

    # source == "spec"
    root_cand = root_dir / candidate
    if root_cand.exists():
        return root_cand.resolve(), source
    if spec_dir is not None:
        spec_cand = spec_dir / candidate
        if spec_cand.exists():
            return spec_cand.resolve(), source
    return root_cand, source


def validate_sav_dataset(
    sav_path: Path | None,
    spec: dict[str, Any],
    source_label: str = "none",
) -> dict[str, Any]:
    """Validate SPSS .sav dataset against spec indicators and build integration report."""
    report: dict[str, Any] = {
        "dataset_configured": sav_path is not None,
        "dataset_source": source_label,
        "original_filename": sav_path.name if sav_path is not None else "",
        "staged_filename": "",
        "sha256": "",
        "n_observations": 0,
        "n_variables": 0,
        "available_variables": [],
        "indicator_mapping": [],
        "missing_value_counts": {},
        "warnings": [],
        "errors": [],
        "validation_status": "skipped",
        "binding_status": "not_configured",
    }

    if sav_path is None:
        return report

    if not sav_path.exists():
        report["errors"].append(f"Specified SAV file not found: {sav_path}")
        report["validation_status"] = "failed"
        report["binding_status"] = "failed"
        return report

    if not sav_path.is_file():
        report["errors"].append(f"Specified SAV path is not a file: {sav_path}")
        report["validation_status"] = "failed"
        report["binding_status"] = "failed"
        return report

    try:
        staged_name, sha256_hex = compute_staged_sav_name(sav_path)
    except OSError as exc:
        report["errors"].append(f"Unable to read SAV file '{sav_path}': {exc}")
        report["validation_status"] = "failed"
        report["binding_status"] = "failed"
        return report

    report["staged_filename"] = staged_name
    report["sha256"] = sha256_hex

    try:
        import pyreadstat
    except ImportError:
        report["errors"].append(
            "Missing required Python dependency 'pyreadstat' to validate .sav dataset. "
            "Install it with: pip install pyreadstat"
        )
        report["validation_status"] = "failed"
        report["binding_status"] = "failed"
        return report

    try:
        df, meta = pyreadstat.read_sav(str(sav_path))
    except Exception as exc:
        report["errors"].append(f"Failed to parse SPSS .sav file '{sav_path.name}': {exc}")
        report["validation_status"] = "failed"
        report["binding_status"] = "failed"
        return report

    import pandas as pd

    n_obs = int(len(df))
    n_vars = int(len(df.columns))
    labels_map = getattr(meta, "column_names_to_labels", {}) or {}

    report["n_observations"] = n_obs
    report["n_variables"] = n_vars
    report["available_variables"] = [
        {"name": str(col), "label": str(labels_map.get(col) or "")}
        for col in df.columns
    ]

    if n_obs == 0:
        report["errors"].append(f"SAV file '{sav_path.name}' contains 0 observations.")

    cols_set = set(str(c) for c in df.columns)
    missing_cols: list[str] = []

    for c in spec.get("constructs", []):
        cid = c.get("id", "")
        for ind in c.get("indicators", []):
            sv = ind.get("sav_var", "")
            present = sv in cols_set
            item: dict[str, Any] = {
                "construct_id": cid,
                "indicator_title": ind.get("title", ""),
                "sav_var": sv,
                "sav_label": str(labels_map.get(sv) or "") if present else "",
                "present": present,
                "dtype": "",
                "missing_count": 0,
                "non_null_count": 0,
                "n_unique": 0,
                "numeric_compatible": False,
                "constant_or_zero_variation": False,
            }
            if not present:
                missing_cols.append(sv)
            else:
                series = df[sv]
                item["dtype"] = str(series.dtype)
                miss_cnt = int(series.isna().sum())
                non_null = series.dropna()
                item["missing_count"] = miss_cnt
                item["non_null_count"] = int(len(non_null))
                item["n_unique"] = int(non_null.nunique())
                report["missing_value_counts"][sv] = miss_cnt

                is_num = bool(pd.api.types.is_numeric_dtype(series))
                item["numeric_compatible"] = is_num
                if not is_num:
                    report["errors"].append(
                        f"SAV variable '{sv}' (construct '{cid}') has non-numeric dtype '{series.dtype}', "
                        "which is incompatible with AMOS SEM estimation."
                    )
                if miss_cnt > 0:
                    report["warnings"].append(
                        f"SAV variable '{sv}' has {miss_cnt} missing value(s) out of {n_obs} observations "
                        "(not auto-deleted or imputed)."
                    )
                if len(non_null) == 0:
                    item["constant_or_zero_variation"] = True
                    report["errors"].append(
                        f"SAV variable '{sv}' (construct '{cid}') has 0 non-missing observations."
                    )
                elif non_null.nunique() <= 1:
                    item["constant_or_zero_variation"] = True
                    report["warnings"].append(
                        f"SAV variable '{sv}' (construct '{cid}') is constant (no usable variation; n_unique={non_null.nunique()})."
                    )

            report["indicator_mapping"].append(item)

    if missing_cols:
        sample_cols = [str(c) for c in list(df.columns)[:20]]
        report["errors"].append(
            f"SAV column mismatch in '{sav_path.name}': missing required indicator variable(s) {sorted(set(missing_cols))}. "
            f"Available columns (first {len(sample_cols)} of {n_vars}): {sample_cols}"
        )

    if report["errors"]:
        report["validation_status"] = "failed"
        report["binding_status"] = "failed"
    else:
        report["validation_status"] = "passed"
        report["binding_status"] = "configured"

    return report


def main() -> int:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Inspect an SPSS .sav dataset and optionally validate against a spec JSON.")
    parser.add_argument("--sav", required=True, help="Path to SPSS .sav file")
    parser.add_argument("--spec", default="", help="Optional path to .json spec for indicator preflight validation")
    parser.add_argument("--json", action="store_true", help="Print full JSON report")
    args = parser.parse_args()

    root_dir = Path(__file__).resolve().parents[1]
    sav_path, src = resolve_sav_path(args.sav, None, root_dir)

    spec: dict[str, Any] = {"constructs": []}
    if args.spec:
        sp = Path(args.spec)
        if not sp.is_absolute():
            sp = Path.cwd() / sp if (Path.cwd() / sp).exists() else (root_dir / sp)
        if not sp.exists():
            sys.stderr.write(f"Spec file not found: {sp}\n")
            return 1
        spec = json.loads(sp.read_text(encoding="utf-8"))

    report = validate_sav_dataset(sav_path, spec, source_label=src)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"SAV: {report['original_filename']} | Staged: {report['staged_filename'] or 'n/a'}")
        print(f"SHA256: {report['sha256'] or 'n/a'}")
        print(f"Observations: {report['n_observations']} | Variables: {report['n_variables']}")
        print(f"Status: {report['validation_status']}")
        if report["available_variables"]:
            print("Variables:")
            for v in report["available_variables"]:
                lbl = f" — {v['label']}" if v["label"] else ""
                print(f"  - {v['name']}{lbl}")
        for w in report["warnings"]:
            print(f"WARNING: {w}", file=sys.stderr)
        for e in report["errors"]:
            print(f"ERROR: {e}", file=sys.stderr)

    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
