"""Universal Single-Source-of-Truth generator and standard validator for AMOS 23 SEM diagrams.

Reads:
  - spec/diagram_standard.json  (project-agnostic typography, spacing, line & anchor rules)
  - <spec>.json                 (declarative constructs, indicators, structural paths, covariances)

Enforces STANDARDS.md:
  1. Validates identifiers, reference indicators, column coverage, exogenous covariances C(k,2),
     and optional .sav column presence.
  2. Auto-scales 96-DPI page canvas, shapes, arc clearance, and caption zone from font_size
     and longest label length using ratios in diagram_standard.json.
  3. Runs full geometric collision checks (shapes, arrows, crossings, east/west covariance arcs,
     Goodness of Fit caption zone) and exits non-zero if any check fails.
  4. Emits plan/layout.json, plan/assessment.html, the target VB.NET IPlugin, and HeadlessFit.vb.

Uses only relative paths from project root.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sys
import textwrap
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STANDARD = ROOT / "spec" / "diagram_standard.json"
DEFAULT_SPEC = ROOT / "spec" / "template_spec.json"
PLAN_DIR = ROOT / "plan"
SCRIPTS_DIR = ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from sav_utils import resolve_sav_path, validate_sav_dataset  # noqa: E402

VALID_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def round_even(v: float) -> float:
    return float(math.ceil(v / 2.0) * 2)


# --------------------------------------------------------------------------- dataclasses
@dataclass
class Shape:
    kind: str
    name: str
    cx: float
    cy: float
    w: float
    h: float
    role: str = ""

    @property
    def rx(self) -> float:
        return self.w / 2.0

    @property
    def ry(self) -> float:
        return self.h / 2.0


@dataclass
class Arrow:
    kind: str
    label: str
    src: str
    dst: str
    p1: tuple[float, float]
    p2: tuple[float, float]
    pos1: float
    pos2: float
    value: str = ""


@dataclass
class Curve:
    a: str
    b: str
    p1: tuple[float, float]
    p2: tuple[float, float]
    pos1: float
    pos2: float
    sagitta: float
    side: str


@dataclass
class Plan:
    page_w: float
    page_h: float
    font_size: float
    shapes: list[Shape] = field(default_factory=list)
    arrows: list[Arrow] = field(default_factory=list)
    curves: list[Curve] = field(default_factory=list)


def ellipse_pos(shape: Shape, tx: float, ty: float) -> float:
    t = math.atan2((ty - shape.cy) / shape.ry, (tx - shape.cx) / shape.rx)
    pos = 0.5 + t / (2.0 * math.pi)
    pos = pos - math.floor(pos)
    return 1.0 if pos < 1e-4 else pos


def ellipse_point(shape: Shape, pos: float) -> tuple[float, float]:
    t = (pos - 0.5) * 2.0 * math.pi
    return shape.cx + shape.rx * math.cos(t), shape.cy + shape.ry * math.sin(t)


def rect_point(shape: Shape, pos: float, rect_east: float, rect_west: float) -> tuple[float, float]:
    if abs(pos - rect_east) < 1e-6:
        return shape.cx + shape.rx, shape.cy
    if abs(pos - rect_west) < 1e-6:
        return shape.cx - shape.rx, shape.cy
    raise ValueError(f"Unsupported rect anchor {pos}")


# --------------------------------------------------------------------------- validation
def validate_spec(spec: dict[str, Any], std: dict[str, Any], check_sav: bool = True) -> list[str]:
    errors: list[str] = []
    constructs = spec.get("constructs", [])
    if not constructs:
        return ["'constructs' list is empty."]

    by_id: dict[str, dict[str, Any]] = {}
    seen_sav: set[str] = set()
    seen_err: set[str] = set()
    seen_dist: set[str] = set()

    for c in constructs:
        cid = c.get("id", "")
        if not VALID_IDENT.match(cid):
            errors.append(f"Invalid construct id '{cid}' (must match [A-Za-z_][A-Za-z0-9_]*).")
        if cid in by_id:
            errors.append(f"Duplicate construct id '{cid}'.")
        by_id[cid] = c

        inds = c.get("indicators", [])
        if len(inds) < 2:
            errors.append(f"Construct '{cid}' must have at least 2 indicators.")
        ref_row = c.get("ref_row", 0)
        if not (0 <= ref_row < len(inds)):
            errors.append(f"Construct '{cid}' ref_row={ref_row} out of range [0..{len(inds)-1}].")

        if c.get("role") == "endogenous":
            dname = c.get("disturbance", "")
            if not VALID_IDENT.match(dname):
                errors.append(f"Endogenous construct '{cid}' has invalid disturbance '{dname}'.")
            if dname in seen_dist:
                errors.append(f"Duplicate disturbance '{dname}'.")
            seen_dist.add(dname)

        for ind in inds:
            sv = ind.get("sav_var", "")
            ev = ind.get("err", "")
            if not VALID_IDENT.match(sv):
                errors.append(f"Construct '{cid}' has invalid sav_var '{sv}'.")
            if sv in seen_sav:
                errors.append(f"Duplicate sav_var '{sv}'.")
            seen_sav.add(sv)
            if not VALID_IDENT.match(ev):
                errors.append(f"Construct '{cid}' has invalid err '{ev}'.")
            if ev in seen_err:
                errors.append(f"Duplicate error variable '{ev}'.")
            seen_err.add(ev)

    diag = spec.get("diagram", {})
    left_col = diag.get("left_column", [])
    right_col = diag.get("right_column", [])
    if set(left_col) & set(right_col):
        errors.append("Overlap between left_column and right_column constructs.")
    if set(left_col) | set(right_col) != set(by_id.keys()):
        errors.append("left_column + right_column must cover every construct exactly once.")

    for sp in spec.get("structural_paths", []):
        if sp["from"] not in by_id or sp["to"] not in by_id:
            errors.append(f"Structural path {sp.get('id')} references unknown construct.")
        elif by_id[sp["to"]].get("role") != "endogenous":
            errors.append(f"Structural path {sp.get('id')} target '{sp['to']}' is not marked role='endogenous'.")

    exog_ids = [c["id"] for c in constructs if c.get("role") == "exogenous"]
    cov_pairs = set()
    for cv in spec.get("covariances", []):
        a, b = cv.get("a", ""), cv.get("b", "")
        if a not in by_id or b not in by_id or a == b:
            errors.append(f"Invalid covariance pair ({a}, {b}).")
        else:
            cov_pairs.add(tuple(sorted((a, b))))

    cov_policy = diag.get("covariance_policy")
    has_enforce_flag = "enforce_full_exogenous_covariances" in diag
    if cov_policy == "all":
        enforce_cov = True
    elif cov_policy == "none":
        enforce_cov = False
        if cov_pairs:
            errors.append("covariance_policy='none' conflicts with non-empty 'covariances' list.")
    elif cov_policy in ("explicit", "unspecified"):
        enforce_cov = False
    elif has_enforce_flag:
        enforce_cov = bool(diag["enforce_full_exogenous_covariances"])
    else:
        enforce_cov = False

    if enforce_cov:
        expected_pairs = {tuple(sorted(p)) for p in itertools.combinations(exog_ids, 2)}
        missing = expected_pairs - cov_pairs
        if missing:
            errors.append(
                f"Standard violation: missing exogenous covariance pair(s): {sorted(missing)} "
                "(set covariance_policy='none'/'explicit' or enforce_full_exogenous_covariances=false only if explicitly requested)."
            )

    if check_sav and spec.get("sav_file"):
        sav_path, _ = resolve_sav_path(None, spec.get("sav_file"), ROOT)
        ds_report = validate_sav_dataset(sav_path, spec, source_label="spec")
        errors.extend(ds_report["errors"])

    return errors


def wrap_label(text: str, width: int) -> list[str]:
    clean = " ".join(text.split())
    if not clean:
        return [""]
    lines = textwrap.wrap(clean, width=width, break_long_words=False, break_on_hyphens=False)
    return lines or [clean]


def vb_multiline_expr(text: str, width: int) -> str:
    lines = wrap_label(text, width)
    return " & vbCrLf & ".join('"' + ln.replace('"', '""') + '"' for ln in lines)


# --------------------------------------------------------------------------- adaptive geometry
def compute_geometry(spec: dict[str, Any], std: dict[str, Any]) -> dict[str, Any]:
    ty = std["typography"]
    ls = std["layout_spacing"]
    la = std["lines_and_anchors"]
    diag = spec["diagram"]

    font_size = float(diag.get("font_size", ty["default_font_size"]))
    param_font = float(diag.get("param_font_size", font_size * ty["param_font_ratio"]))
    constructs = {c["id"]: c for c in spec["constructs"]}
    left_col: list[str] = diag["left_column"]
    right_col: list[str] = diag["right_column"]

    rect_wrap_chars = int(ty.get("rect_wrap_chars", 24))
    oval_wrap_chars = int(ty.get("oval_wrap_chars", 16))
    wrapped_inds = [wrap_label(ind["title"], rect_wrap_chars) for c in constructs.values() for ind in c["indicators"]]
    wrapped_facs = [wrap_label(c["title"], oval_wrap_chars) for c in constructs.values()]

    max_rect_line_chars = max(len(ln) for lines in wrapped_inds for ln in lines)
    max_rect_lines = max(len(lines) for lines in wrapped_inds)
    max_fac_line_chars = max(len(ln) for lines in wrapped_facs for ln in lines)
    max_fac_lines = max(len(lines) for lines in wrapped_facs)
    max_ind_rows = max(len(c["indicators"]) for c in constructs.values())
    n_bands = max(len(left_col), len(right_col))

    rect_w = round_even(max(
        ty["min_rect_width"],
        font_size * ty["rect_width_per_font"],
        max_rect_line_chars * font_size * 0.54 + font_size * 1.4,
    ))
    rect_h = round_even(max(
        ty["min_rect_height"],
        font_size * ty["rect_height_per_font"],
        font_size * (1.35 * max_rect_lines + 0.55),
    ))
    oval_w = round_even(max(
        ty["min_oval_width"],
        font_size * ty["oval_width_per_font"],
        max_fac_line_chars * font_size * 0.68 + font_size * 2.4,
    ))
    oval_h = round_even(max(
        ty["min_oval_height"],
        font_size * ty["oval_height_per_font"],
        font_size * (1.4 * max_fac_lines + 1.6),
    ))
    err_d = round_even(max(ty["min_err_diameter"], font_size * ty["err_diameter_per_font"]))
    err_gap = round_even(max(ty["min_err_gap"], font_size * ty["err_gap_per_font"]))
    row_step = round_even(rect_h + max(ty["min_row_gap"], font_size * ty["row_gap_per_font"]))

    block_h = (max_ind_rows - 1) * row_step + rect_h
    top_margin = round_even(max(ls["min_top_margin"], font_size * ls["top_margin_per_font"]))
    band_gap = round_even(max(ls["min_band_gap"], font_size * ls["band_gap_per_font"]))
    band_step = block_h + band_gap
    band_y = [top_margin + block_h / 2.0 + k * band_step for k in range(n_bands)]

    dist_offset = round_even(max(ls["min_disturbance_offset"], err_d / 2.0 + font_size * ls["disturbance_extra_per_font"]))

    sag_ratio = float(la["cov_sagitta_ratio"])
    left_pos = {cid: idx for idx, cid in enumerate(left_col)}
    max_cov_span = 0.0
    for cv in spec.get("covariances", []):
        if cv["a"] in left_pos and cv["b"] in left_pos:
            span = abs(band_y[left_pos[cv["a"]]] - band_y[left_pos[cv["b"]]])
            if span > max_cov_span:
                max_cov_span = span
    min_arc_clearance = sag_ratio * max(0.0, max_cov_span - oval_h) + max(24.0, font_size * 1.3)
    oval_rect_gap = round_even(max(ls["min_oval_rect_gap"], font_size * ls["oval_rect_gap_per_font"], min_arc_clearance - oval_w / 2.0))

    outer_margin = round_even(max(ls["min_outer_margin"], font_size * ls["outer_margin_per_font"]))
    east_arc_corridor = (sag_ratio * max_cov_span + max(30.0, font_size * 1.5)) if max_cov_span > 0 else 0.0
    center_corridor = round_even(max(ls["min_center_corridor"], font_size * ls["center_corridor_per_font"], oval_w * 1.15, east_arc_corridor))

    left_rect_x = outer_margin + err_d + err_gap + rect_w / 2.0
    left_oval_x = left_rect_x + rect_w / 2.0 + oval_rect_gap + oval_w / 2.0
    right_oval_x = left_oval_x + oval_w + center_corridor
    right_rect_x = right_oval_x + oval_w / 2.0 + oval_rect_gap + rect_w / 2.0
    needed_w = right_rect_x + rect_w / 2.0 + err_gap + err_d + outer_margin

    page_w_in = int(math.ceil(needed_w / 96.0))
    page_w = page_w_in * 96.0
    shift_x = round_even((page_w - needed_w) / 2.0)
    left_rect_x += shift_x
    left_oval_x += shift_x
    right_oval_x += shift_x
    right_rect_x += shift_x

    # Choose Goodness of Fit caption placement based on column symmetry
    has_caption = diag.get("goodness_of_fit_caption", True)
    cap_w = font_size * 11.5
    cap_h = font_size * 14.5
    if has_caption and len(right_col) < len(left_col):
        # Asymmetric open bottom-right quadrant
        cap_x = right_oval_x - round_even(font_size * 3.0)
        cap_y = band_y[len(left_col) - 1] - round_even(font_size * 5.5)
        extra_bottom = round_even(max(ls["min_bottom_margin"], font_size * ls["bottom_margin_per_font"]))
    elif has_caption:
        # Symmetric columns: reserve clean corridor centered below both columns
        cap_x = round_even(page_w / 2.0 - cap_w / 2.0)
        cap_y = band_y[-1] + block_h / 2.0 + round_even(font_size * 1.8)
        extra_bottom = cap_h + round_even(font_size * 3.2)
    else:
        cap_x, cap_y = 0.0, 0.0
        extra_bottom = round_even(max(ls["min_bottom_margin"], font_size * ls["bottom_margin_per_font"]))

    needed_h = band_y[-1] + block_h / 2.0 + extra_bottom
    page_h_in = int(math.ceil(needed_h / 96.0))
    page_h = page_h_in * 96.0

    return {
        "font_size": font_size,
        "param_font_size": param_font,
        "page_w_in": page_w_in,
        "page_h_in": page_h_in,
        "page_w": page_w,
        "page_h": page_h,
        "rect_w": rect_w,
        "rect_h": rect_h,
        "oval_w": oval_w,
        "oval_h": oval_h,
        "err_d": err_d,
        "err_gap": err_gap,
        "row_step": row_step,
        "left_rect_x": left_rect_x,
        "right_rect_x": right_rect_x,
        "left_oval_x": left_oval_x,
        "right_oval_x": right_oval_x,
        "band_y": band_y,
        "dist_offset": dist_offset,
        "cap_x": cap_x,
        "cap_y": cap_y,
        "cap_w": cap_w,
        "cap_h": cap_h,
    }


def resolve_cov_anchors(cv: dict[str, Any], col_order: list[str], std: dict[str, Any]) -> tuple[float, float]:
    if "pos1" in cv and "pos2" in cv:
        return float(cv["pos1"]), float(cv["pos2"])
    la = std["lines_and_anchors"]
    if cv["a"] in col_order and cv["b"] in col_order:
        gap = abs(col_order.index(cv["a"]) - col_order.index(cv["b"]))
        pair = la["cov_adjacent_pos"] if gap <= 1 else la["cov_spanned_pos"]
        return float(pair[0]), float(pair[1])
    return float(la["cov_adjacent_pos"][0]), float(la["cov_adjacent_pos"][1])


def build_plan(spec: dict[str, Any], g: dict[str, Any], std: dict[str, Any]) -> Plan:
    la = std["lines_and_anchors"]
    rect_east = float(la["rect_anchors"]["east"])
    rect_west = float(la["rect_anchors"]["west"])
    el_east = float(la["ellipse_anchors"]["east"])
    el_west = float(la["ellipse_anchors"]["west"])
    el_north = float(la["ellipse_anchors"]["north"])
    el_south = float(la["ellipse_anchors"]["south"])
    sag_ratio = float(la["cov_sagitta_ratio"])

    plan = Plan(page_w=g["page_w"], page_h=g["page_h"], font_size=g["font_size"])
    constructs = {c["id"]: c for c in spec["constructs"]}
    left_col = spec["diagram"]["left_column"]
    right_col = spec["diagram"]["right_column"]
    ell: dict[str, Shape] = {}

    def add_block(cid: str, is_left: bool, oval_x: float, y0: float) -> None:
        c = constructs[cid]
        rect_x = g["left_rect_x"] if is_left else g["right_rect_x"]
        err_x = rect_x + (-1 if is_left else 1) * (g["rect_w"] / 2.0 + g["err_gap"] + g["err_d"] / 2.0)
        oval = Shape("ellipse", c["title"], oval_x, y0, g["oval_w"], g["oval_h"], role="construct")
        plan.shapes.append(oval)
        ell[cid] = oval

        inds = c["indicators"]
        mid = (len(inds) - 1) / 2.0
        for j, ind in enumerate(inds):
            y = y0 + (j - mid) * g["row_step"]
            box = Shape("rect", ind["title"], rect_x, y, g["rect_w"], g["rect_h"], role="indicator")
            plan.shapes.append(box)
            err = Shape("ellipse", ind["err"], err_x, y, g["err_d"], g["err_d"], role="error")
            plan.shapes.append(err)

            edge_pos = rect_east if is_left else rect_west
            ex, ey = rect_point(box, edge_pos, rect_east, rect_west)
            p1pos = ellipse_pos(oval, ex, ey)
            plan.arrows.append(Arrow(
                "loading", f"{c['title']} -> {box.name}", oval.name, box.name,
                ellipse_point(oval, p1pos), (ex, ey), p1pos, edge_pos,
                "1" if j == c["ref_row"] else ""
            ))
            front = el_east if is_left else el_west
            back_edge = rect_west if is_left else rect_east
            bx, by = rect_point(box, back_edge, rect_east, rect_west)
            plan.arrows.append(Arrow(
                "error", f"{ind['err']} -> {box.name}", ind["err"], box.name,
                ellipse_point(err, front), (bx, by), front, back_edge, "1"
            ))

    for k in reversed(range(len(left_col))):
        add_block(left_col[k], True, g["left_oval_x"], g["band_y"][k])
    # Distribute right column evenly across available vertical span
    if len(right_col) == len(left_col):
        right_y = [g["band_y"][k] for k in range(len(right_col))]
    elif len(right_col) == 1:
        right_y = [(g["band_y"][0] + g["band_y"][-1]) / 2.0]
    else:
        max_idx = max(1, len(g["band_y"]) - (2 if spec["diagram"].get("goodness_of_fit_caption", True) and len(left_col) >= 3 else 1))
        min_idx = 1 if (len(left_col) >= 5 and len(right_col) <= 2) else 0
        y_top = g["band_y"][min_idx]
        y_bot = g["band_y"][max_idx]
        right_y = [y_top + k * (y_bot - y_top) / (len(right_col) - 1) for k in range(len(right_col))]
    for k in range(len(right_col)):
        add_block(right_col[k], False, g["right_oval_x"], right_y[k])

    for sp in spec["structural_paths"]:
        a, b = ell[sp["from"]], ell[sp["to"]]
        pa, pb = ellipse_pos(a, b.cx, b.cy), ellipse_pos(b, a.cx, a.cy)
        plan.arrows.append(Arrow(
            "structural", f"{sp['id']}: {a.name} -> {b.name}", a.name, b.name,
            ellipse_point(a, pa), ellipse_point(b, pb), pa, pb
        ))

    left_set = set(left_col)
    for cid, c in constructs.items():
        if c.get("role") != "endogenous":
            continue
        o = ell[cid]
        dx = -o.rx * 0.65 if cid in left_set else o.rx * 0.75
        cx, cy = o.cx + dx, o.cy - o.ry - g["dist_offset"]
        circ = Shape("ellipse", c["disturbance"], cx, cy, g["err_d"], g["err_d"], role="disturbance")
        plan.shapes.append(circ)
        pa, pb = ellipse_pos(circ, o.cx, o.cy), ellipse_pos(o, cx, cy)
        plan.arrows.append(Arrow(
            "disturbance", f"{circ.name} -> {o.name}", circ.name, o.name,
            ellipse_point(circ, pa), ellipse_point(o, pb), pa, pb, "1"
        ))

    for cv in spec.get("covariances", []):
        c1, c2 = ell[cv["a"]], ell[cv["b"]]
        lo, up = (c1, c2) if c1.cy > c2.cy else (c2, c1)
        pos1, pos2 = resolve_cov_anchors(cv, left_col, std)
        # Forward-corridor east arc: upper construct leaves at pos1 (south-east), lower at pos2 (north-east)
        a, b = ellipse_point(lo, pos2), ellipse_point(up, pos1)
        chord = math.hypot(b[0] - a[0], b[1] - a[1])
        plan.curves.append(Curve(lo.name, up.name, a, b, pos1, pos2, sag_ratio * chord, "east"))

    return plan


# --------------------------------------------------------------------------- collision gate
def seg_hits_rect(p, q, r: Shape, pad: float = 1.0, samples: int = 60) -> bool:
    for i in range(samples + 1):
        t = i / samples
        x = p[0] + (q[0] - p[0]) * t
        y = p[1] + (q[1] - p[1]) * t
        if abs(x - r.cx) < r.rx - pad and abs(y - r.cy) < r.ry - pad:
            return True
    return False


def seg_hits_ellipse(p, q, e: Shape, shrink: float = 0.97, samples: int = 80) -> bool:
    for i in range(samples + 1):
        t = i / samples
        x = p[0] + (q[0] - p[0]) * t
        y = p[1] + (q[1] - p[1]) * t
        if ((x - e.cx) / (e.rx * shrink)) ** 2 + ((y - e.cy) / (e.ry * shrink)) ** 2 < 1.0:
            return True
    return False


def arc_points(c: Curve, side: str, n: int = 60):
    ax, ay = c.p1
    bx, by = c.p2
    mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
    dx, dy = bx - ax, by - ay
    ln = math.hypot(dx, dy)
    nx, ny = dy / ln, -dx / ln
    if (side == "east" and nx < 0) or (side == "west" and nx > 0):
        nx, ny = -nx, -ny
    cx, cy = mx + nx * c.sagitta * 2.0, my + ny * c.sagitta * 2.0
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * ax + 2 * (1 - t) * t * cx + t * t * bx
        y = (1 - t) ** 2 * ay + 2 * (1 - t) * t * cy + t * t * by
        pts.append((x, y))
    return pts, (cx, cy)


def run_checks(plan: Plan, g: dict[str, Any], has_caption: bool) -> list[str]:
    problems: list[str] = []
    rects = [s for s in plan.shapes if s.kind == "rect"]
    ellipses = [s for s in plan.shapes if s.kind == "ellipse"]

    for s in plan.shapes:
        if s.cx - s.rx < 0 or s.cx + s.rx > plan.page_w or s.cy - s.ry < 0 or s.cy + s.ry > plan.page_h:
            problems.append(f"outside page: {s.name}")

    for i, a in enumerate(plan.shapes):
        for b in plan.shapes[i + 1:]:
            if abs(a.cx - b.cx) < a.rx + b.rx - 0.5 and abs(a.cy - b.cy) < a.ry + b.ry - 0.5:
                problems.append(f"overlap: {a.name} / {b.name}")

    for ar in plan.arrows:
        for r in rects:
            if r.name not in (ar.dst, ar.src) and seg_hits_rect(ar.p1, ar.p2, r):
                problems.append(f"arrow '{ar.label}' crosses box '{r.name}'")
        if ar.kind in ("structural", "disturbance"):
            for e in ellipses:
                if e.name not in (ar.src, ar.dst) and seg_hits_ellipse(ar.p1, ar.p2, e):
                    problems.append(f"arrow '{ar.label}' crosses ellipse '{e.name}'")

    def cross(p1, p2, p3, p4):
        def ccw(a, b, c):
            return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])
        return ccw(p3, p4, p1) * ccw(p3, p4, p2) < 0 and ccw(p1, p2, p3) * ccw(p1, p2, p4) < 0

    loads = [a for a in plan.arrows if a.kind == "loading"]
    for i, a in enumerate(loads):
        for b in loads[i + 1:]:
            if a.src == b.src and cross(a.p1, a.p2, b.p1, b.p2):
                problems.append(f"loading arrows cross: {a.dst} / {b.dst}")

    for c in plan.curves:
        for side in ("east", "west"):
            pts, _ = arc_points(c, side)
            for r in rects:
                if any(abs(x - r.cx) < r.rx and abs(y - r.cy) < r.ry for x, y in pts):
                    problems.append(f"covariance {c.a}<->{c.b} ({side} arc) touches box '{r.name}'")
                    break
        pts, _ = arc_points(c, c.side)
        for e in ellipses:
            if e.role == "construct" and e.name not in (c.a, c.b):
                if any(((x - e.cx) / e.rx) ** 2 + ((y - e.cy) / e.ry) ** 2 < 1.0 for x, y in pts):
                    problems.append(f"covariance {c.a}<->{c.b} ({c.side} arc) touches construct '{e.name}'")

    if has_caption:
        cap_w, cap_h = g["cap_w"], g["cap_h"]
        cap_cx, cap_cy = g["cap_x"] + cap_w / 2.0, g["cap_y"] + cap_h / 2.0
        if g["cap_x"] < 0 or g["cap_y"] < 0 or g["cap_x"] + cap_w > plan.page_w or g["cap_y"] + cap_h > plan.page_h:
            problems.append("Goodness of Fit caption exceeds page bounds")
        for s in plan.shapes:
            if abs(cap_cx - s.cx) < (cap_w / 2.0 + s.rx - 2.0) and abs(cap_cy - s.cy) < (cap_h / 2.0 + s.ry - 2.0):
                problems.append(f"Goodness of Fit caption overlaps shape '{s.name}'")

    return problems


# --------------------------------------------------------------------------- HTML preview
def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;")


def render_html(plan: Plan, problems: list[str], g: dict[str, Any], has_caption: bool) -> str:
    font_size = g["font_size"]
    out: list[str] = []
    for c in plan.curves:
        for side in ("east", "west"):
            _, ctrl = arc_points(c, side)
            d = f"M {c.p1[0]:.1f} {c.p1[1]:.1f} Q {ctrl[0]:.1f} {ctrl[1]:.1f} {c.p2[0]:.1f} {c.p2[1]:.1f}"
            cls = "cov" if side == c.side else "cov-alt"
            out.append(f'<path class="{cls}" data-side="{side}" d="{d}"/>')
    for a in plan.arrows:
        out.append(
            f'<line class="arr {a.kind}" x1="{a.p1[0]:.1f}" y1="{a.p1[1]:.1f}" '
            f'x2="{a.p2[0]:.1f}" y2="{a.p2[1]:.1f}"><title>{esc(a.label)} '
            f'(pos1={a.pos1:.3f}, pos2={a.pos2:.3f})</title></line>'
        )
        if a.value:
            mx = a.p1[0] * 0.4 + a.p2[0] * 0.6
            my = a.p1[1] * 0.4 + a.p2[1] * 0.6
            out.append(f'<text class="one" x="{mx - 4:.1f}" y="{my - 5:.1f}">{a.value}</text>')
    for s in plan.shapes:
        if s.kind == "rect":
            out.append(f'<rect class="node" x="{s.cx - s.rx:.1f}" y="{s.cy - s.ry:.1f}" width="{s.w:.1f}" height="{s.h:.1f}"/>')
            out.append(f'<text class="lbl" x="{s.cx:.1f}" y="{s.cy:.1f}">{esc(s.name)}</text>')
        else:
            out.append(f'<ellipse class="node" cx="{s.cx:.1f}" cy="{s.cy:.1f}" rx="{s.rx:.1f}" ry="{s.ry:.1f}"/>')
            fs = "lbl" if s.role == "construct" else "lbl small"
            out.append(f'<text class="{fs}" x="{s.cx:.1f}" y="{s.cy:.1f}">{esc(s.name)}</text>')
    if has_caption:
        out.append(
            f'<rect x="{g["cap_x"]:.1f}" y="{g["cap_y"]:.1f}" width="{g["cap_w"]:.1f}" height="{g["cap_h"]:.1f}" '
            'fill="none" stroke="#6688aa" stroke-dasharray="6 4" stroke-width="1.2"/>'
        )
        out.append(
            f'<text x="{g["cap_x"] + 8:.1f}" y="{g["cap_y"] + font_size:.1f}" font-size="{font_size:.0f}" fill="#334">'
            'Goodness of Fit (CMIN / DF / CFI / RMSEA)</text>'
        )

    svg_body = "\n".join(out)
    status = "<span class='ok'>0 tabrakan terdeteksi</span>" if not problems else f"<span class='bad'>{len(problems)} masalah</span>"
    plist = "".join(f"<li>{esc(p)}</li>" for p in problems) or "<li>Tidak ada.</li>"
    zoom = min(0.85, max(0.42, round(1050.0 / plan.page_w, 2)))
    return f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="UTF-8"><title>Rencana Diagram SEM Standar (Font {font_size:.0f})</title>
<style>
body{{font-family:Arial,sans-serif;margin:0;background:#f3f3f3;color:#111}}
header{{padding:10px 16px;background:#fff;border-bottom:1px solid #ccc}}
.ok{{color:#0a7a2a;font-weight:bold}} .bad{{color:#b00020;font-weight:bold}}
.bar{{display:flex;gap:16px;align-items:center;flex-wrap:wrap;font-size:13px;margin-top:6px}}
main{{display:flex;gap:12px;padding:12px}}
#paper{{background:#fff;box-shadow:0 1px 8px rgba(0,0,0,.3);flex:none}}
aside{{font-size:12px;max-width:340px}}
.node{{fill:url(#g);stroke:#000;stroke-width:1.4}}
.lbl{{font-size:{font_size:.0f}px;text-anchor:middle;dominant-baseline:central;fill:#000;pointer-events:none}}
.lbl.small{{font-size:{font_size:.0f}px}}
.arr{{stroke:#000;stroke-width:1.4;marker-end:url(#a)}}
.arr.structural{{stroke-width:1.6}}
.cov{{fill:none;stroke:#000;stroke-width:1.4;marker-start:url(#ar);marker-end:url(#a)}}
.cov-alt{{fill:none;stroke:#c33;stroke-width:1;stroke-dasharray:5 4;opacity:.0}}
body.showalt .cov-alt{{opacity:.85}}
.one{{font-size:{font_size:.0f}px;fill:#000;paint-order:stroke;stroke:#fff;stroke-width:2.5px}}
</style></head><body>
<header><b>Rencana tata letak Amos terverifikasi standar ({plan.page_w:.0f} x {plan.page_h:.0f} px, font {font_size:.0f})</b>
<div class="bar"><span>{status}</span>
<button onclick="document.body.classList.toggle('showalt')">Tampilkan/sembunyikan lengkung alternatif</button></div></header>
<main>
<div id="paper"><svg id="svg" width="{plan.page_w:.0f}" height="{plan.page_h:.0f}" viewBox="0 0 {plan.page_w:.0f} {plan.page_h:.0f}" style="zoom:{zoom}">
<defs>
<marker id="a" viewBox="0 0 13 9" refX="12.5" refY="4.5" markerWidth="11.5" markerHeight="8" orient="auto"><path d="M0 .6L12.5 4.5 0 8.4Z"/></marker>
<marker id="ar" viewBox="0 0 13 9" refX=".5" refY="4.5" markerWidth="11.5" markerHeight="8" orient="auto"><path d="M12.5 .6L0 4.5 12.5 8.4Z"/></marker>
<linearGradient id="g" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#DCE7F3"/><stop offset=".45" stop-color="#E6E3F6"/><stop offset="1" stop-color="#F2DAF7"/></linearGradient>
</defs>
{svg_body}
</svg></div>
<aside><h4>Pemeriksaan Standar Otomatis</h4><ul>{plist}</ul></aside>
</main></body></html>
"""


# --------------------------------------------------------------------------- VB.NET emitters
def emit_plugin_vb(spec: dict[str, Any], g: dict[str, Any], std: dict[str, Any], staged_sav_name: str = "") -> str:
    la = std["lines_and_anchors"]
    sd = std["statistical_defaults"]
    constructs = spec["constructs"]
    idx_by_id = {c["id"]: i for i, c in enumerate(constructs)}
    max_inds = max(len(c["indicators"]) for c in constructs)
    sav_leaf = staged_sav_name or (Path(spec["sav_file"]).name if spec.get("sav_file") else "")
    ns_tag = spec.get("root_namespace", "StandardSEM")

    rect_wrap_chars = int(std["typography"].get("rect_wrap_chars", 24))
    oval_wrap_chars = int(std["typography"].get("oval_wrap_chars", 16))

    def pad_grid(key: str, wrap_w: int = 0) -> str:
        rows = []
        for c in constructs:
            vals = [ind[key] for ind in c["indicators"]] + [""] * (max_inds - len(c["indicators"]))
            if wrap_w > 0:
                cells = [vb_multiline_expr(v, wrap_w) if v else '""' for v in vals]
            else:
                cells = ['"' + v.replace('"', '""') + '"' for v in vals]
            rows.append("        {" + ", ".join(cells) + "}")
        return ", _\n".join(rows)

    left_list = spec["diagram"]["left_column"]
    right_list = spec["diagram"]["right_column"]
    left_idxs = ", ".join(str(idx_by_id[cid]) for cid in left_list)
    right_idxs = ", ".join(str(idx_by_id[cid]) for cid in right_list)
    band_y_str = ", ".join(f"{y:.1f}" for y in g["band_y"])
    if len(right_list) == len(left_list):
        right_y_vals = [g["band_y"][k] for k in range(len(right_list))]
    elif len(right_list) == 1:
        right_y_vals = [(g["band_y"][0] + g["band_y"][-1]) / 2.0]
    else:
        max_idx = max(1, len(g["band_y"]) - (2 if spec["diagram"].get("goodness_of_fit_caption", True) and len(left_list) >= 3 else 1))
        min_idx = 1 if (len(left_list) >= 5 and len(right_list) <= 2) else 0
        y_top, y_bot = g["band_y"][min_idx], g["band_y"][max_idx]
        right_y_vals = [y_top + k * (y_bot - y_top) / (len(right_list) - 1) for k in range(len(right_list))]
    right_band_y_str = ", ".join(f"{y:.1f}" for y in right_y_vals)

    struct_lines = "\n".join(
        f"        DrawStructuralPath(factors({idx_by_id[sp['from']]}), factors({idx_by_id[sp['to']]}))   ' {sp['id']}: {sp['from']} -> {sp['to']}"
        for sp in spec["structural_paths"]
    )
    cov_lines = []
    for cv in spec.get("covariances", []):
        ia, ib = idx_by_id[cv["a"]], idx_by_id[cv["b"]]
        if cv["a"] in left_list and cv["b"] in left_list and left_list.index(cv["a"]) > left_list.index(cv["b"]):
            ia, ib = ib, ia
        pos1, pos2 = resolve_cov_anchors(cv, left_list, std)
        cov_lines.append(
            f"        DrawCurvedCovariance(factors({ia}), factors({ib}), {pos1}, {pos2})   ' {cv['a']} <-> {cv['b']}"
        )

    dist_lines = []
    left_set = set(left_list)
    for i, c in enumerate(constructs):
        if c.get("role") != "endogenous":
            continue
        if c["id"] in left_set:
            b_idx = left_list.index(c["id"])
            x_expr = "leftOvalX - rx * 0.65"
            y_expr = f"bandY({b_idx})"
        else:
            b_idx = right_list.index(c["id"])
            x_expr = "rightOvalX + rx * 0.75"
            y_expr = f"rightBandY({b_idx})"
        dist_lines.append(
            f"        AddDisturbance(factors({i}), \"{c['disturbance']}\", {x_expr}, {y_expr} - ry - {g['dist_offset']:.1f})"
        )

    gof_call = "        AddGoodnessOfFitCaption()" if spec["diagram"].get("goodness_of_fit_caption", True) else ""
    std_cmd = r'text = System.Text.RegularExpressions.Regex.Replace(text, "(?m)^\$nostandardized\b", "$standardized")' if sd["enable_standardized_estimates"] else "' standardized disabled"
    smc_cmd = r'text = System.Text.RegularExpressions.Regex.Replace(text, "(?m)^\$nosmc\b", "$smc")' if sd["enable_smc"] else "' smc disabled"
    rect_east = la["rect_anchors"]["east"]
    rect_west = la["rect_anchors"]["west"]
    el_east = la["ellipse_anchors"]["east"]
    el_west = la["ellipse_anchors"]["west"]

    return f"""Option Strict On
Option Explicit On

Imports System
Imports System.Collections.Generic
Imports System.Diagnostics
Imports System.IO
Imports System.Reflection
Imports Microsoft.VisualBasic
Imports MiscAmosTypes
Imports AmosGraphics

' GENERATED AUTOMATICALLY BY scripts/generate_from_spec.py.
' Rules: spec/diagram_standard.json | Namespace: {ns_tag}
Public Class CPlugin
    Implements IPlugin

    Private Const FactorCount As Integer = {len(constructs)}
    Private Const MaxIndicators As Integer = {max_inds}
    Private Const RectEast As Double = {rect_east}
    Private Const RectWest As Double = {rect_west}
    Private Const SavFileName As String = "{sav_leaf}"

    Private ReadOnly factorCode() As String = {{ {", ".join('"' + c["id"] + '"' for c in constructs)} }}
    Private ReadOnly factorTitle() As String = {{ {", ".join(vb_multiline_expr(c["title"], oval_wrap_chars) for c in constructs)} }}
    Private ReadOnly indCount() As Integer = {{ {", ".join(str(len(c["indicators"])) for c in constructs)} }}
    Private ReadOnly refIndicatorRow() As Integer = {{ {", ".join(str(c["ref_row"]) for c in constructs)} }}

    Private ReadOnly indicatorTitle(,) As String = {{ _
{pad_grid("title", rect_wrap_chars)} _
    }}

    Private ReadOnly savVarName(,) As String = {{ _
{pad_grid("sav_var")} _
    }}

    Private ReadOnly errorLabel(,) As String = {{ _
{pad_grid("err")} _
    }}

    Private ReadOnly leftColumn() As Integer = {{{left_idxs}}}
    Private ReadOnly rightColumn() As Integer = {{{right_idxs}}}
    Private factors(FactorCount - 1) As PDElement

    Private rectWidth As Double = {g["rect_w"]:.1f}
    Private rectHeight As Double = {g["rect_h"]:.1f}
    Private ovalWidth As Double = {g["oval_w"]:.1f}
    Private ovalHeight As Double = {g["oval_h"]:.1f}
    Private errSize As Double = {g["err_d"]:.1f}
    Private errGap As Double = {g["err_gap"]:.1f}
    Private rowStep As Double = {g["row_step"]:.1f}
    Private leftRectX As Double = {g["left_rect_x"]:.1f}
    Private rightRectX As Double = {g["right_rect_x"]:.1f}
    Private leftOvalX As Double = {g["left_oval_x"]:.1f}
    Private rightOvalX As Double = {g["right_oval_x"]:.1f}
    Private leftErrX As Double
    Private rightErrX As Double
    Private bandY() As Double = {{{band_y_str}}}
    Private rightBandY() As Double = {{{right_band_y_str}}}
    Private nameFont As Single = {g["font_size"]:.1f}F
    Private factorFont As Single = {g["font_size"]:.1f}F
    Private errFont As Single = {g["font_size"]:.1f}F
    Private paramFont As Single = {g["param_font_size"]:.1f}F

    Public Function MainSub() As Integer Implements IPlugin.MainSub
        pd.IsViewingPathDiagram = True
        pd.PopAllButtons()

        If CreateLandscapeDiagram() Then
            Return 0
        End If

        leftErrX = leftRectX - (rectWidth / 2.0 + errGap + errSize / 2.0)
        rightErrX = rightRectX + (rectWidth / 2.0 + errGap + errSize / 2.0)

        Dim k As Integer
        Dim factorIndex As Integer

        For k = leftColumn.Length - 1 To 0 Step -1
            factorIndex = leftColumn(k)
            factors(factorIndex) = DrawBlock(factorIndex, True, leftOvalX, bandY(k), leftRectX, leftErrX)
        Next k

        For k = 0 To rightColumn.Length - 1
            factorIndex = rightColumn(k)
            factors(factorIndex) = DrawBlock(factorIndex, False, rightOvalX, rightBandY(k), rightRectX, rightErrX)
        Next k

{struct_lines}

{chr(10).join(cov_lines)}
        pd.PopAllButtons()

        Dim rx As Double = ovalWidth / 2.0
        Dim ry As Double = ovalHeight / 2.0
{chr(10).join(dist_lines)}

{gof_call}
        RepositionAllArrows()
        BindSavDataFile()
        FitDiagramToWindow()
        Return 0
    End Function

    Private Function FindSavFile(ByVal searchedPaths As List(Of String)) As String
        If String.IsNullOrEmpty(SavFileName) Then Return ""
        Try
            Dim asmLoc As String = Assembly.GetExecutingAssembly().Location
            If Not String.IsNullOrEmpty(asmLoc) Then
                Dim dllDir As String = Path.GetDirectoryName(asmLoc)
                Dim besideDll As String = Path.Combine(dllDir, SavFileName)
                searchedPaths.Add(besideDll)
                If File.Exists(besideDll) Then Return besideDll
                Dim rootData As String = Path.Combine(Path.GetDirectoryName(dllDir), "data", SavFileName)
                searchedPaths.Add(rootData)
                If File.Exists(rootData) Then Return rootData
            End If
        Catch ex As Exception
            Trace.WriteLine("AMOS SEM FindSavFile assembly lookup error: " & ex.Message)
        End Try
        Dim localSav As String = Path.Combine( _
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), _
            "AmosDevelopment", "Amos", "23", SavFileName)
        searchedPaths.Add(localSav)
        If File.Exists(localSav) Then Return localSav
        Return ""
    End Function

    Private Sub BindSavDataFile()
        If String.IsNullOrEmpty(SavFileName) Then Return
        Dim searchedPaths As New List(Of String)()
        Dim savPath As String = FindSavFile(searchedPaths)
        If String.IsNullOrEmpty(savPath) Then
            Dim missingMsg As String = _
                "AMOS SEM dataset binding failed: staged SPSS dataset could not be located." & vbCrLf & _
                "Expected dataset: " & SavFileName & vbCrLf & _
                "File exists: False" & vbCrLf & _
                "Searched paths:" & vbCrLf & String.Join(vbCrLf, searchedPaths.ToArray())
            Trace.WriteLine(missingMsg)
            MsgBox(missingMsg, MsgBoxStyle.Exclamation, "AMOS SEM Dataset Binding Error")
            Return
        End If
        Try
            pd.SetDataFile(1, cDatabaseFormat.mmSPSS, savPath, "", "", 0)
        Catch ex As Exception
            Dim bindMsg As String = _
                "AMOS rejected dataset binding." & vbCrLf & _
                "Expected dataset: " & SavFileName & vbCrLf & _
                "Resolved path: " & savPath & vbCrLf & _
                "File exists: " & File.Exists(savPath).ToString() & vbCrLf & _
                "Searched paths:" & vbCrLf & String.Join(vbCrLf, searchedPaths.ToArray()) & vbCrLf & _
                "AMOS Error: " & ex.Message
            Trace.WriteLine(bindMsg)
            MsgBox(bindMsg, MsgBoxStyle.Exclamation, "AMOS SEM Dataset Binding Error")
        End Try
    End Sub

    Private Function CreateLandscapeDiagram() As Boolean
        Try
            Dim source As String = Path.Combine( _
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), _
                "AmosDevelopment", "Amos", "23", "Templates", "English", "Normal.amt")
            If File.Exists(source) Then
                Dim text As String = File.ReadAllText(source, System.Text.Encoding.Unicode)
                text = System.Text.RegularExpressions.Regex.Replace(text, "(?m)^page_width=.*$", "page_width={g['page_w_in']}")
                text = System.Text.RegularExpressions.Regex.Replace(text, "(?m)^page_height=.*$", "page_height={g['page_h_in']}")
                {std_cmd}
                {smc_cmd}
                Dim target As String = Path.Combine(Path.GetTempPath(), "{ns_tag}-standard.amt")
                File.WriteAllText(target, text, System.Text.Encoding.Unicode)
                Return pd.FileNewWithTemplate(target, pd.PDSaveChangesEnum.pdPromptToSaveChanges)
            End If
        Catch
        End Try
        Return pd.FileNew(pd.PDSaveChangesEnum.pdPromptToSaveChanges)
    End Function

    Private Sub AddGoodnessOfFitCaption()
        Dim capText As String = _
            "Goodness of Fit" & vbCrLf & _
            "Chi Square = \\cmin" & vbCrLf & _
            "Prob = \\p" & vbCrLf & _
            "CMIN/DF = \\cmindf" & vbCrLf & _
            "DF = \\df" & vbCrLf & _
            "RMSEA = \\rmsea" & vbCrLf & _
            "GFI = \\gfi" & vbCrLf & _
            "AGFI = \\AGFI" & vbCrLf & _
            "TLI = \\tli" & vbCrLf & _
            "CFI = \\cfi" & vbCrLf & _
            "NFI = \\nfi"
        Dim cap As PDElement = pd.Caption(capText, {g['cap_x']:.1f}, {g['cap_y']:.1f}, PDElement.TitlePositionEnum.Left)
        If cap IsNot Nothing Then
            cap.NameFontSize = factorFont
        End If
    End Sub

    Private Function DrawBlock(ByVal factorIndex As Integer, _
                               ByVal isLeftColumn As Boolean, _
                               ByVal ovalX As Double, _
                               ByVal ovalY As Double, _
                               ByVal rectX As Double, _
                               ByVal errX As Double) As PDElement
        Dim factor As PDElement = pd.DiagramDrawUnobserved(ovalX, ovalY, ovalWidth, ovalHeight)
        factor.originX = ovalX
        factor.originY = ovalY
        factor.Width = ovalWidth
        factor.Height = ovalHeight
        factor.NameOrCaption = factorCode(factorIndex)
        factor.LongLabel = factorTitle(factorIndex)
        factor.NameFontSize = factorFont

        Dim n As Integer = indCount(factorIndex)
        Dim mid As Double = (n - 1) / 2.0
        Dim j As Integer
        For j = 0 To n - 1
            Dim y As Double = ovalY + (j - mid) * rowStep
            Dim observed As PDElement = pd.DiagramDrawObserved(rectX, y, rectWidth, rectHeight)
            observed.originX = rectX
            observed.originY = y
            observed.Width = rectWidth
            observed.Height = rectHeight
            observed.NameOrCaption = savVarName(factorIndex, j)
            observed.LongLabel = indicatorTitle(factorIndex, j)
            observed.NameFontSize = nameFont

            Dim loadParam As String = If(j = refIndicatorRow(factorIndex), "1", "")
            Dim edgeX As Double = If(isLeftColumn, rectX + rectWidth / 2.0, rectX - rectWidth / 2.0)
            Dim endPos As Double = If(isLeftColumn, RectEast, RectWest)
            Dim startPos As Double = AnchorTowards(ovalX, ovalY, ovalWidth / 2.0, ovalHeight / 2.0, edgeX, y)
            DrawAnchoredPath(factor, observed, startPos, endPos, loadParam)

            Dim errName As String = errorLabel(factorIndex, j)
            If isLeftColumn Then
                DrawUniqueCircle(observed, errName, errX, y, errSize, {el_east}, RectWest)
            Else
                DrawUniqueCircle(observed, errName, errX, y, errSize, {el_west}, RectEast)
            End If
        Next j
        Return factor
    End Function

    Private Function AnchorTowards(ByVal cx As Double, ByVal cy As Double, _
                                   ByVal rx As Double, ByVal ry As Double, _
                                   ByVal tx As Double, ByVal ty As Double) As Double
        Dim t As Double = Math.Atan2((ty - cy) / ry, (tx - cx) / rx)
        Dim pos As Double = 0.5 + t / (2.0 * Math.PI)
        pos = pos - Math.Floor(pos)
        If pos < 0.0001 Then pos = 1.0
        Return pos
    End Function

    Private Sub DrawStructuralPath(ByVal src As PDElement, ByVal dst As PDElement)
        Dim p1 As Double = AnchorTowards(src.originX, src.originY, src.Width / 2.0, src.Height / 2.0, dst.originX, dst.originY)
        Dim p2 As Double = AnchorTowards(dst.originX, dst.originY, dst.Width / 2.0, dst.Height / 2.0, src.originX, src.originY)
        DrawAnchoredPath(src, dst, p1, p2, "")
    End Sub

    Private Sub AddDisturbance(ByVal target As PDElement, ByVal caption As String, ByVal cx As Double, ByVal cy As Double)
        Dim r As Double = errSize / 2.0
        Dim p1 As Double = AnchorTowards(cx, cy, r, r, target.originX, target.originY)
        Dim p2 As Double = AnchorTowards(target.originX, target.originY, target.Width / 2.0, target.Height / 2.0, cx, cy)
        DrawUniqueCircle(target, caption, cx, cy, errSize, p1, p2)
    End Sub

    Private Sub DrawUniqueCircle(ByVal targetElement As PDElement, _
                                 ByVal caption As String, _
                                 ByVal cx As Double, ByVal cy As Double, ByVal diameter As Double, _
                                 ByVal posOnCircle As Double, ByVal posOnTarget As Double)
        Dim uniqueEl As PDElement = pd.DiagramDrawUnobserved(cx, cy, diameter, diameter)
        uniqueEl.originX = cx
        uniqueEl.originY = cy
        uniqueEl.Width = diameter
        uniqueEl.Height = diameter
        uniqueEl.NameOrCaption = caption
        uniqueEl.NameFontSize = errFont
        DrawAnchoredPath(uniqueEl, targetElement, posOnCircle, posOnTarget, "1")
    End Sub

    Private Function DrawAnchoredPath(ByVal src As PDElement, ByVal dst As PDElement, _
                                      ByVal pos1 As Double, ByVal pos2 As Double, ByVal paramValue As String) As PDElement
        Dim p As PDElement = pd.DiagramDrawPath(src.originX, src.originY, dst.originX, dst.originY)
        If p IsNot Nothing Then
            p.position1 = pos1
            p.position2 = pos2
            p.ParameterFontSize = paramFont
            If paramValue <> "" Then p.Value1 = paramValue
        End If
        Return p
    End Function

    Private Sub DrawCurvedCovariance(ByVal upper As PDElement, ByVal lower As PDElement, _
                                     ByVal pos1 As Double, ByVal pos2 As Double)
        Dim prevCount As Integer = pd.PDElements.Count
        pd.DiagramDrawCovariance()
        pd.DragMouse(upper.originX, upper.originY, lower.originX, lower.originY)
        If pd.PDElements.Count > prevCount Then
            Dim c As PDElement = pd.PDElements.Item(pd.PDElements.Count - 1)
            If c IsNot Nothing AndAlso c.IsCovariance() Then
                c.position1 = pos1
                c.position2 = pos2
                c.ParameterFontSize = paramFont
            End If
        End If
    End Sub

    Private Sub RepositionAllArrows()
        Dim el As PDElement
        For Each el In pd.PDElements
            If el.IsVariable() Then el.Width = el.Width
        Next
    End Sub

    Private Sub FitDiagramToWindow()
        Dim element As PDElement
        Dim left As Double = 999999, top As Double = 999999, right As Double = -999999, bottom As Double = -999999
        Dim padding As Double = 40.0
        For Each element In pd.PDElements
            If element.IsVariable() Then
                If element.originX - element.Width / 2 < left Then left = element.originX - element.Width / 2
                If element.originY - element.Height / 2 < top Then top = element.originY - element.Height / 2
                If element.originX + element.Width / 2 > right Then right = element.originX + element.Width / 2
                If element.originY + element.Height / 2 > bottom Then bottom = element.originY + element.Height / 2
            End If
        Next
        If left < 999999 AndAlso top < 999999 Then
            pd.DiagramZoom(left - padding, top - padding, right + padding, bottom + padding)
        End If
    End Sub

    Public Function Name() As String Implements IPlugin.Name
        Return "{spec.get('plugin_menu_name', 'Result')}"
    End Function

    Public Function Description() As String Implements IPlugin.Description
        Return "Generated SEM diagram (font {g['font_size']:.0f}, full exogenous covariances={str(spec['diagram'].get('enforce_full_exogenous_covariances', True)).lower()}) per diagram_standard.json."
    End Function
End Class
"""


def emit_headless_fit_vb(spec: dict[str, Any]) -> str:
    meas_lines = []
    for c in spec["constructs"]:
        for j, ind in enumerate(c["indicators"]):
            ref = "(1) " if j == c["ref_row"] else ""
            meas_lines.append(f'        Sem.AStructure("{ind["sav_var"]} = {ref}{c["id"]} + (1) {ind["err"]}")')

    by_target: dict[str, list[str]] = {}
    for sp in spec["structural_paths"]:
        by_target.setdefault(sp["to"], []).append(sp["from"])
    c_by_id = {c["id"]: c for c in spec["constructs"]}
    struct_lines = []
    for dst, srcs in by_target.items():
        dname = c_by_id[dst]["disturbance"]
        struct_lines.append(f'        Sem.AStructure("{dst} = {" + ".join(srcs)} + (1) {dname}")')

    cov_lines = [f'        Sem.AStructure("{cv["a"]} <--> {cv["b"]}")' for cv in spec.get("covariances", [])]

    return f"""Option Strict On
Option Explicit On

Imports System
Imports System.IO
Imports System.Reflection
Imports MiscAmosTypes
Imports AmosEngineLib

' GENERATED AUTOMATICALLY BY scripts/generate_from_spec.py.
Module MainModule
    Function Main(ByVal args() As String) As Integer
        If args Is Nothing OrElse args.Length < 1 Then
            Console.Error.WriteLine("Usage: HeadlessFit.exe <path-to-sav>")
            Return 1
        End If
        AddHandler AppDomain.CurrentDomain.AssemblyResolve, AddressOf ResolveAmos
        Return RunFit(args(0))
    End Function

    Private Function ResolveAmos(ByVal sender As Object, ByVal e As ResolveEventArgs) As Assembly
        Dim amosDir As String = Environment.GetEnvironmentVariable("AMOS_DIR")
        If String.IsNullOrEmpty(amosDir) Then Return Nothing
        Dim name As String = New AssemblyName(e.Name).Name & ".dll"
        Dim candidate As String = Path.Combine(amosDir, name)
        If File.Exists(candidate) Then Return Assembly.LoadFrom(candidate)
        Return Nothing
    End Function

    Private Function RunFit(ByVal savPath As String) As Integer
        Dim Sem As New AmosEngine()
        Try
            Sem.Standardized()
            Sem.Smc()
            Sem.TextOutput()
            Sem.GenerateDefaultCovariances(False)
            Sem.BeginGroupEx(cDatabaseFormat.mmSPSS, savPath, "")

{chr(10).join(meas_lines)}

{chr(10).join(struct_lines)}

{chr(10).join(cov_lines)}

            Dim status As Integer = Sem.FitModel()
            Dim isAdmissible As Boolean = Sem.Admissible()
            Console.WriteLine("FitStatus=" & CStr(status) & "; Admissible=" & CStr(isAdmissible) & "; CMIN=" & CStr(Sem.Cmin) & "; DF=" & CStr(Sem.Df) & "; RMSEA=" & CStr(Sem.Rmsea))
            Console.WriteLine("TEXT_OUT=" & Sem.TextOutputFileName())
            Sem.Dispose()
            If status <> 0 Then Return 2
            If Not isAdmissible Then Return 4
            Return 0
        Catch ex As Exception
            Console.Error.WriteLine("RUNTIME_ERROR=" & ex.Message)
            Try
                Sem.Dispose()
            Catch
            End Try
            Return 3
        End Try
    End Function
End Module
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Universal AMOS SEM standard generator and collision gate.")
    parser.add_argument("--spec", default=str(DEFAULT_SPEC), help="Path to project spec JSON")
    parser.add_argument("--standard", default=str(DEFAULT_STANDARD), help="Path to diagram_standard.json")
    parser.add_argument("--font", type=float, default=None, help="Override font size")
    parser.add_argument("--sav", default="", help="Runtime SPSS .sav path (overrides spec.sav_file)")
    parser.add_argument("--dataset-report", default="", help="Output path for dataset integration JSON report")
    parser.add_argument("--skip-sav-check", action="store_true", help="Skip .sav column existence check")
    parser.add_argument("--validate-only", action="store_true", help="Validate spec, dataset, and geometry without emitting VB.NET")
    args = parser.parse_args()

    std_path = Path(args.standard)
    if not std_path.is_absolute():
        std_path = ROOT / std_path
    if not std_path.exists():
        sys.stderr.write(f"Standard rules file not found: {std_path}\n")
        return 1
    std = json.loads(std_path.read_text(encoding="utf-8"))

    spec_path = Path(args.spec)
    if not spec_path.is_absolute():
        spec_path = ROOT / spec_path
    if not spec_path.exists():
        sys.stderr.write(f"Spec file not found: {spec_path}\n")
        return 1

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if args.font is not None:
        spec["diagram"]["font_size"] = float(args.font)
        spec["diagram"]["param_font_size"] = float(args.font) * float(std["typography"]["param_font_ratio"])

    # Surface covariance policy warnings explicitly without silent assumption
    diag = spec.get("diagram", {})
    exog_count = sum(1 for c in spec.get("constructs", []) if c.get("role") == "exogenous")
    for w in spec.get("warnings", []):
        sys.stderr.write(f"WARNING: {w}\n")
    if (
        exog_count >= 2
        and not spec.get("covariances")
        and "covariance_policy" not in diag
        and "enforce_full_exogenous_covariances" not in diag
    ):
        sys.stderr.write(
            "WARNING: Covariance policy not explicitly specified in JSON spec "
            "(missing 'covariance_policy' / 'enforce_full_exogenous_covariances'). "
            "No exogenous covariance paths were added automatically.\n"
        )

    # Validate structural spec (dataset checked below via unified sav_utils)
    spec_errors = validate_spec(spec, std, check_sav=False)
    if spec_errors:
        for e in spec_errors:
            sys.stderr.write(f"STANDARD ERROR: {e}\n")
        return 1

    # Unified dataset resolution & preflight validation
    resolved_sav, sav_source = resolve_sav_path(args.sav, spec.get("sav_file"), ROOT, spec_dir=spec_path.parent)
    if args.skip_sav_check:
        dataset_report = validate_sav_dataset(None, spec, source_label="skipped")
    else:
        dataset_report = validate_sav_dataset(resolved_sav, spec, source_label=sav_source)

    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    ds_report_path = Path(args.dataset_report) if args.dataset_report else (PLAN_DIR / "dataset_report.json")
    if not ds_report_path.is_absolute():
        ds_report_path = ROOT / ds_report_path
    ds_report_path.parent.mkdir(parents=True, exist_ok=True)
    ds_report_path.write_text(json.dumps(dataset_report, indent=2), encoding="utf-8")

    for w in dataset_report.get("warnings", []):
        sys.stderr.write(f"DATASET WARNING: {w}\n")
    if dataset_report.get("errors"):
        for e in dataset_report["errors"]:
            sys.stderr.write(f"DATASET ERROR: {e}\n")
        return 1

    g = compute_geometry(spec, std)
    plan = build_plan(spec, g, std)
    has_cap = spec["diagram"].get("goodness_of_fit_caption", True)
    problems = run_checks(plan, g, has_cap)

    (PLAN_DIR / "layout.json").write_text(
        json.dumps(
            {
                "standard_version": std.get("standard_version", "1.0"),
                "geometry": g,
                "shapes": [asdict(s) for s in plan.shapes],
                "arrows": [asdict(a) for a in plan.arrows],
                "curves": [asdict(c) for c in plan.curves],
                "problems": problems,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (PLAN_DIR / "assessment.html").write_text(render_html(plan, problems, g, has_cap), encoding="utf-8")

    if problems:
        for p in problems:
            sys.stderr.write(f"COLLISION PROBLEM: {p}\n")
        return 1

    if args.validate_only:
        print(
            f"VALIDATE_OK spec={spec_path.name} font={g['font_size']:.0f} page={g['page_w_in']}x{g['page_h_in']}in "
            f"sav={dataset_report.get('staged_filename') or 'none'}"
        )
        return 0

    staged_sav_name = dataset_report.get("staged_filename", "")
    vb_rel = spec.get("plugin_source", "scripts/GeneratedSEM.vb")
    vb_out = ROOT / vb_rel if not Path(vb_rel).is_absolute() else Path(vb_rel)
    vb_out.parent.mkdir(parents=True, exist_ok=True)
    vb_out.write_text(emit_plugin_vb(spec, g, std, staged_sav_name=staged_sav_name), encoding="utf-8")
    (SCRIPTS_DIR / "HeadlessFit.vb").write_text(emit_headless_fit_vb(spec), encoding="utf-8")

    print(
        f"STANDARD_OK spec={spec_path.name} font={g['font_size']:.0f} page={g['page_w_in']}x{g['page_h_in']}in "
        f"shapes={len(plan.shapes)} arrows={len(plan.arrows)} curves={len(plan.curves)} vb={vb_out.name} "
        f"sav={staged_sav_name or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
