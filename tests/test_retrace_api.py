"""End-to-end tests for the CAD retrace API with a synthetic known-geometry fixture.

The fixture is a programmatic line drawing: outer frame 1000x600 px mapped to
1000x600 mm (scale 1 mm/px) plus one full-height partition at 500 mm. All
residuals should be ~0; 4 native DIMENSION entities are expected.
"""
import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUDITOS_DATA_DIR", tempfile.mkdtemp(prefix="auditos-test-retrace-"))

from auditos.main import app  # noqa: E402  (after env setup)

client = TestClient(app)


@pytest.fixture(scope="module")
def fixture_png() -> bytes:
    img = np.full((800, 1200, 3), 255, dtype=np.uint8)
    # outer frame (100,100)-(1100,700), 4 px thick
    cv2.rectangle(img, (100, 100), (1100, 700), (0, 0, 0), 4)
    # full-height partition at x=600 (500 mm from left)
    cv2.line(img, (600, 100), (600, 700), (0, 0, 0), 4)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_capabilities():
    r = client.get("/api/retrace/capabilities")
    assert r.status_code == 200
    caps = r.json()
    assert caps["max_image_bytes"] == 20 * 1024 * 1024
    assert "no OCR" in caps["boundaries"][0]


def test_full_job_lifecycle(fixture_png):
    r = client.post(
        "/api/retrace/jobs",
        files={"file": ("fixture.png", fixture_png, "image/png")},
        data={
            "unit": "mm", "declare_mm_assumed": "true",
            "width_mm": "1000", "height_mm": "600",
            "chain_mm": "500,500", "emit_dims": "true", "want_dwg": "false",
        },
    )
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "done", job.get("error")
    ev = job["evidence"]

    # geometry: scale 1 mm/px, residuals must be zero
    anchors = {a["name"]: a for a in ev["residuals"]["anchors"]}
    assert anchors["overall_width"]["residual_mm"] == 0.0
    assert anchors["overall_height"]["residual_mm"] == pytest.approx(0.0, abs=0.01)
    assert anchors["chain_seg1"]["residual_mm"] == pytest.approx(0.0, abs=0.01)
    assert anchors["chain_seg2"]["residual_mm"] == pytest.approx(0.0, abs=0.01)

    # native dimensions + audit
    assert ev["audit"]["entity_counts"]["DIMENSION"] == 4
    assert ev["audit"]["errors"] == 0
    assert ev["calibration"]["unit_source"] == "assumed"
    assert ev["calibration"]["unit"] == "mm"

    # artifacts downloadable
    dxf = client.get(f"/api/retrace/jobs/{job['id']}/artifacts/dxf")
    assert dxf.status_code == 200
    import ezdxf, io
    doc = ezdxf.read(io.StringIO(dxf.content.decode("utf-8").replace("\r\n", "\n")))
    dims = list(doc.modelspace().query("DIMENSION"))
    assert len(dims) == 4
    assert {round(d.get_measurement()) for d in dims} == {1000, 600, 500}

    png = client.get(f"/api/retrace/jobs/{job['id']}/artifacts/png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    evid = client.get(f"/api/retrace/jobs/{job['id']}/artifacts/evidence")
    assert evid.status_code == 200
    # dwg not requested -> 404
    assert client.get(f"/api/retrace/jobs/{job['id']}/artifacts/dwg").status_code == 404


def test_rejects_non_image(fixture_png):
    r = client.post(
        "/api/retrace/jobs",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"unit": "unitless"},
    )
    assert r.status_code == 400


def test_job_listing_and_missing(tmp_path, fixture_png):
    listed = client.get("/api/retrace/jobs")
    assert listed.status_code == 200 and isinstance(listed.json(), list)
    assert client.get("/api/retrace/jobs/does-not-exist").status_code == 404
    assert client.get("/api/retrace/jobs/does-not-exist/artifacts/dxf").status_code == 404
