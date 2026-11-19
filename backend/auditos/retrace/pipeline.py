# DAVYLAB CAD Core — independent implementation (clean-room).
# Written for DAVYLAB P0 verification round 1 (2026-11-19, WorkBuddy).
# No code or implementation detail copied from pengxiaoan/autocad-dwg-redraw-skill
# (upstream has license=null; DAVYLAB path is independent implementation).
"""Raster line-drawing -> calibrated DXF with native DIMENSION entities.

Pipeline: preflight -> binarize -> structural line extraction -> calibration
(1-N anchors, residuals) -> layered DXF ($INSUNITS explicit) -> audit -> preview.

Evidence contract: every run writes a JSON record with input/code hashes,
command, software versions, metrics and residuals.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

CODE_VERSION = "davylab-cad-round1-r1"
INSUNITS = {"mm": 4, "cm": 5, "m": 6, "unitless": 0}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest().upper()


def self_sha256() -> str:
    return sha256(Path(__file__))


def preflight(img) -> dict:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sharp = cv2.Laplacian(gray, cv2.CV_64F).var()
    dark_ratio = float((gray < 128).mean())
    return {
        "width_px": int(img.shape[1]),
        "height_px": int(img.shape[0]),
        "sharpness_laplacian_var": round(sharp, 2),
        "dark_pixel_ratio": round(dark_ratio, 4),
        "mode": "line-drawing" if dark_ratio < 0.35 else "photo/render-like",
    }


def binarize(img) -> np.ndarray:
    """Inverted binary ink mask (255 = ink)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.medianBlur(gray, 3)
    mask = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                 cv2.THRESH_BINARY_INV, 21, 8)
    return mask


def extract_structural_lines(mask: np.ndarray, min_len_frac: float = 0.05):
    """Morphological extraction of long horizontal/vertical lines.

    Returns (h_lines, v_lines, frame_bbox, n_contours) where h/v_lines are
    [x0, x1, y] / [x, y0, y1] pixel segments of strong straight lines.
    """
    h_px, w_px = mask.shape
    min_len = int(min(h_px, w_px) * min_len_frac)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(min_len // 2, 9), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(min_len // 2, 9)))
    hmask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, hk)
    vmask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, vk)

    h_lines, v_lines = [], []
    for m, kind in ((hmask, "h"), (vmask, "v")):
        n, labels, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            if area < 0.5 * min_len:
                continue
            if kind == "h" and bw >= min_len:
                h_lines.append([int(x), int(x + bw), int(y + bh / 2)])
            elif kind == "v" and bh >= min_len:
                v_lines.append([int(x + bw / 2), int(y), int(y + bh)])
    frame = _frame_bbox(mask, h_lines, v_lines, w_px, h_px)
    if frame is not None:
        frame = _refine_frame_edges(h_lines, v_lines, frame)
    n_contours = _count_contours(mask, hmask, vmask)
    return h_lines, v_lines, frame, n_contours


def _frame_bbox(mask, h_lines, v_lines, w_px, h_px):
    """Frame = bbox of the largest connected ink component (the cabinet body),
    falling back to extents of long lines if component detection is poor."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n > 1:
        areas = stats[1:, cv2.CC_STAT_AREA]
        biggest = 1 + int(np.argmax(areas))
        x, y, bw, bh, area = stats[biggest]
        if bw > 0.4 * w_px and bh > 0.4 * h_px:
            return [int(x), int(y), int(x + bw), int(y + bh)]
    xs, ys = [], []
    for x0, x1, y in h_lines:
        if x1 - x0 > 0.5 * w_px:
            xs += [x0, x1]; ys.append(y)
    for x, y0, y1 in v_lines:
        if y1 - y0 > 0.5 * h_px:
            ys += [y0, y1]; xs.append(x)
    if not xs or not ys:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _count_contours(mask, hmask, vmask):
    rest = cv2.bitwise_and(mask, cv2.bitwise_not(cv2.bitwise_or(hmask, vmask)))
    cnts, _ = cv2.findContours(rest, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    return sum(1 for c in cnts if cv2.arcLength(c, False) > 60)


def cluster_positions(values, tol=6):
    """Cluster 1-D coordinates; returns sorted list of (mean, count)."""
    vals = sorted(values)
    clusters = []
    for v in vals:
        if clusters and v - clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [(round(float(np.mean(c)), 1), len(c)) for c in clusters]


def _refine_frame_edges(h_lines, v_lines, frame):
    """Replace component-bbox frame with straight-edge frame: candidate edges
    are lines (span >= 50% of extent) inside a narrow window at each side;
    take the OUTERMOST candidate per side (min for top/left, max for
    bottom/right), removing rounded-corner / line-thickness inflation."""
    x0, y0, x1, y1 = frame
    w, h = x1 - x0, y1 - y0
    top = [y for a, b, y in h_lines if b - a >= 0.5 * w and y0 <= y <= y0 + 0.05 * h]
    bot = [y for a, b, y in h_lines if b - a >= 0.5 * w and y1 - 0.05 * h <= y <= y1]
    left = [x for x, a, b in v_lines if b - a >= 0.5 * h and x0 <= x <= x0 + 0.05 * w]
    right = [x for x, a, b in v_lines if b - a >= 0.5 * h and x1 - 0.05 * w <= x <= x1]
    return [round(min(left)) if left else x0,
            round(min(top)) if top else y0,
            round(max(right)) if right else x1,
            round(max(bot)) if bot else y1]


def vertical_partitions(v_lines, frame, tol=8):
    """Inside-frame dominant vertical lines (partitions): must span >=95% of
    frame height (excludes dimension extension lines, interior detail lines
    and near-edge lines)."""
    x0, y0, x1, y1 = frame
    fh = y1 - y0
    xs = [x for x, a, b in v_lines
          if x0 + 0.01 * (x1 - x0) < x < x1 - 0.01 * (x1 - x0)
          and (b - a) >= 0.95 * fh]
    return cluster_positions(xs, tol)


def horizontal_partitions(h_lines, frame, tol=8):
    x0, y0, x1, y1 = frame
    fw = x1 - x0
    ys = [y for a, b, y in h_lines
          if y0 + 0.01 * (y1 - y0) < y < y1 - 0.01 * (y1 - y0)
          and (b - a) >= 0.95 * fw]
    return cluster_positions(ys, tol)


def anchor_residuals(v_parts, h_parts, frame, scale, expected_chain=None,
                     expected_w=None, expected_h=None):
    """Compute mm-space residuals for declared anchors. scale = mm per px."""
    x0, y0, x1, y1 = frame
    w_mm, h_mm = (x1 - x0) * scale, (y1 - y0) * scale
    out = {"frame_mm": [round(w_mm, 3), round(h_mm, 3)], "anchors": []}
    if expected_w:
        out["anchors"].append({"name": "overall_width", "expected_mm": expected_w,
                               "actual_mm": round(w_mm, 3),
                               "residual_mm": round(w_mm - expected_w, 3),
                               "residual_pct": round((w_mm - expected_w) / expected_w * 100, 4)})
    if expected_h:
        out["anchors"].append({"name": "overall_height", "expected_mm": expected_h,
                               "actual_mm": round(h_mm, 3),
                               "residual_mm": round(h_mm - expected_h, 3),
                               "residual_pct": round((h_mm - expected_h) / expected_h * 100, 4)})
    if expected_chain and len(v_parts) >= len(expected_chain) - 1:
        boundaries = [x0] + [p for p, _ in v_parts] + [x1]
        chain = [round((boundaries[i + 1] - boundaries[i]) * scale, 3)
                 for i in range(len(boundaries) - 1)]
        out["internal_chain_mm"] = chain
        for i, exp in enumerate(expected_chain):
            if i < len(chain):
                out["anchors"].append({"name": f"chain_seg{i + 1}", "expected_mm": exp,
                                       "actual_mm": chain[i],
                                       "residual_mm": round(chain[i] - exp, 3),
                                       "residual_pct": round((chain[i] - exp) / exp * 100, 4)})
    return out


def build_dxf(out_path: Path, frame, scale, unit, v_parts, h_parts,
              product_boxes=None, labels=None, dims=None):
    """Construct DXF with explicit layers, $INSUNITS and native DIMENSIONs."""
    import ezdxf
    from ezdxf.enums import TextEntityAlignment

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = INSUNITS[unit]
    doc.header["$MEASUREMENT"] = 1 if unit == "mm" else 0
    for name, color, lw in (("STRUCTURE", 7, 70), ("PARTITION", 3, 50),
                            ("PRODUCT", 8, 25), ("TEXT", 2, 25), ("DIM", 1, 25)):
        doc.layers.add(name, color=color, lineweight=lw)

    msp = doc.modelspace()
    # pixel -> mm CAD coordinates (y flipped)
    def X(px): return (px - frame[0]) * scale
    def Y(py): return (frame[3] - py) * scale
    H = (frame[3] - frame[1]) * scale
    W = (frame[2] - frame[0]) * scale

    # outer frame (STRUCTURE)
    msp.add_lwpolyline([(0, 0), (W, 0), (W, H), (0, H)], close=True,
                       dxfattribs={"layer": "STRUCTURE"})
    # inner vertical partitions
    for px, _cnt in v_parts[1:-1]:
        msp.add_line((X(px), 0), (X(px), H), dxfattribs={"layer": "PARTITION"})
    for py, _cnt in h_parts[1:-1]:
        msp.add_line((0, Y(py)), (W, Y(py)), dxfattribs={"layer": "PARTITION"})
    # product boxes as outlines
    for bx0, by0, bx1, by1 in (product_boxes or []):
        msp.add_lwpolyline([(X(bx0), Y(by1)), (X(bx1), Y(by1)),
                            (X(bx1), Y(by0)), (X(bx0), Y(by0))], close=True,
                           dxfattribs={"layer": "PRODUCT"})
    # human-entered text labels
    for (tx, ty, txt, hpx) in (labels or []):
        msp.add_text(txt, dxfattribs={"layer": "TEXT", "height": hpx * scale}
                     ).set_placement((X(tx), Y(ty)), align=TextEntityAlignment.MIDDLE_CENTER)
    # native dimensions
    style = doc.dimstyles.get("EZDXF")
    style.dxf.dimlfac = 1.0  # ezdxf EZDXF preset uses 100; our coords are already in declared units
    style.dxf.dimtxt = 70
    style.dxf.dimasz = 50
    style.dxf.dimexe = 40
    style.dxf.dimexo = 30
    style.dxf.dimdec = 0
    style.dxf.dimtxsty = "OpenSansCondensed-Light"

    for d in (dims or []):
        kind = d.pop("kind")
        if kind == "h":
            base_y = d.pop("base_y")
            dim = msp.add_linear_dim(base=(0, base_y), p1=(d["x1"], d["ly"]),
                                     p2=(d["x2"], d["ly"]), dimstyle="EZDXF",
                                     dxfattribs={"layer": "DIM"})
        else:
            base_x = d.pop("base_x")
            dim = msp.add_linear_dim(base=(base_x, 0), p1=(d["lx"], d["y1"]),
                                     p2=(d["lx"], d["y2"]), angle=90,
                                     dimstyle="EZDXF", dxfattribs={"layer": "DIM"})
        dim.render()
    doc.saveas(out_path)
    return doc


def audit_dxf(path: Path) -> dict:
    import ezdxf
    doc = ezdxf.readfile(path)
    auditor = doc.audit()
    counts = {}
    for e in doc.modelspace():
        counts[e.dxftype()] = counts.get(e.dxftype(), 0) + 1
    layers = sorted({e.dxf.layer for e in doc.modelspace()})
    return {"errors": len(auditor.errors), "fixes": len(auditor.fixes),
            "entity_counts": counts, "layers_used": layers,
            "insunits": doc.header.get("$INSUNITS")}


def render_preview(doc, png_path: Path, max_px: int = 1600):
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure()
    ax = fig.add_axes([0, 0, 1, 1])
    ctx = RenderContext(doc)
    Frontend(ctx, MatplotlibBackend(ax)).draw_layout(doc.modelspace(), finalize=True)
    fig.savefig(png_path, dpi=200)
    plt.close(fig)


def record_run(input_path, out_paths, extra, record_path, command):
    rec = {
        "code_version": CODE_VERSION,
        "code_sha256": self_sha256(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "opencv": cv2.__version__,
        "command": command,
        "input": {"path": str(input_path), "sha256": sha256(Path(input_path))},
        "outputs": {Path(k).name: {"sha256": sha256(Path(v)), "bytes": Path(v).stat().st_size}
                    for k, v in out_paths.items()},
        **extra,
    }
    Path(record_path).write_text(json.dumps(rec, indent=2, ensure_ascii=False), "utf-8")
    return rec


def main():
    ap = argparse.ArgumentParser(description="DAVYLAB CAD retrace (independent)")
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True, help="output DXF path")
    ap.add_argument("--unit", default="unitless", choices=list(INSUNITS))
    ap.add_argument("--scale", type=float, default=1.0, help="mm per pixel (0=auto from anchors)")
    ap.add_argument("--anchor-width-px", type=float, default=None,
                    help="measured frame width px for auto scale")
    ap.add_argument("--width-mm", type=float, default=None)
    ap.add_argument("--height-mm", type=float, default=None)
    ap.add_argument("--chain-mm", type=float, nargs="*", default=None,
                    help="expected internal chain segments (mm) e.g. 2000 100 500 600")
    ap.add_argument("--dims", action="store_true", help="emit native DIMENSION entities")
    ap.add_argument("--labels-json", default=None, help="human-entered text labels file")
    ap.add_argument("--evidence", default=None, help="evidence JSON output")
    ap.add_argument("--png", default=None, help="preview png output")
    ap.add_argument("--declare-mm-assumed", action="store_true",
                    help="record that mm unit is assumed, not printed on source")
    args = ap.parse_args()

    img = cv2.imread(str(args.image))
    if img is None:
        sys.exit(f"cannot read image {args.image}")
    pf = preflight(img)
    mask = binarize(img)
    h_lines, v_lines, frame, n_contours = extract_structural_lines(mask)
    if frame is None:
        sys.exit("no structural frame detected")

    # calibration
    if args.width_mm and not args.anchor_width_px:
        scale = args.width_mm / (frame[2] - frame[0])  # auto: calibrate on detected frame width
    elif args.width_mm and args.anchor_width_px:
        scale = args.width_mm / args.anchor_width_px
    else:
        scale = args.scale or 1.0
    v_parts = vertical_partitions(v_lines, frame)
    h_parts = horizontal_partitions(h_lines, frame)
    resid = anchor_residuals(v_parts, h_parts, frame, scale,
                             expected_chain=args.chain_mm,
                             expected_w=args.width_mm, expected_h=args.height_mm)

    labels_data = json.loads(Path(args.labels_json).read_text("utf-8")) if args.labels_json else {}
    labels = labels_data.get("labels", []) if isinstance(labels_data, dict) else labels_data

    dims = []
    if args.dims:
        x0, y0, x1, y1 = frame
        H = (y1 - y0) * scale
        W = (x1 - x0) * scale
        off = 0.12 * H
        dims.append({"kind": "h", "x1": 0, "x2": W, "ly": 0, "base_y": -off})
        bnd = [0] + [(p - x0) * scale for p, _ in v_parts] + [W]
        for i in range(len(bnd) - 1):
            dims.append({"kind": "h", "x1": bnd[i], "x2": bnd[i + 1], "ly": 0,
                         "base_y": -off * 1.9})
        dims.append({"kind": "v", "y1": 0, "y2": H, "lx": W, "base_x": W + off})

    doc = build_dxf(Path(args.out), frame, scale, args.unit, v_parts, h_parts,
                    product_boxes=None, labels=labels, dims=dims)
    aud = audit_dxf(Path(args.out))
    png = None
    if args.png:
        render_preview(doc, Path(args.png))
        png = args.png

    out_paths = {args.out: args.out}
    if png:
        out_paths[png] = png
    extra = {"preflight": pf,
             "extraction": {"h_lines": len(h_lines), "v_lines": len(v_lines),
                            "contours_non_line": n_contours,
                            "v_partitions_px": v_parts, "h_partitions_px": h_parts,
                            "frame_px": frame},
             "calibration": {"scale_mm_per_px": round(scale, 6), "unit": args.unit,
                             "unit_source": "assumed" if args.declare_mm_assumed
                             else ("declared" if args.unit != "unitless" else "unitless-uncalibrated")},
             "residuals": resid,
             "audit": aud}
    rec = record_run(args.image, out_paths, extra, args.evidence or str(Path(args.out).with_suffix(".run.json")), " ".join(sys.argv))
    print(json.dumps({k: rec[k] for k in ("code_version", "calibration", "residuals", "audit")}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
