# Docs audit

Scripts that check README.md and the Sphinx docs against what the code does. They were written
for the docs rewrite of PR "docs: audit README and docs against current behaviour" and can be
re-run after any change to the docs or the code.

| Script | What it does |
|---|---|
| `docs_examples.py` + `examples_manifest.py` | Finds every code block of README.md, docs/*.rst and docs/api/*.rst and checks it the way its manifest entry says: runs it as written in a workspace whose files carry the names the docs use (copies of `sample_documents/` and generated fixtures), compares shown output with real output, or records why it is not run. OpenAI examples run against the local stand-in for the OpenAI API from the E2E suite (`tests/e2e/fake_openai.py`): the provider code runs for real, only the model is replaced. |
| `probes.py` | Reproduces the code issues the audit found (the PR's "Code issues found"); a probe whose title says "fixed" prints the behaviour since the fix. |
| `run_on_spark.sh OUT_DIR` | Runs both, and the strict Sphinx build, in the E2E image (Tesseract + LibreOffice) with the extras the docs use and a local Redis server. Source `~/.config/typesafe/env` first to let the judge examples call TypeSafe (a few questions). |
| `merged_cells.py` | Re-measures the merged-cell comparison of the README (doc2mark, and markitdown / Docling when installed). |
| `run_judge_eval.sh` | Re-runs `eval/judge_eval.py` (the judge numbers of the README and docs/judge.rst) on a spark with a fresh verdict cache. |

Results of the audit are in `results/`: `examples.md` and `probes.txt` (the run on the PR's head
commit; `probes.txt` shows the issues as they were), `merged_cells.*` and `judge_eval.*` (the re-measured numbers), `claims.md` (every claim of
the old README and docs with its status and evidence) and `facts-*.md` (the code fact sheets the
rewrite was based on; the throwaway probe scripts they cite are not committed, `probes.py`
reproduces the code issues).

```bash
# on a spark, in a worktree of the commit to check
eval/docs_audit/run_on_spark.sh ~/code/.executors/docs-audit-out
cat ~/code/.executors/docs-audit-out/examples.md
```
