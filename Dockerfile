# Brush Gaussian Splat training — RunPod Serverless worker
# No COLMAP here: the bundle arrives with the Mac-side reconstruction.
FROM runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Vulkan runtime for Brush (wgpu) + xvfb for the headless app binary.
# libegl1 matters: without it Brush can silently fall back to CPU (llvmpipe).
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget \
        xz-utils \
        libgl1 \
        libegl1 \
        libglib2.0-0 \
        libvulkan1 \
        vulkan-tools \
        mesa-vulkan-drivers \
        xvfb && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV NVIDIA_DRIVER_CAPABILITIES=all

# Pinned Brush release (proven working in RunPod serverless setups)
RUN wget -q https://github.com/ArthurBrussee/brush/releases/download/v0.3.0/brush-app-x86_64-unknown-linux-gnu.tar.xz -O /tmp/brush.tar.xz && \
    tar -xf /tmp/brush.tar.xz -C / && \
    rm /tmp/brush.tar.xz

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY handler.py test_input.json ./

CMD ["python", "-u", "handler.py"]
