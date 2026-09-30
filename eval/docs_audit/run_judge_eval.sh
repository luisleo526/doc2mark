#!/usr/bin/env bash
# Re-measure the published numbers of the optional TypeSafe/Jev judge (README.md "Optional:
# quality judge", docs/judge.rst "Cost and latency" and "How well it works") in Docker on a
# spark host, with FRESH verdict caches: every verdict is a real request, nothing is replayed.
#
# Usage, on a spark (linux/arm64, docker without sudo):
#
#   eval/docs_audit/run_judge_eval.sh [CHECKOUT] [OUT_DIR]
#
#   CHECKOUT  doc2mark checkout to measure (default: the one holding this script). It is
#             mounted read-only and copied into the container; nothing in it is written.
#   OUT_DIR   results directory (default: ./judge-eval-<UTC timestamp>), created if missing.
#
# Environment:
#   TYPESAFE_ENV_FILE    file that sets TYPESAFE_API_KEY (default ~/.config/typesafe/env). It is
#                        sourced inside this script and the key reaches the container by NAME only
#                        (docker -e TYPESAFE_API_KEY): never printed, logged or put on a command
#                        line. At the end every file under OUT_DIR is checked for it.
#   D2M_JUDGE_SETS_BASE  optional commit (e.g. cb0018e, the merge of PR #22) whose labelled-set
#                        builder is run too, as a same-environment control for step 4.
#
# Steps, in the d2m-e2e image of tests/e2e/Dockerfile (built if missing, as
# scripts/run_e2e_docker.sh does) after `pip install -e ".[ocr,dev,typesafe]"`. Each writes
# OUT_DIR/<step>.txt (stdout), <step>.stderr and <step>.exit:
#   judge_eval           python eval/judge_eval.py --calibrate --cache-dir /tmp/judge-cache --json ...
#                        the labelled sets of tests/data/judge (TRAIN, TEST, EXTERNAL), fresh cache
#   judge_eval_shipped   the same without --calibrate (shipped thresholds); replays the verdicts
#                        of judge_eval from its cache, sends nothing
#   documents            the whole-document table of docs/judge.rst, measured as for PR #22: a
#                        12-page text report (tests/e2e/pdfgen.text_pdf), the 8-slide deck and the
#                        3-page letter of tests/e2e/builders_judge.py, each converted with
#                        judge_eval.document_run (OCR on, an OCR stand-in that returns nothing) on a
#                        second fresh cache, again from that cache, then without a judge; added time
#                        = wall time with the judge minus wall time without
#   judge_sets_rebuild   python eval/judge_sets.py --out OUT_DIR/judge_sets_rebuild (no network), with
#                        the library versions the committed sets were built with (PyMuPDF 1.27.2,
#                        Pillow 12.2, fontTools 4.61); judge_sets_compare then compares the rebuild
#                        item by item with tests/data/judge: do the committed sets still hold what
#                        this checkout hands the hooks? The _installed variants do the same with the
#                        library versions the checkout installs today (plus fontTools).
#   judge_eval_rebuilt   judge_eval on a rebuild, only when it differs from the committed sets
#                        (same cache as judge_eval: only changed items cost a request)
# The private deck behind docs/judge.rst (--deck) is never used: its rows are not re-measured.
#
# Cost: about 350 requests (~190k input tokens), under $0.01 at $0.042 per million input tokens;
# judge_eval.txt ends with the judge's totals (requests, tokens, cost).
# Exit: 0 when every step ran, 90 when the run could not be set up, else 1.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
checkout="$(cd "${1:-$here/../..}" && pwd)"
out="${2:-$PWD/judge-eval-$(date -u +%Y%m%dT%H%M%SZ)}"
if [ ! -f "$checkout/eval/judge_eval.py" ] || [ ! -f "$checkout/tests/e2e/Dockerfile" ]; then
    echo "run_judge_eval.sh: $checkout is not a doc2mark checkout" >&2
    exit 90
fi
mkdir -p "$out/runner"
out="$(cd "$out" && pwd)"

env_file="${TYPESAFE_ENV_FILE:-$HOME/.config/typesafe/env}"
if [ ! -r "$env_file" ]; then
    echo "run_judge_eval.sh: cannot read $env_file" >&2
    exit 90
fi
set -a
# shellcheck disable=SC1090
. "$env_file"
set +a
if [ -z "${TYPESAFE_API_KEY:-}" ]; then
    echo "run_judge_eval.sh: $env_file does not set TYPESAFE_API_KEY" >&2
    exit 90
fi

if command -v sha256sum >/dev/null 2>&1; then
    digest="$(sha256sum "$checkout/tests/e2e/Dockerfile" | cut -c1-12)"
else
    digest="$(shasum -a 256 "$checkout/tests/e2e/Dockerfile" | cut -c1-12)"
fi
image="d2m-e2e:$digest"
if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "run_judge_eval.sh: building $image" >&2
    docker build -t "$image" - < "$checkout/tests/e2e/Dockerfile" || exit 90
fi

base_mount=()
base_dir=""
cleanup() { [ -z "$base_dir" ] || rm -rf "$base_dir"; }
trap cleanup EXIT
if [ -n "${D2M_JUDGE_SETS_BASE:-}" ]; then
    base_dir="$(mktemp -d)"
    git -C "$checkout" archive "$D2M_JUDGE_SETS_BASE" | tar -x -C "$base_dir" || exit 90
    base_mount=(-v "$base_dir:/base-src:ro")
fi

sha="$(git -C "$checkout" rev-parse HEAD 2>/dev/null || echo unknown)"
dirty="$(git -C "$checkout" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
{
    echo "host: $(hostname)"
    echo "date (UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "checkout: $checkout"
    echo "sha: $sha (uncommitted changes: $dirty files)"
    echo "judge_sets base: ${D2M_JUDGE_SETS_BASE:-none}${D2M_JUDGE_SETS_BASE:+ ($(git -C "$checkout" rev-parse "$D2M_JUDGE_SETS_BASE" 2>/dev/null))}"
    echo "image: $image ($(docker image inspect "$image" --format '{{.Id}} {{.Architecture}}'))"
} > "$out/run-info.txt"
cat "$out/run-info.txt"

# ------------------------------------------------------------------ inside the container
cat > "$out/runner/container.sh" <<'CONTAINER'
#!/usr/bin/env bash
# Runs inside the container (see run_judge_eval.sh): /src is the checkout (read-only), /out the results.
set -uo pipefail
trap 'chown -R "${HOST_UID:-0}:${HOST_GID:-0}" /out 2>/dev/null || true' EXIT

step() {  # step NAME SECONDS CMD...: stdout to /out/NAME.txt, stderr to /out/NAME.stderr, status to /out/NAME.exit
    local name="$1" limit="$2" started=$SECONDS status
    shift 2
    echo "== $name: $*"
    timeout "$limit" "$@" > "/out/$name.txt" 2> "/out/$name.stderr" < /dev/null
    status=$?
    echo "$status" > "/out/$name.exit"
    echo "== $name: exit $status after $((SECONDS - started)) s"
    return "$status"
}

versions() {
    python - <<'PY'
import importlib.metadata as md, platform, sys
names = ("doc2mark", "typesafe-sdk", "pymupdf", "pillow", "fonttools")
found = []
for name in names:
    try:
        found.append(f"{name} {md.version(name)}")
    except md.PackageNotFoundError:
        found.append(f"{name} (not installed)")
print(f"python {sys.version.split()[0]} ({platform.machine()}); " + ", ".join(found))
PY
}

mkdir /work
tar -C /src --exclude=.git --exclude=.venv --exclude=.omc --exclude=__pycache__ \
    --exclude=.pytest_cache --exclude="*.egg-info" -cf - . | tar -C /work -xf - || exit 90
cd /work || exit 90
pip install -q -e ".[ocr,dev,typesafe]" < /dev/null || exit 90
[ ! -e /tmp/judge-cache ] && [ ! -e /tmp/judge-cache-docs ] || exit 90   # fresh caches only
echo "eval environment: $(versions)" | tee /out/versions.txt

status=0
step judge_eval 1800 python eval/judge_eval.py --calibrate --cache-dir /tmp/judge-cache \
    --json /out/judge_eval.json || status=1
step judge_eval_shipped 600 python eval/judge_eval.py --cache-dir /tmp/judge-cache || status=1
step documents 900 python /out/runner/documents.py /tmp/judge-cache-docs /tmp/documents \
    /out/documents.json || status=1

# The labelled sets record what doc2mark handed the hooks when they were built. Rebuild them with
# this checkout, first with the libraries it installs today (plus fontTools, which only the builder
# needs), then with the versions the committed sets were built with, and compare; evaluate a
# rebuild that differs (same verdict cache: only changed items cost a request).
compare_and_eval() {  # compare_and_eval SUFFIX REBUILT [BASE_REBUILT]
    local suffix="$1" rebuilt="$2"
    shift 2
    [ "$(cat "/out/judge_sets_rebuild$suffix.exit" 2>/dev/null)" = 0 ] || return 0   # rebuild failed: nothing to compare
    step "judge_sets_compare$suffix" 300 python /out/runner/compare_sets.py /work/tests/data/judge "$rebuilt" "$@"
    case $? in
        0) ;;
        10) step "judge_eval_rebuilt$suffix" 1800 python eval/judge_eval.py --calibrate --data "$rebuilt" \
                --cache-dir /tmp/judge-cache --json "/out/judge_eval_rebuilt$suffix.json" || status=1 ;;
        *) status=1 ;;
    esac
}
pip install -q fonttools < /dev/null || echo "could not install fontTools"
echo "set builder environment, as installed: $(versions)" | tee -a /out/versions.txt
step judge_sets_rebuild_installed 2400 python eval/judge_sets.py --out /out/judge_sets_rebuild_installed || status=1
pip install -q "pymupdf==1.27.2" "pillow==12.2.*" "fonttools==4.61.*" < /dev/null \
    || echo "could not pin the set builder's library versions; rebuilding with the installed ones"
echo "set builder environment, pinned: $(versions)" | tee -a /out/versions.txt
step judge_sets_rebuild 2400 python eval/judge_sets.py --out /out/judge_sets_rebuild || status=1
base=()
if [ -d /base-src ]; then
    mkdir /base && tar -C /base-src -cf - . | tar -C /base -xf -
    step judge_sets_rebuild_base 2400 python /base/eval/judge_sets.py --out /out/judge_sets_rebuild_base || status=1
    base=(/out/judge_sets_rebuild_base)
fi
compare_and_eval "" /out/judge_sets_rebuild ${base[@]+"${base[@]}"}
compare_and_eval _installed /out/judge_sets_rebuild_installed
exit "$status"
CONTAINER

# ------------------------------------------------------------------ whole documents
cat > "$out/runner/documents.py" <<'DOCUMENTS'
"""Whole-document cost and latency of the judge, measured as for docs/judge.rst (PR #22).

    python documents.py CACHE_DIR WORK_DIR OUT_JSON

Each document is converted with ``judge_eval.document_run`` (OCR on, an OCR stand-in that
returns nothing, a new TypeSafeJudge on the fresh CACHE_DIR), again from that cache, and then
without a judge; the added time is the wall time with the judge minus the time without.
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path.cwd()
sys.path[:0] = [str(ROOT), str(ROOT / "eval")]
import doc2mark  # noqa: E402

assert ROOT in Path(doc2mark.__file__).resolve().parents, doc2mark.__file__
from judge_eval import document_run  # noqa: E402

from doc2mark import UnifiedDocumentLoader  # noqa: E402
from doc2mark.judge import TypeSafeJudge  # noqa: E402
from doc2mark.ocr.base import BaseOCR, OCRResult  # noqa: E402
from tests.e2e import builders_judge, pdfgen  # noqa: E402

cache, work, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
work.mkdir(parents=True, exist_ok=True)

# Observation only: count the questions each hook asks (_ask(count=True) is one hook question).
by_hook: Counter = Counter()
_ask = TypeSafeJudge._ask


def _counting_ask(self, question, state, count=False):
    if count:
        by_hook[question.hook] += 1
    return _ask(self, question, state, count)


TypeSafeJudge._ask = _counting_ask


class NoOCR(BaseOCR):
    def batch_process_images(self, images, **kwargs):
        return [OCRResult(text="") for _ in images]


docs = {
    "12-page text report": pdfgen.text_pdf(work / "report12.pdf", [
        f"Maintenance report page {p}\nStation {p} replaced all shaft seals on 3 June 2026.\n"
        f"Energy use fell to {400 + p} MWh, down 7 percent on last year.\n"
        "Two inspectors signed off the valve logs for every station.\n"
        "The next full survey is scheduled for the spring of 2027." for p in range(1, 13)]),
    "8-slide deck with a brand line": builders_judge.deck_pdf(work / "deck8.pdf"),
    "3-page letter with a letterhead": builders_judge.letter_pdf(work / "letter.pdf"),
}
results = {}
for name, path in docs.items():
    by_hook.clear()
    fresh = document_run(path, TypeSafeJudge(cache_dir=cache))
    hooks = dict(by_hook)
    again = document_run(path, TypeSafeJudge(cache_dir=cache))
    results[name] = {"fresh": fresh, "asked_by_hook": hooks, "again": again}
    print(f"{name}: {fresh['pages']} pages; asked {fresh.get('asked', 0)} {hooks}, fresh {fresh.get('fresh', 0)}, "
          f"failed {fresh.get('failed', 0)}, input tokens {fresh.get('input_tokens', 0)}, "
          f"cost ${fresh.get('cost_usd', 0)}, wall {fresh['seconds']} s; re-run: fresh {again.get('fresh', 0)}, "
          f"cached {again.get('cached', 0)}, wall {again['seconds']} s")
for name, path in docs.items():
    started = time.perf_counter()
    UnifiedDocumentLoader(ocr_provider=NoOCR()).load(path, ocr_images=True)
    seconds = time.perf_counter() - started
    results[name]["no_judge_seconds"] = round(seconds, 2)
    results[name]["added_seconds"] = round(results[name]["fresh"]["seconds"] - seconds, 2)
    print(f"{name}: no judge {seconds:.2f} s; added time with the judge {results[name]['added_seconds']:+.2f} s")
out.write_text(json.dumps(results, indent=1, default=str))
DOCUMENTS

# ------------------------------------------------------------------ committed vs rebuilt sets
cat > "$out/runner/compare_sets.py" <<'COMPARE'
"""Compare labelled-set directories item by item (ids and changed fields; no texts printed).

    python compare_sets.py COMMITTED REBUILT [BASE_REBUILT]

Exit 0 when REBUILT equals COMMITTED byte for byte, 10 when they differ, 1 on an error.
With BASE_REBUILT (the sets rebuilt from an older commit in the same environment), also
COMMITTED vs BASE_REBUILT (does the old builder reproduce the committed files here?) and
BASE_REBUILT vs REBUILT (what the code changes alone changed).
"""
import json
import sys
from collections import Counter
from pathlib import Path

NAMES = ("legibility", "boilerplate", "non_content", "non_content_ambiguous",
         "external/legibility", "external/boilerplate", "external/non_content")
SHOWN = ("split", "label", "asked", "route", "pipeline_asks", "judged", "pattern", "expected_copies", "kind")


def load(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def changes(old, new):
    """Changed fields as dotted names (one level into dicts), with old -> new for short scalars."""
    found = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        if isinstance(a, dict) and isinstance(b, dict):
            found += [f"{key}.{sub}" for sub in sorted(set(a) | set(b)) if a.get(sub) != b.get(sub)]
        elif key in SHOWN:
            found.append(f"{key} {a!r} -> {b!r}")
        elif isinstance(a, str) and isinstance(b, str):
            found.append(f"{key} ({len(a)} -> {len(b)} chars)")
        else:
            found.append(key)
    return found


def compare(title, left, right):
    print(f"== {title}\n   {left}\n   {right}")
    differ = False
    for name in NAMES:
        a, b = left / f"{name}.jsonl", right / f"{name}.jsonl"
        if not a.exists() or not b.exists():
            print(f"{name}: MISSING ({a if not a.exists() else b})")
            differ = True
            continue
        if a.read_bytes() == b.read_bytes():
            print(f"{name}: identical ({len(load(a))} items)")
            continue
        differ = True
        old, new = {i["id"]: i for i in load(a)}, {i["id"]: i for i in load(b)}
        gone, added = sorted(old.keys() - new.keys()), sorted(new.keys() - old.keys())
        changed = {key: changes(old[key], new[key]) for key in sorted(old.keys() & new.keys())}
        changed = {key: value for key, value in changed.items() if value}
        fields = Counter(field.split(" ")[0] for value in changed.values() for field in value)
        order = " (same items, other order)" if not gone and not added and not changed else ""
        print(f"{name}: DIFFERS{order}: {len(old)} vs {len(new)} items; only left {len(gone)}, only right "
              f"{len(added)}, changed {len(changed)}; fields changed: {dict(fields.most_common())}")
        for key in gone:
            item = old[key]
            print(f"    only left:  {key} (kind {item.get('kind')}, label {item.get('label')}, split {item.get('split')})")
        for key in added:
            item = new[key]
            print(f"    only right: {key} (kind {item.get('kind')}, label {item.get('label')}, split {item.get('split')})")
        for key, value in changed.items():
            print(f"    changed:    {key}: {'; '.join(value)}")
    print()
    return differ


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 1
    committed, rebuilt = Path(argv[0]), Path(argv[1])
    differ = compare("committed tests/data/judge vs rebuilt from this checkout", committed, rebuilt)
    if len(argv) == 3:
        base = Path(argv[2])
        compare("committed tests/data/judge vs rebuilt from the base commit (control)", committed, base)
        compare("rebuilt from the base commit vs rebuilt from this checkout (code changes only)", base, rebuilt)
    return 10 if differ else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
COMPARE

# ------------------------------------------------------------------ run
name="d2m-docs-audit-judge-$(date +%s)"
status=0
docker run --rm --init --name "$name" \
    -v "$checkout:/src:ro" \
    -v "$out:/out" \
    ${base_mount[@]+"${base_mount[@]}"} \
    -v d2m-e2e-pip-cache:/pip-cache \
    -e PIP_CACHE_DIR=/pip-cache \
    -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
    -e PIP_ROOT_USER_ACTION=ignore \
    -e TYPESAFE_API_KEY \
    -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
    "$image" bash /out/runner/container.sh < /dev/null || status=$?

# The key must appear in no result file (grep reads it from a pipe, not from its command line).
leaked="$(grep -rlF -f <(printf '%s\n' "$TYPESAFE_API_KEY") "$out" 2>/dev/null || true)"
if [ -n "$leaked" ]; then
    echo "run_judge_eval.sh: the TypeSafe key appears in: $leaked" >&2
    status=1
else
    echo "key check: the TypeSafe key appears in no file under $out"
fi
for file in "$out"/*.exit; do
    if [ -e "$file" ]; then
        echo "$(basename "$file" .exit): exit $(cat "$file")"
    fi
done
grep -h "^judge totals" "$out"/judge_eval*.txt 2>/dev/null || true
echo "results: $out (container exit $status)"
case $status in
    0) exit 0 ;;
    90) exit 90 ;;
    *) exit 1 ;;
esac
