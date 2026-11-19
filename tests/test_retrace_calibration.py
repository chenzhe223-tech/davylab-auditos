"""Retrace calibration unit tests — pure functions, no image fixtures."""
import pytest

from auditos.retrace.pipeline import (
    INSUNITS,
    anchor_residuals,
    cluster_positions,
)


def test_cluster_positions_merges_within_tolerance():
    assert cluster_positions([10, 12, 30, 31], tol=4) == [(11.0, 2), (30.5, 2)]
    assert cluster_positions([], 4) == []


def test_cluster_positions_keeps_separated_lines():
    # 734 vs 741 must NOT merge at tol=4 (glass detail vs partition)
    parts = cluster_positions([708, 734, 741, 865], tol=4)
    assert len(parts) == 4


def test_anchor_residuals_chain():
    frame = [0, 0, 832, 310]
    v_parts = [(520.0, 1), (546.0, 1), (677.0, 1)]  # px for 2000/2100/2600 @3.846
    out = anchor_residuals(v_parts, [], frame, scale=3200 / 832,
                           expected_chain=[2000, 100, 500, 600],
                           expected_w=3200, expected_h=1180)
    names = [a["name"] for a in out["anchors"]]
    assert names == ["overall_width", "overall_height",
                     "chain_seg1", "chain_seg2", "chain_seg3", "chain_seg4"]
    # width is calibration anchor: exact by construction
    assert out["anchors"][0]["residual_mm"] == 0.0
    # chain sum equals width by construction
    chain = out["internal_chain_mm"]
    assert abs(sum(chain) - 3200.0) < 1e-6


def test_insunits_mapping():
    assert INSUNITS["mm"] == 4 and INSUNITS["unitless"] == 0
