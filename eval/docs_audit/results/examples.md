# Docs examples run

- doc2mark: 0.6.1 (/work/doc2mark/__init__.py)
- git: unknown
- host: spark-1693
- date: 2026-09-30 14:36 UTC
- python: 3.12.14
- results: {"pass": 76, "skip": 12}

## Blocks

| block | lang | mode | status | detail / first output line |
|---|---|---|---|---|
| `README.md:10` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `README.md:50` | bash | skip | skip | not run: the runner installs doc2mark with these extras (pip install -e ".[ocr,dev,docs,tokenizers,redis,typesafe]") |
| `README.md:63` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `README.md:77` | python | python | pass | INVOICE 2041 |
| `README.md:88` | bash | bash | pass | Output saved to: report.md |
| `README.md:117` | html | check | pass | <!-- page 1 --> |
| `README.md:138` | python | python | pass | <!-- page 1 --> |
| `README.md:189` | python | python (fake OpenAI) | pass | ACME STORE |
| `README.md:249` | bash | bash | pass | calls TypeSafe when TYPESAFE_API_KEY is set in the run |
| `README.md:255` | python | python | pass | prints the judge record when TYPESAFE_API_KEY is set, None otherwise |
| `README.md:287` | bash | skip | skip | not run here: the full unit and E2E suites were run separately on the same commit (see the PR) |
| `docs/api/convenience.rst:37` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/api/loader.rst:9` | python | check | pass | UnifiedDocumentLoader( |
| `docs/api/ocr.rst:102` | python | python | pass | ['gemini', 'openai', 'tesseract', 'vertex_ai'] |
| `docs/api/schema.rst:85` | python | check | pass | OCRPage fields: ['interpretation', 'raw'] |
| `docs/api/schema.rst:278` | python | python | pass | ACME STORE |
| `docs/api/types.rst:90` | python | python | pass | archive.7z unsupported: Cannot detect format for extension: 7z |
| `docs/caching.rst:16` | python | python | pass | 1 1 |
| `docs/caching.rst:47` | python | python | pass | MemoryOCRCache RedisOCRCache None |
| `docs/caching.rst:92` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/chunking.rst:13` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/chunking.rst:62` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/chunking.rst:81` | python | python (fake OpenAI) | pass | {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120} |
| `docs/cli.rst:9` | bash | bash | pass | # Sample DOCX Document |
| `docs/cli.rst:125` | bash | bash | pass | Output saved to: scan.md |
| `docs/contextual_ocr.rst:47` | literal | check | pass | <class 'int'> 0 |
| `docs/contextual_ocr.rst:116` | python | check | pass | excerpt matches doc2mark/ocr/openai.py |
| `docs/contextual_ocr.rst:135` | python | check | pass | excerpt matches doc2mark/ocr/vertex_ai.py |
| `docs/contextual_ocr.rst:164` | python | python (fake OpenAI) | pass |  |
| `docs/development.rst:6` | bash | skip | skip | not run: clone and editable install are what the runner does with this checkout |
| `docs/development.rst:14` | bash | skip | skip | not run here: the full unit and E2E suites were run separately on the same commit (see the PR) |
| `docs/development.rst:37` | bash | skip | skip | not run here: the full unit and E2E suites were run separately on the same commit (see the PR) |
| `docs/development.rst:65` | bash | skip | skip | not run: needs docker on the host and a TypeSafe key; the variables are read by scripts/run_e2e_docker.sh and tests/conftest.py |
| `docs/development.rst:76` | bash | skip | skip | not run here: the full unit and E2E suites were run separately on the same commit (see the PR) |
| `docs/development.rst:100` | python | check | pass | run as a pytest test in tests/e2e |
| `docs/development.rst:114` | bash | skip | skip | not run here: run_on_spark.sh runs exactly this build after the examples (sphinx.log), and so does the Docs workflow |
| `docs/formats.rst:11` | python | python | pass | ['docx', 'xlsx', 'pptx', 'doc', 'xls', 'ppt', 'rtf', 'pps', 'pdf', 'txt', 'csv', 'tsv', 'json', 'jso |
| `docs/formats.rst:109` | python | python | pass | DocumentFormat.DOCX 1 |
| `docs/formats.rst:130` | python | python | pass | DocumentFormat.DOC doc docx |
| `docs/formats.rst:153` | python | python | pass | , 11 6 |
| `docs/images.rst:14` | python | python | pass | # Image: receipt.png |
| `docs/images.rst:22` | text | check | pass | # Image: receipt.png |
| `docs/index.rst:12` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/installation.rst:8` | bash | skip | skip | not run: the runner installs doc2mark with these extras (pip install -e ".[ocr,dev,docs,tokenizers,redis,typesafe]") |
| `docs/installation.rst:55` | bash | skip | skip | not run: the runner installs doc2mark with these extras (pip install -e ".[ocr,dev,docs,tokenizers,redis,typesafe]") |
| `docs/installation.rst:69` | bash | skip | skip | not run: tests/e2e/Dockerfile installs exactly these Debian packages, and every example runs in that image |
| `docs/installation.rst:85` | bash | skip | skip | not run: tests/e2e/Dockerfile installs exactly these Debian packages, and every example runs in that image |
| `docs/installation.rst:94` | bash | bash | pass | usage: doc2mark [-h] [-o OUTPUT] [--ocr {openai,vertex_ai,tesseract,none}] |
| `docs/judge.rst:66` | bash | bash | pass | calls TypeSafe when TYPESAFE_API_KEY is set in the run |
| `docs/judge.rst:72` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/judge.rst:89` | python | python | pass |  |
| `docs/loading.rst:14` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/loading.rst:49` | python | python | pass | 11 6 |
| `docs/loading.rst:80` | python | python | pass | Markdown saved to: out/report.md |
| `docs/loading.rst:98` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis.1/4 documents/ |
| `docs/loading.rst:121` | python | check | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/loading.rst:145` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/ocr.rst:48` | python | python | pass | gpt-5.4-mini gemini-3.1-flash-lite-preview |
| `docs/ocr.rst:85` | python | python (fake OpenAI) | pass | ACME STORE |
| `docs/ocr.rst:154` | python | python (fake OpenAI) | pass | None 2 |
| `docs/ocr.rst:180` | python | python | pass | ACME STORE |
| `docs/ocr.rst:234` | python | python (fake OpenAI) | pass | ACME STORE |
| `docs/ocr.rst:282` | python | python (fake OpenAI) | pass | gpt-5.4-mini 120 |
| `docs/ocr_policy.rst:52` | python | check | pass | the commented results are what the calls return |
| `docs/ocr_policy.rst:106` | literal | check | pass | 0.55 200 |
| `docs/ocr_policy.rst:149` | literal | check | pass | --- stderr --- |
| `docs/ocr_policy.rst:413` | python | python | pass |  |
| `docs/ocr_policy.rst:684` | python | python | pass | {'document_route': 'image', 'overrides': []} |
| `docs/output.rst:91` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/output.rst:154` | python | python | pass | {'document_route': 'image', 'overrides': []} |
| `docs/pdf.rst:141` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/quickstart.rst:9` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/quickstart.rst:25` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/quickstart.rst:42` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/quickstart.rst:59` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/quickstart.rst:80` | python | python | pass | INVOICE 2041 |
| `docs/quickstart.rst:92` | python | python (fake OpenAI) | pass |  |
| `docs/quickstart.rst:106` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/quickstart.rst:122` | bash | bash | pass | Output saved to: report.md |
| `docs/rag.rst:14` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/rag.rst:70` | python | python | pass | 1 INVOICE 2041 |
| `docs/tables.rst:26` | python | python | pass | <table> |
| `docs/tables.rst:43` | text | check | pass | <table> |
| `docs/tables.rst:87` | python | python | pass | <!-- page 1 --> |
| `docs/tables.rst:109` | python | python | pass | \\| Item \\| Note \\| |
| `docs/tables.rst:116` | text | check | pass | \\| Item \\| Note \\| |
| `docs/tables.rst:298` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
| `docs/troubleshooting.rst:13` | python | python | pass | Consider using the pymupdf_layout package for a greatly improved page layout analysis. |
