# Multi-stage build for the HotS Helper web service.
#   Stage 1 builds the React SPA.
#   Stage 2 installs the Python package (with the [web] extra) and copies
#   the built SPA into the package's static dir so FastAPI serves it.
#
# Designed for Hugging Face Spaces (Docker SDK, port 7860) but works on
# any Docker host — see packaging/DEPLOY.md.

# --- Stage 1: build the SPA -------------------------------------------------
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY web/ ./
RUN npm run build   # outputs to /src/hots_helper/web/static via vite outDir

# --- Stage 2: Python runtime -----------------------------------------------
FROM python:3.11-slim
WORKDIR /app

# Install the curated headless runtime deps first (cached layer), then
# the package itself with --no-deps so the heavy desktop dependencies
# (PySide6, onnxruntime, pynput, mss, …) never enter the image. Editable
# install so the app runs from ./src and we can drop the built SPA into
# src/hots_helper/web/static afterwards.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir \
        "fastapi>=0.110" "uvicorn[standard]>=0.29" "pydantic>=2.6" \
        "mpyq>=0.2.5" "platformdirs>=4.2.0" \
 && pip install --no-cache-dir --no-deps -e .

# Bring in the built SPA. The web stage's vite outDir wrote it to
# /src/hots_helper/web/static (../src relative to the /web workdir).
COPY --from=web /src/hots_helper/web/static ./src/hots_helper/web/static

ENV HOTS_WEB_PORT=7860
EXPOSE 7860
CMD ["hots-web"]
