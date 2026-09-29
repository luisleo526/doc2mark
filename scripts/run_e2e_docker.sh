#!/usr/bin/env bash
# Run the doc2mark E2E suite in Docker (Tesseract + LibreOffice + CJK fonts).
#
#   scripts/run_e2e_docker.sh [pytest args...]
#
# Builds d2m-e2e:<hash of tests/e2e/Dockerfile> if it is missing, copies this
# checkout into a throwaway container (the checkout itself stays untouched),
# installs it with `pip install -e ".[ocr,dev]"` (pip cache in the named volume
# d2m-e2e-pip-cache) and runs `pytest -m e2e <args>` with D2M_E2E_STRICT=1.
# The exit code is pytest's.
#
# D2M_E2E_PASS_ENV: space-separated names of host env vars to forward into the
# container. Only the names go on the docker command line; the values are read
# from this script's environment, so a secret can come from a file:
#   set -a; . ~/.secrets/openai.env; set +a
#   D2M_E2E_PASS_ENV="OPENAI_API_KEY" scripts/run_e2e_docker.sh
#
# A later -m in <args> replaces `-m e2e`, e.g. the unit suite in the same image:
#   scripts/run_e2e_docker.sh -m "not e2e" -q
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dockerfile="$repo/tests/e2e/Dockerfile"

if command -v sha256sum >/dev/null 2>&1; then
    digest="$(sha256sum "$dockerfile" | cut -c1-12)"
else
    digest="$(shasum -a 256 "$dockerfile" | cut -c1-12)"
fi
image="d2m-e2e:$digest"

if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "run_e2e_docker.sh: building $image" >&2
    docker build -t "$image" - < "$dockerfile"
fi

pass_env=()
for var in ${D2M_E2E_PASS_ENV:-}; do
    if [ -z "${!var+x}" ]; then
        echo "run_e2e_docker.sh: $var is listed in D2M_E2E_PASS_ENV but is not set" >&2
        exit 2
    fi
    pass_env+=(-e "$var")
done

name="e2e-$(basename "$repo" | tr -c 'A-Za-z0-9_.\n-' '-')-$(date +%s)-$$"

exec docker run --rm --init --name "$name" \
    -v "$repo:/src:ro" \
    -v d2m-e2e-pip-cache:/pip-cache \
    -e PIP_CACHE_DIR=/pip-cache \
    -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
    -e PIP_ROOT_USER_ACTION=ignore \
    -e D2M_E2E_STRICT=1 \
    ${pass_env[@]+"${pass_env[@]}"} \
    "$image" \
    bash -euo pipefail -c '
        mkdir /work
        tar -C /src --exclude=.git --exclude=.venv --exclude=.omc --exclude=__pycache__ \
            --exclude=.pytest_cache --exclude="*.egg-info" -cf - . | tar -C /work -xf -
        cd /work
        pip install -q -e ".[ocr,dev]"
        exec pytest -m e2e "$@"
    ' bash "$@"
