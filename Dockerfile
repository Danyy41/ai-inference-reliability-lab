# --- builder ---------------------------------------------------------------
# Installs the project and its dependencies (including the optional
# "huggingface" extra) into an isolated virtualenv. Nothing from this stage
# except that venv and the source tree is copied into the runtime stage, so
# build tools and pip's cache never end up in the final image.
#
# To add NVIDIA GPU support later: this stage stays the same except the
# torch wheel installed here becomes a CUDA-enabled build; only the runtime
# stage's base image needs to change (see below).
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir ".[huggingface]"

# --- runtime -----------------------------------------------------------------
# Minimal final image: just the built venv, the app source, and a non-root
# user. No compilers, no pip cache, no dev/test dependencies.
#
# GPU variant later: swap this FROM line for an NVIDIA CUDA base image (e.g.
# nvidia/cuda:12.x-runtime-ubuntu22.04 with Python installed), keep
# everything else in this stage unchanged, and run with `docker run --gpus
# all` on a host with the NVIDIA Container Toolkit installed.
FROM python:3.11-slim AS runtime

RUN groupadd --system appuser && useradd --system --gid appuser --create-home appuser

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY src ./src

# Model weights and the Hugging Face cache are deliberately NOT baked into
# this image - they're downloaded at runtime into the cache dir below, which
# should be mounted as a volume in production so repeated runs don't
# re-download. No .env files or secrets are copied in either (see
# .dockerignore).
ENV HF_HOME=/home/appuser/.cache/huggingface
RUN mkdir -p "$HF_HOME" && chown -R appuser:appuser /home/appuser /app

USER appuser

EXPOSE 8000

# All INFERENCE_LAB_* settings (see core/config.py) are still plain
# environment variables, passed at `docker run` time - this image bakes in
# no backend choice, model name, or device.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"]

CMD ["uvicorn", "inference_lab.main:app", "--host", "0.0.0.0", "--port", "8000"]
