# Brush RunPod worker

CUDA Gaussian Splat training as a RunPod Serverless endpoint.
Input: a zip bundle containing a COLMAP reconstruction (`images/` +
`sparse/0/` or `pose/sparse/0/` with `cameras.bin` / `images.bin` /
`points3D.bin` — Brush discovers the model anywhere in the archive).
Output: a trained splat-format `.ply`.

**This folder is fully self-contained.** Copying just these files into a
fresh repository is enough to build and deploy — there are no dependencies
on the rest of the project:

```
Dockerfile        # image definition (pinned Brush v0.3.0 release binary)
handler.py        # RunPod serverless handler (stdlib + runpod + requests only)
requirements.txt  # python deps installed into the image
test_input.json   # sample payload for local testing / RunPod hub tests
README.md
```

## API contract

```jsonc
// input
{
  "job_id": "...",
  "bundle_url": "https://…/colmap_bundle.zip",  // must be reachable from RunPod
  "preset": "quality" | "balanced" | "fast",    // default: quality
  "iterations": 30000,                          // optional override
  "data_factor": 1                              // optional; divides max resolution
}
// output
{
  "success": true,
  "ply_url": "https://…",     // when BUCKET_ENDPOINT_URL is configured (preferred)
  "ply_base64": "…",          // fallback, only for small outputs (<18MB)
  "metrics": { "trainer": "brush", "total_steps": 30000, "ply_bytes": 0, "train_time_sec": 0 },
  "error": null
}
```

## Deploy

### Option A — RunPod builds from GitHub (no Docker needed locally)

1. Put this folder in a GitHub repository (it can be the repo root).
2. RunPod console → **Serverless → New Endpoint → GitHub Repo**, pick the
   repo; set the Dockerfile path if the folder isn't the repo root.

### Option B — build & push yourself

```bash
docker build --platform linux/amd64 -t <registry-user>/brush-runpod:v1 .
docker push <registry-user>/brush-runpod:v1
```

### Endpoint settings (either option)

- GPU: RTX 4090 (24GB) or A5000
- Execution timeout: **3600s** (training runs 20–40 min)
- Env vars — required for real rooms (PLYs exceed the inline response limit):
  - `BUCKET_ENDPOINT_URL` — any S3-compatible endpoint (Cloudflare R2 / S3 / MinIO)
  - `BUCKET_ACCESS_KEY_ID`, `BUCKET_SECRET_ACCESS_KEY`
- Keep 1 active worker during heavy use to avoid multi-GB cold-start pulls.

## Smoke test (no other tooling required)

```bash
# Submit
curl -s -X POST "https://api.runpod.ai/v2/<ENDPOINT_ID>/run" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" -H "Content-Type: application/json" \
  -d '{"input": {"job_id": "smoke", "bundle_url": "https://…/bundle.zip", "preset": "fast"}}'
# -> {"id": "<REQUEST_ID>", ...}

# Poll
curl -s "https://api.runpod.ai/v2/<ENDPOINT_ID>/status/<REQUEST_ID>" \
  -H "Authorization: Bearer $RUNPOD_API_KEY"
```

Local handler test (needs a real `bundle_url` in `test_input.json` and a
Vulkan-capable machine): `python handler.py`.

## Using from the main project (optional helpers)

The parent repository wires this endpoint into its pipeline via
`GaussianCloudProvider` and ships two helper scripts —
`scripts/make-colmap-bundle.py` (build a bundle from a processed job) and
`scripts/submit-runpod-brush.py` (submit + download via the pipeline's own
client). Neither is needed to build or run this worker.

## Troubleshooting

- **Training absurdly slow** → Brush fell back to CPU. `vulkaninfo | grep deviceName`
  inside the container must show the NVIDIA GPU, not `llvmpipe`. The image
  installs `libegl1` + `mesa-vulkan-drivers` and sets
  `NVIDIA_DRIVER_CAPABILITIES=all` to prevent this.
- **"too large for inline return"** → configure the `BUCKET_*` env vars.
- **Polling timeouts** → raise the endpoint execution timeout (and the
  caller's poll timeout).
