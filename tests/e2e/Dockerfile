# System toolchain for the doc2mark E2E suite: Tesseract (eng, chi_tra, chi_sim),
# LibreOffice and CJK fonts. Python dependencies are not baked in: the runner
# (scripts/run_e2e_docker.sh) installs them from the checkout at run time.
# The runner tags this image with a hash of this file, so any edit rebuilds it.
FROM python:3.12-slim-bookworm

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-chi-tra \
        tesseract-ocr-chi-sim \
        libreoffice-writer \
        libreoffice-impress \
        libreoffice-calc \
        fonts-noto-cjk \
        libmagic1 \
        git \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
