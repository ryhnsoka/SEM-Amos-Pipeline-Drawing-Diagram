"""Convert a compact SEM text description (.txt) into a validated spec JSON.

Supported text syntax (order-independent, comments start with '#'):
  PLUGIN: My Model Name
  FONT: 20
  SAV: data/mydata.sav
  DEFAULT_IND: 4
  COV: all | none | A <-> B
  LEFT: A, B
  RIGHT: M, Y

  # Hypotheses / structural paths (arrow or Indonesian causal phrasing):
  H1: Brand Image -> Trust
  H2: Service Quality -> Trust -> Loyalty
  H3: Perceived Value berpengaruh signifikan terhadap Loyalty

  # Optional indicator lists per construct ('*' marks fixed-1 reference indicator):
  Brand Image: *BI1 | Citra Positif, BI2 | Reputasi Baik, BI3 | Keunikan Merek
  Trust: TR1, TR2, TR3

Uses only relative paths from project root. Exits non-zero on invalid text.
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

ARROW_RE = re.compile(r"\s*(?:->|=>|-->|→)\s*")
COV_ARROW_RE = re.compile(r"\s*(?:<->|<-->|↔)\s*")
HYP_PREFIX_RE = re.compile(r"^(H\d+)\s*[:.\-]\s*(.+)$", re.IGNORECASE)
INDO_CAUSAL_RE = re.compile(
    r"^(.+?)\s+(?:berpengaruh(?:\s+(?:positif|negatif|langsung|tidak langsung|dan))*?\s+(?:signifikan\s+)?terhadap|memengaruhi|mempengaruhi|berhubungan terhadap)\s+(.+?)\.?$",
    re.IGNORECASE,
)


def make_ident(label: str, used: set[str], prefix: str = "V") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", label.strip()).strip("_")
    if not cleaned:
        cleaned = prefix
    if cleaned[0].isdigit():
        cleaned = f"{prefix}_{cleaned}"
    base = cleaned
    n = 2
    while cleaned in used:
        cleaned = f"{base}_{n}"
        n += 1
    used.add(cleaned)
    return cleaned


def parse_text_to_spec(text: str, out_hint: str = "TextGeneratedSEM") -> dict[str, Any]:
    plugin_name = "Standard SEM Text Model"
    root_ns = re.sub(r"[^A-Za-z0-9]", "", out_hint) or "TextGeneratedSEM"
    if root_ns[0].isdigit():
        root_ns = "SEM_" + root_ns
    plugin_source = f"scripts/{root_ns}.vb"
    sav_file = ""
    font_size = 20.0
    default_ind = 4
    cov_mode = "unspecified"  # "all" | "none" | "explicit" | "unspecified"
    explicit_covs: list[tuple[str, str]] = []
    explicit_left: list[str] = []
    explicit_right: list[str] = []

    construct_titles: list[str] = []
    raw_paths: list[tuple[str, str, str]] = []  # (hid, src_title, dst_title)
    raw_inds: dict[str, list[tuple[str, str, bool]]] = {}  # title -> [(sav_or_code, display_title, is_ref)]

    def register_title(t: str) -> str:
        t_clean = re.sub(r"\s+", " ", t.strip().rstrip("."))
        if t_clean and t_clean not in construct_titles:
            construct_titles.append(t_clean)
        return t_clean

    h_counter = 1
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        # Key: Value directives (strictly matched known keys)
        m_dir = re.match(r"^(PLUGIN|NAME|NAMESPACE|PLUGIN_SOURCE|SAV|FONT|DEFAULT_IND|COV|LEFT|RIGHT)\s*:\s*(.+)$", line, re.IGNORECASE)
        if m_dir:
            key = m_dir.group(1).upper()
            val = m_dir.group(2).strip()
            if key in ("PLUGIN", "NAME"):
                plugin_name = val
            elif key == "NAMESPACE":
                root_ns = re.sub(r"[^A-Za-z0-9_]", "", val) or root_ns
                plugin_source = f"scripts/{root_ns}.vb"
            elif key == "PLUGIN_SOURCE":
                plugin_source = val
            elif key == "SAV":
                sav_file = val
            elif key == "FONT":
                font_size = float(val)
            elif key == "DEFAULT_IND":
                default_ind = max(2, int(val))
            elif key == "COV":
                if val.lower() in ("all", "auto", "full", "lengkap"):
                    cov_mode = "all"
                elif val.lower() in ("none", "0", "off", "tanpa"):
                    cov_mode = "none"
                else:
                    cov_mode = "explicit"
                    for part in val.split(","):
                        sides = COV_ARROW_RE.split(part.strip())
                        if len(sides) != 2:
                            raise ValueError(f"Line {lineno}: invalid COV pair '{part}'")
                        explicit_covs.append((register_title(sides[0]), register_title(sides[1])))
            elif key == "LEFT":
                explicit_left = [register_title(p) for p in val.split(",") if p.strip()]
            elif key == "RIGHT":
                explicit_right = [register_title(p) for p in val.split(",") if p.strip()]
            continue

        # Covariance line: A <-> B
        if COV_ARROW_RE.search(line) and not ARROW_RE.search(line):
            cov_mode = "explicit"
            sides = COV_ARROW_RE.split(line)
            if len(sides) != 2:
                raise ValueError(f"Line {lineno}: invalid covariance syntax '{line}'")
            explicit_covs.append((register_title(sides[0]), register_title(sides[1])))
            continue

        # Strip optional Hk: prefix
        hid = ""
        body = line
        m_h = HYP_PREFIX_RE.match(line)
        if m_h:
            hid = m_h.group(1).upper()
            body = m_h.group(2).strip()

        # Arrow structural path(s): A -> B (-> C ...)
        if ARROW_RE.search(body):
            chain = [register_title(p) for p in ARROW_RE.split(body) if p.strip()]
            if len(chain) < 2:
                raise ValueError(f"Line {lineno}: incomplete arrow path '{line}'")
            for i in range(len(chain) - 1):
                cur_hid = hid if (hid and len(chain) == 2) else f"H{h_counter}"
                if not (hid and len(chain) == 2):
                    h_counter += 1
                else:
                    h_counter = max(h_counter + 1, int(hid[1:]) + 1)
                raw_paths.append((cur_hid, chain[i], chain[i + 1]))
            continue

        # Natural Indonesian hypothesis phrasing
        m_indo = INDO_CAUSAL_RE.match(body)
        if m_indo:
            src_t = register_title(m_indo.group(1))
            dst_t = register_title(m_indo.group(2))
            cur_hid = hid or f"H{h_counter}"
            h_counter = max(h_counter + 1, (int(hid[1:]) + 1) if hid else h_counter + 1)
            raw_paths.append((cur_hid, src_t, dst_t))
            continue

        # Indicator specification: Construct Name: *Ind1 | Label 1, Ind2 | Label 2, ...
        if ":" in line:
            cname_raw, items_raw = line.split(":", 1)
            ctitle = register_title(cname_raw)
            if ctitle in raw_inds:
                raise ValueError(f"Line {lineno}: duplicate indicator definition for construct '{ctitle}'.")
            parsed_items: list[tuple[str, str, bool]] = []
            ref_count = 0
            for tok in items_raw.split(","):
                tok = tok.strip()
                if not tok:
                    continue
                is_ref = tok.startswith("*")
                if is_ref:
                    ref_count += 1
                    tok = tok[1:].strip()
                if "|" in tok:
                    sv_raw, disp_raw = [x.strip() for x in tok.split("|", 1)]
                    parsed_items.append((sv_raw, disp_raw, is_ref))
                else:
                    parsed_items.append((tok, tok, is_ref))
            if len(parsed_items) < 2:
                raise ValueError(f"Line {lineno}: construct '{ctitle}' needs at least 2 indicators.")
            if ref_count > 1:
                raise ValueError(f"Line {lineno}: construct '{ctitle}' has {ref_count} reference indicators ('*'); expected at most 1.")
            raw_inds[ctitle] = parsed_items
            continue

        raise ValueError(f"Line {lineno}: unrecognized syntax '{line}'")

    if not raw_paths:
        raise ValueError("No structural paths found in text specification (e.g. 'H1: A -> B').")

    # Classify exogenous vs endogenous
    targets = {dst for _, _, dst in raw_paths}
    exog_titles = [t for t in construct_titles if t not in targets]
    endog_titles = [t for t in construct_titles if t in targets]

    # Build constructs + unique IDs + error/disturbance names
    used_ids: set[str] = set()
    used_sav: set[str] = set()
    id_by_title: dict[str, str] = {}
    for t in construct_titles:
        id_by_title[t] = make_ident(t, used_ids, prefix="F")

    err_counter = 1
    dist_counter = 1
    constructs_json: list[dict[str, Any]] = []
    for t in construct_titles:
        cid = id_by_title[t]
        role = "endogenous" if t in targets else "exogenous"
        c_obj: dict[str, Any] = {"id": cid, "title": t, "role": role}
        if role == "endogenous":
            c_obj["disturbance"] = f"d{dist_counter}"
            dist_counter += 1

        items = raw_inds.get(t)
        if not items:
            items = [(f"{cid}_{k+1}", f"{t} {k+1}", k == 0) for k in range(default_ind)]

        ref_row = 0
        inds_json = []
        for idx, (sv_raw, disp, is_ref) in enumerate(items):
            if is_ref:
                ref_row = idx
            sav_ident = make_ident(sv_raw, used_sav, prefix="V")
            inds_json.append({"title": disp, "sav_var": sav_ident, "err": f"e{err_counter}"})
            err_counter += 1
        c_obj["ref_row"] = ref_row
        c_obj["indicators"] = inds_json
        constructs_json.append(c_obj)

    # Left / right column assignment
    if explicit_left or explicit_right:
        left_ids = [id_by_title[t] for t in explicit_left]
        right_ids = [id_by_title[t] for t in explicit_right]
        if not right_ids:
            right_ids = [id_by_title[t] for t in construct_titles if id_by_title[t] not in left_ids]
        if not left_ids:
            left_ids = [id_by_title[t] for t in construct_titles if id_by_title[t] not in right_ids]
    else:
        left_ids = [id_by_title[t] for t in exog_titles]
        right_ids = [id_by_title[t] for t in endog_titles]

    struct_json = [
        {"id": hid, "from": id_by_title[s], "to": id_by_title[d]}
        for hid, s, d in raw_paths
    ]

    warnings: list[str] = []
    if cov_mode == "none":
        cov_json = []
        enforce_cov = False
        cov_policy = "none"
    elif cov_mode == "explicit":
        cov_json = [{"a": id_by_title[a], "b": id_by_title[b]} for a, b in explicit_covs]
        enforce_cov = False
        cov_policy = "explicit"
    elif cov_mode == "all":
        cov_json = [{"a": id_by_title[a], "b": id_by_title[b]} for a, b in itertools.combinations(exog_titles, 2)]
        enforce_cov = True
        cov_policy = "all"
    else:
        cov_json = []
        enforce_cov = False
        cov_policy = "unspecified"
        if len(exog_titles) >= 2:
            warnings.append(
                "Covariance policy not explicitly specified (omitted 'COV:'). "
                "No exogenous covariance paths were added automatically. "
                "Specify 'COV: all' (all exogenous pairs), 'COV: none' (no covariances), "
                "or explicit pairs such as 'COV: Construct A <-> Construct B'."
            )

    spec_out: dict[str, Any] = {
        "plugin_name": plugin_name,
        "root_namespace": root_ns,
        "plugin_source": plugin_source,
        "sav_file": sav_file,
        "diagram": {
            "font_size": font_size,
            "covariance_policy": cov_policy,
            "enforce_full_exogenous_covariances": enforce_cov,
            "left_column": left_ids,
            "right_column": right_ids,
            "goodness_of_fit_caption": True,
        },
        "constructs": constructs_json,
        "structural_paths": struct_json,
        "covariances": cov_json,
    }
    if warnings:
        spec_out["warnings"] = warnings
    return spec_out


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile compact SEM text DSL into standard spec JSON.")
    parser.add_argument("--in", dest="in_file", required=True, help="Input .txt specification file")
    parser.add_argument("--out", dest="out_file", default="", help="Output .json spec path (default: spec/<stem>.json)")
    args = parser.parse_args()

    in_path = Path(args.in_file)
    if not in_path.is_absolute():
        in_path = ROOT / in_path
    if not in_path.exists():
        sys.stderr.write(f"Text spec file not found: {in_path}\n")
        return 1

    out_path = Path(args.out_file) if args.out_file else (ROOT / "spec" / f"{in_path.stem}.json")
    if not out_path.is_absolute():
        out_path = ROOT / out_path

    try:
        spec = parse_text_to_spec(in_path.read_text(encoding="utf-8"), out_hint=in_path.stem)
    except Exception as exc:
        sys.stderr.write(f"TEXT PARSE ERROR: {exc}\n")
        return 1

    for w in spec.get("warnings", []):
        sys.stderr.write(f"WARNING: {w}\n")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        out_label = str(out_path.relative_to(ROOT))
    except ValueError:
        out_label = str(out_path)
    print(f"TEXT_TO_SPEC_OK in={in_path.name} -> out={out_label} constructs={len(spec['constructs'])} paths={len(spec['structural_paths'])} covs={len(spec['covariances'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
