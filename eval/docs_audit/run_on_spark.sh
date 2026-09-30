#!/usr/bin/env bash
# Run the docs audit in the E2E image (Tesseract eng/chi_tra/chi_sim + LibreOffice, tests/e2e/Dockerfile):
# every code example of README.md and docs/ (docs_examples.py) and the strict Sphinx build.
#
#   eval/docs_audit/run_on_spark.sh OUT_DIR [docs_examples.py args...]
#
# Like scripts/run_e2e_docker.sh it builds d2m-e2e:<hash of the Dockerfile> when missing, copies the
# checkout into a throwaway container and installs it there, here with the extras the docs use
# (ocr, dev, docs, tokenizers, redis, typesafe). A Redis server is installed and started in the
# container so the Redis examples run for real. OUT_DIR receives examples.md, examples.json,
# probes.log (the code issues, probes.py), sphinx.log and the exit codes. The judge examples call
# TypeSafe only when TYPESAFE_API_KEY is set in this script's environment (source
# ~/.config/typesafe/env first; only the name is passed to docker); without it they show the
# documented "judge unavailable" path.
set -euo pipefail

out="$(mkdir -p "$1" && cd "$1" && pwd)"; shift
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
dockerfile="$repo/tests/e2e/Dockerfile"
if command -v sha256sum >/dev/null 2>&1; then
    digest="$(sha256sum "$dockerfile" | cut -c1-12)"
else
    digest="$(shasum -a 256 "$dockerfile" | cut -c1-12)"
fi
image="d2m-e2e:$digest"
docker image inspect "$image" >/dev/null 2>&1 || docker build -t "$image" - < "$dockerfile"

pass_env=()
[ -n "${TYPESAFE_API_KEY:-}" ] && pass_env+=(-e TYPESAFE_API_KEY)
sha="$(git -C "$repo" rev-parse --short HEAD 2>/dev/null || echo unknown)"

exec docker run --rm --init --name "d2m-docs-audit-$(date +%s)-$$" \
    -v "$repo:/src:ro" -v "$out:/out" \
    -v d2m-e2e-pip-cache:/pip-cache -e PIP_CACHE_DIR=/pip-cache \
    -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PIP_ROOT_USER_ACTION=ignore \
    -e OMP_THREAD_LIMIT=1 -e D2M_SHA="$sha" -e D2M_HOST="$(hostname)" \
    ${pass_env[@]+"${pass_env[@]}"} \
    "$image" \
    bash -uo pipefail -c '
        mkdir /work
        tar -C /src --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude="*.egg-info" \
            --exclude=docs/_build -cf - . | tar -C /work -xf -
        cd /work
        (apt-get update -qq && apt-get install -y -qq --no-install-recommends redis-server >/dev/null \
            && redis-server --daemonize yes >/dev/null) || echo "redis-server not available" >&2
        pip install -q -e ".[ocr,dev,docs,tokenizers,redis,typesafe]" || exit 90
        python eval/docs_audit/docs_examples.py --report /out/examples.md --json /out/examples.json "$@" \
            > /out/examples.log 2>&1
        echo $? > /out/examples.exit
        python eval/docs_audit/probes.py > /out/probes.log 2>&1
        echo $? > /out/probes.exit
        python -m sphinx -b html -W --keep-going docs /tmp/html > /out/sphinx.log 2>&1
        echo $? > /out/sphinx.exit
        cat /out/examples.exit /out/probes.exit /out/sphinx.exit
    ' bash "$@"
