#!/usr/bin/env python3
"""RunPod serverless handler: Brush Gaussian Splat training from a COLMAP bundle.

Service side of the backend's GaussianCloudProvider. No COLMAP runs here —
the bundle already contains our Mac-side reconstruction (images/ +
pose/sparse/0/ with cameras.bin/images.bin/points3D.bin). Brush discovers
COLMAP data anywhere inside the archive, so the zip is passed to it directly.

Contract (matches app/pipeline/reconstruction/gaussian_cloud.py):
  input:
    {
      "job_id": str,
      "bundle_url": str,        # zip: images/ + pose/sparse/0/
      "preset": "quality" | "balanced" | "fast",
      "iterations": int | null,
      "data_factor": int | null  # downscale factor -> max_resolution
    }
  output:
    {
      "success": bool,
      "ply_url": str      # when BUCKET_ENDPOINT_URL is configured (preferred)
      "ply_base64": str,  # fallback for small outputs
      "metrics": {...},
      "error": str | null
    }
"""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import time
from pathlib import Path

import requests
import runpod

BRUSH_EXE = os.environ.get("BRUSH_EXE", "/brush-app-x86_64-unknown-linux-gnu/brush_app")

PRESETS = {
    "quality": {"total_steps": 30000, "max_resolution": 1920},
    "balanced": {"total_steps": 15000, "max_resolution": 1280},
    "fast": {"total_steps": 7000, "max_resolution": 960},
}

# RunPod inline response safety margin
MAX_INLINE_BYTES = 18 * 1024 * 1024


def _download(url: str, dest: Path) -> None:
    res = requests.get(url, timeout=900)
    res.raise_for_status()
    dest.write_bytes(res.content)


def _train(bundle_zip: Path, out_dir: Path, total_steps: int, max_resolution: int) -> Path:
    """Run Brush headless on the bundle zip; Brush finds COLMAP data inside it."""
    cmd = [
        "xvfb-run",
        "-a",
        BRUSH_EXE,
        str(bundle_zip),
        "--export-path",
        str(out_dir),
        "--export-name",
        "output.ply",
        "--total-steps",
        str(total_steps),
        "--max-resolution",
        str(max_resolution),
    ]
    print(f"[brush] {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Brush failed (code {proc.returncode})\n"
            f"STDOUT:\n{proc.stdout[-4000:]}\nSTDERR:\n{proc.stderr[-4000:]}"
        )
    ply = out_dir / "output.ply"
    if not ply.exists():
        raise RuntimeError(f"Brush finished but {ply} is missing.\nSTDOUT:\n{proc.stdout[-4000:]}")
    return ply


def _emit_ply(ply: Path, output: dict, job_id: str) -> None:
    """Attach the PLY as a bucket URL (preferred) or inline base64."""
    if os.environ.get("BUCKET_ENDPOINT_URL"):
        from runpod.serverless.utils import rp_upload

        # BUCKET_NAME is required: without it the SDK defaults the bucket to
        # time.strftime("%m-%y"), which does not exist. prefix keeps jobs from
        # overwriting each other's output.ply.
        output["ply_url"] = rp_upload.upload_file_to_bucket(
            file_name=ply.name,
            file_location=str(ply),
            bucket_name=os.environ["BUCKET_NAME"],
            prefix=job_id,
        )
        return
    data = ply.read_bytes()
    if len(data) > MAX_INLINE_BYTES:
        raise RuntimeError(
            f"PLY is {len(data) // (1024 * 1024)}MB — too large for inline return. "
            "Configure BUCKET_ENDPOINT_URL / BUCKET_ACCESS_KEY_ID / "
            "BUCKET_SECRET_ACCESS_KEY on the endpoint."
        )
    output["ply_base64"] = base64.b64encode(data).decode()


def handler(job: dict) -> dict:
    inp = job.get("input") or {}
    bundle_url = inp.get("bundle_url")
    if not bundle_url:
        return {"success": False, "error": "Missing required field: bundle_url", "metrics": {}}
    job_id = str(inp.get("job_id") or job.get("id") or "job")

    preset = PRESETS.get(str(inp.get("preset") or "quality"), PRESETS["quality"])
    total_steps = int(inp.get("iterations") or preset["total_steps"])
    data_factor = inp.get("data_factor")
    max_resolution = (
        max(480, preset["max_resolution"] // int(data_factor))
        if data_factor
        else preset["max_resolution"]
    )

    t0 = time.time()
    try:
        with tempfile.TemporaryDirectory(prefix="brush_job_") as tmp:
            work = Path(tmp)
            bundle_zip = work / "bundle.zip"

            runpod.serverless.progress_update(job, "Downloading COLMAP bundle")
            _download(bundle_url, bundle_zip)

            out_dir = work / "out"
            out_dir.mkdir()
            runpod.serverless.progress_update(job, f"Training ({total_steps} steps)")
            ply = _train(bundle_zip, out_dir, total_steps, max_resolution)

            runpod.serverless.progress_update(job, "Uploading result")
            output: dict = {
                "success": True,
                "error": None,
                "metrics": {
                    "trainer": "brush",
                    "total_steps": total_steps,
                    "max_resolution": max_resolution,
                    "ply_bytes": ply.stat().st_size,
                    "train_time_sec": round(time.time() - t0, 1),
                },
            }
            _emit_ply(ply, output, job_id)
            return output
    except Exception as e:
        return {
            "success": False,
            "error": f"{type(e).__name__}: {e}",
            "metrics": {"train_time_sec": round(time.time() - t0, 1)},
        }


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
