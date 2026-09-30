# doc2mark OCR layer: fact sheet + verdict on every OCR doc claim

Tree: `/Users/haoliangwen/code/doc2mark-d2m-docs-audit` at `becb74b` (== origin/main). Read-only audit; no repo file edited.

**Method.** I read the full code of `doc2mark/ocr/{__init__,base,openai,vertex_ai,tesseract,usage,refusal,prompts,schema}.py`, the loader's `_create_ocr_provider` / `load()`, and the OCR call sites in the PDF, Office and image pipelines. I ran probes from `/tmp/d2m-docs-audit-probe` with the worktree on `PYTHONPATH`, and every probe printed `doc2mark.__file__` = the worktree path. The probes used heredoc scripts; the only files they wrote were temp files under /tmp, removed afterwards.

**Probe environment.** Python 3.11.14 (conda), langchain 1.4.0, langchain-core 1.6.3, langchain-openai, openai 2.30.0, langchain-google-genai 4.4.0, pydantic 2.13.5, PyMuPDF 1.26.4. **pytesseract and the tesseract binary are not installed.**

**No network was used:**
- LLM calls were replaced by in-process fake agents that subclass `VisionAgent` / `VertexAIVisionAgent`, or by fake `BaseOCR` providers.
- `ChatOpenAI` was built with a dummy key and sent no request.
- `ChatGoogleGenerativeAI` was never constructed.
- Tesseract post-processing ran against an in-memory fake `pytesseract` module.

"(read)" marks a fact that comes from reading the code only.

**Verdicts:**
- TRUE / FALSE / PARTLY.
- UNVERIFIABLE-WITHOUT-KEY: the claim depends on how a live model or provider behaves.

---

## A. Fact sheet

### A1. The `OCR` facade (`doc2mark/ocr/__init__.py`)

**Constructor: `OCR(provider="openai", *, api_key=None, **config_kwargs)` (82-97).**
- A string `task` is coerced to a `Task` by `_coerce_task` (41-53). An unknown value raises `ValueError` that lists the valid values.
- `detail` must be `"raw"` or `"full"` (56-62), otherwise `ValueError`.
- The rest of the kwargs go to `OCRConfig(**config_kwargs)`. A kwarg that is not an OCRConfig field raises **TypeError** (probe: `project=` and `location=` both raise TypeError).
- It then calls `OCRFactory.create(...)`. The provider name is case-insensitive (base.py:674). An unknown name raises `ValueError("Unknown OCR provider: …")` (base.py:676).

**What the facade can and cannot reach.**
- It sets **only OCRConfig fields**.
- It cannot reach any provider constructor parameter: `project`, `location`, `timeout`, `max_retries`, `prompt_template`, `default_prompt`, `max_workers`.
- It adds no `CachedOCR` and no `UsageAggregatingOCR`. So through the facade there is no cache, no `ocr_issues`, and no document-level token_usage.

**Methods.**
- `read(images, *, task=None, tasks=None, language=None, structured=None, detail=None)` (99-128) coerces the values, then calls `provider.batch_process_images(images, task=…, tasks=…, language=…, structured=…, detail=…)`. A `None` argument means "use the config value". `read([])` returns `[]`.
- `read_one(image, **kw)` is `read([image], **kw)[0]` (130-132).

**Per-call semantics by provider** (probe-confirmed):

| kwarg | OpenAI | Vertex / Gemini | Tesseract |
|---|---|---|---|
| `task` / `tasks` | TASK_PROMPTS per image; `tasks` wins (openai.py:817-825) | same (vertex_ai.py:512-526) | ignored |
| `tasks` length mismatch | `ValueError` (openai.py:818-821) | **`OCRError`** (vertex_ai.py:513-517) | no check |
| `language` | output-language block appended to prompt (openai.py:827-831) | same (vertex_ai.py:532-538) | **per-call value ignored**; `config.language` picks the `-l` traineddata (tesseract.py:393) |
| `structured=False` | free-form path, `document=None` | same | ignored (always an OCRPage) |
| `detail="raw"` | **prompt instruction only** (openai.py:66-69, 832-833). Interpretation is kept if the model fills it (probe) | prompt note, and `page.interpretation = None` (vertex_ai.py:746-747) | ignored (interpretation always None) |
| `instructions` (provider kwarg, not exposed by the facade) | ignored in structured mode; replaces the free-form prompt (prompts.py:565-566) | **replaces the whole structured prompt** (vertex_ai.py:528-530) | n/a |

**What `language` means for the LLM providers.** It is an *output-language* instruction: "You MUST respond ENTIRELY in {language} … extract it as-is but frame your response in {language}" (prompts.py:508-517).
- When no language is set, an auto-detect block is appended. It ends with `respond in English with "No text detected in image"` (prompts.py:494-505), which is a phrase the refusal patterns count as no content.
- The CLI passes `--ocr-lang` only to Tesseract (cli.py:444).

### A2. `OCRConfig` (base.py:240-303): fields, defaults, consumers

Consumers are listed for the facade path. The loader path is in A5.

| field | default | OpenAI | Vertex / Gemini | Tesseract |
|---|---|---|---|---|
| model | None | live (openai.py:440) | **ignored**. The constructor default is used (vertex_ai.py:302, 331). Probe: `OCR("vertex_ai", model="gemini-2.0-flash")` gives `gemini-3.1-flash-lite-preview` | n/a |
| task | `Task.AUTO` | live | live | ignored |
| language | None | live (prompt) | live (prompt) | live (`-l` code) |
| temperature | None | live (441). **But** LangChain drops temperature≠1 for gpt-5 non-chat models. Probe: `gpt-5.4-mini` gives `ChatOpenAI.temperature=None`; `gpt-4o-mini` gives 0.0 | **ignored** (default 0) | n/a |
| max_tokens | None | live (442; default 8192) | **ignored** (default 8192) | n/a |
| base_url | None | live, falls back to `OPENAI_BASE_URL` (445-446) | n/a | n/a |
| max_concurrency | None | live (535-537) | live (vertex_ai.py:403-405) | n/a (fixed 4 workers). Also sizes PDF batches (A4) |
| structured | True | live | live | ignored |
| detail | "full" | prompt only | prompt + strip | ignored |
| response_model | None (means OCRPage) | **broken for non-OCRPage models**: `OCRError "'My' object has no attribute 'interpretation'"` (openai.py:1034; probe) | works: `text=str(model)`, `document=None` (vertex_ai.py:759-762, 789; probe) | n/a |
| on_parse_error | "raw_text" | live (1024-1032; `"raise"` raises OCRError, probe) | live (737-742) | n/a |
| context_pages | 0 | read only by the PDF pipeline (pymupdf_advanced_pipeline.py:680). OpenAI attaches the PDF only for PDF-capable models | always attaches | ignores `context_pdfs` |
| enhance_image | True | inert, DeprecationWarning | inert, DW | **live**: grayscale + threshold 150 (tesseract.py:170-174, 357-365) |
| detect_tables | True | inert, DW | inert, DW | not consumed (only logged, tesseract.py:94) |
| detect_layout | True | inert, DW | inert, DW | **live**: `--psm 3` vs `--psm 6` (tesseract.py:490-495) |
| max_retries | 3 | inert, DW. What reaches ChatOpenAI is the provider constructor param (default 3). Probe: `OCR("openai", max_retries=9)` gives 3 | inert, DW | n/a |
| timeout | 30 | inert, DW. Constructor param (default 30) is what reaches ChatOpenAI (probe) | inert, DW | n/a |
| extra | None | inert, DW | inert, DW | unused |
| non_content_judge | None | live (base.py:369-370) | live | unused |

**Deprecation.**
- The deprecated fields are `_DEPRECATED_LLM_FIELDS = ("enhance_image","detect_tables","detect_layout","timeout","max_retries","extra")` (base.py:202-204).
- `deprecated_llm_overrides()` (295-303) returns the fields whose value is not the default. The defaults are True, True, True, 30, 3, None. `extra={}` counts as non-default (probe).
- The warning is emitted **once per provider construction**, as one warning that lists every touched field. It comes from `OpenAIOCR.__init__` (openai.py:421-429) and `VertexAIOCR.__init__` (vertex_ai.py:341-349).
- It is never emitted by `OCRConfig` itself, never for Tesseract, and never for the loader's own `timeout=` / `max_retries=` params (probe).
- `stacklevel=2` attributes the warning to doc2mark code (base.py:682 via the facade, loader.py via the loader). As a result **Python's default filters hide it**: `python3 -c "OCR('openai', timeout=60)"` prints nothing, while `-W default` shows it (probe).

**Stale code text.**
- The comment at base.py:275 says "Only Gemini/Vertex consumes [context_pages] today". OpenAI consumes it too (openai.py:161-172).
- The OCRConfig docstring (244-249) calls `model` a live LLM knob. That is true for OpenAI only.

### A3. `OCRResult` and the metadata keys

`OCRResult` is a dataclass (base.py:183-196) with fields `text: str` (required), `confidence=None`, `language=None`, `metadata=None`, `document=None`.

**`confidence`:**
- Structured LLM: `interpretation.self_confidence`, or None when there is no interpretation (openai.py:1054; vertex_ai.py:755-757).
- Free-form: hard-coded **1.0** (openai.py:1071; vertex_ai.py:679).
- Tesseract: None, unless `batch_process_images(..., with_confidence=True)` is called (tesseract.py:202-219), and `OCR.read` cannot pass that.

**`language`:**
- OpenAI structured: `raw.detected_language` only (1055).
- Vertex structured: the detected language, else the per-call value, else the config value (768-770).
- Free-form: the per-call value or the config value.
- Tesseract: `config.language` exactly as requested (e.g. `"chinese"`), also copied into `raw.detected_language` (tesseract.py:229, 237).

**Metadata keys** (probe-confirmed):

| path | keys |
|---|---|
| OpenAI structured | `model, token_usage, structured=True, image_size_bytes, batch_index` (openai.py:1036-1042), plus `refusal, non_content="provider_refusal"` (1043-1044), `failed, error` (1045-1046), `router_violations` (1050-1051) |
| OpenAI free-form | `model, temperature, max_tokens, using_langchain, prompt_template, using_custom_instructions, image_size_bytes, batch_index, content_type, model_kwargs, token_usage, structured=False` (1073-1085), plus flags, plus `failed, error` |
| Vertex structured | `model, provider="vertex_ai", project, location, temperature, max_tokens, structured, detail, using_custom_instructions, image_size_bytes, batch_index, parse_error, token_usage`, plus `refusal/non_content`, `router_violations`, `failed/error` (vertex_ai.py:771-788) |
| Vertex free-form | `model, provider, project, location, temperature, max_tokens, prompt_template, using_custom_instructions, image_size_bytes, batch_index, content_type, token_usage`, plus flags, plus `failed/error` (683-697) |
| shared post-processing | `non_content` ∈ {`pattern`,`judge`,`provider_refusal`}, `ocr_refusal=True`, `non_content_suspected`, `non_content_unjudged` (base.py:432-471, 473-543); `structured_fallback="free_form"` (532); `router_violations`, `router_fallback` ∈ {`verbatim`,`unresolved`}, `token_usage` summed over both calls, `batch_index` kept (597-605) |
| Tesseract | success: `engine, config, language_code, image_size_bytes, original_image_size, original_image_mode, enhanced` (238-246). Per-image failure: `error, image_index, failed=True, engine`, with `text=""` and `document=None` (295-303) |
| CachedOCR | `doc2mark_from_cache=True` on cache hits and dedup copies (cache.py:36, 77-85) |
| OpenAI `save_locally` | `local_file, file_url, saved_locally, image_size_bytes, saved_size_bytes` (openai.py:663-669) |

**Per-result `token_usage`.**
- It is LangChain's `usage_metadata` dict: `input_tokens`, `output_tokens`, `total_tokens`, and possibly nested `input_token_details` / `output_token_details` (the probe passed a nested dict through untouched). It is `{}` on a failed call.
- **Gap:** the tokens of the free-form *recovery* call are dropped. The result keeps only the structured call's usage (base.py:531-541), and the document sum excludes the recovery call too. Probe: the recovery call reported 1500 tokens and the document sum stayed at 15.

### A4. Providers

**OpenAI (`openai.py`).**
- **Defaults:** `gpt-5.4-mini`, temperature 0, max_tokens 8192 (440-442; VisionAgent 209-211).
- **Constructor params:**
  - `timeout=30`, `max_retries=3` are forwarded to ChatOpenAI (443-444, 274-277). Probe: loader `timeout=7, max_retries=2` gives `request_timeout=7.0, max_retries=2`.
  - `max_workers=5` is stored and **never used** (450).
  - `**kwargs` go into `model_kwargs` and are **never sent** to ChatOpenAI (447, 266-283). Probe: `top_p=None`, `model_kwargs={}`.
  - `default_prompt` is stored but **never used in any prompt** (463-468); only its hash enters the OCR cache key (cache.py:260).
- **Credentials:**
  - `api_key` or `OPENAI_API_KEY` is read **at construction** (413), so the env var must be set before `OCR()` is built.
  - `base_url` comes from the param, then the config, then `OPENAI_BASE_URL` (445-446).
- **Extra:** `doc2mark[ocr]` = `openai>=2.0.0, langchain>=1.2.0, langchain-openai>=1.1.0, pytesseract>=0.3.10` (pyproject.toml).
- **Laziness:** construction never fails (485-487; probe with no key).
- **First OCR call:**
  - No LangChain: `ImportError("… pip install doc2mark[ocr]")` (512-517).
  - No key: `RuntimeError("OpenAI OCR requires an API key…")` (519-523).
  - Both are raised outside the try block, so they are **not OCRError**.
  - Under the loader both are swallowed: placeholder text, `ocr_issues.errors`, and `failed` stays 0 (probe).
- **Structured output:** `with_structured_output(schema, method="json_schema", include_raw=True)` (289-291). The pydantic class goes to the OpenAI SDK's strict schema. Probe with `to_strict_json_schema(OCRPage)`: `required` == every property, `additionalProperties: false`.
- **Message:** `SystemMessage(prompt)` + `HumanMessage([ {"type":"image","base64","mime_type"}, (context text + file part) ])` (147-183).
- **PDF gate:** `_PDF_CAPABLE_PREFIXES = ("gpt-4o","gpt-4.1","gpt-5","o1")`, case-insensitive prefix match (78-84). Probe: `o3`, `o4-mini`, `gpt-4-turbo`, `llava` all give False.
- **Concurrency:** `batch_as_completed(config={"max_concurrency": n} if n else None, return_exceptions=True)` (330-333).
  - `n` comes from `resolve_max_concurrency` (base.py:36-54): config value, else `OCR_MAX_CONCURRENCY` (positive int; `0`, negative, `abc` and `""` all give None), else None.
  - None means LangChain's `ThreadPoolExecutor(max_workers=None)`, i.e. `min(32, cpu+4)` threads. That is **20** on the 16-core audit Mac.
- **Free-form text:** ```` ``` ```` is folded to `` ` `` (367, 1031).

**Vertex / Gemini (`vertex_ai.py`).**
- `"vertex_ai"` and `"gemini"` are both registered to `VertexAIOCR` (820-821).
- **Defaults:** model `gemini-3.1-flash-lite-preview`, location `"global"`, temperature 0, max_tokens 8192, timeout 30, max_retries 3 (296-310). `project` comes from the param or `GOOGLE_CLOUD_PROJECT` (329).
- **What it ignores:** `OCRConfig.model`, `temperature` and `max_tokens` (331-333), and `api_key`, which is never passed.
- **LLM construction:** `ChatGoogleGenerativeAI(model, temperature, max_output_tokens, vertexai=True, location, [project], [timeout], [max_retries])` (164-180).
- **Auth:** ADC. The installed langchain-google-genai also reads `GOOGLE_API_KEY` / `GEMINI_API_KEY` on its own (third-party, not verified live).
- **Extra:** `doc2mark[vertex_ai]` = `langchain-google-genai>=2.0.0, langchain>=1.2.0`.
- **Laziness:** construction never fails.
  - At the first call, a missing package raises `ImportError("… pip install doc2mark[vertex_ai]")` (390-394).
  - An LLM construction error becomes `RuntimeError("Failed to initialize Vertex AI VisionAgent…")` (411-413).
  - When a credential failure surfaces is UNVERIFIABLE-WITHOUT-KEY.
- **Structured output:** same method (186-190).
- **Message:** the image part is `{"type":"image_url", data-URI}` (103). The context part is `{"type":"media","mime_type":"application/pdf","data":<raw b64>}` with **no PDF gate** (105-114).
- **Blocked answers:** `finish_reason` ∈ SAFETY, RECITATION, BLOCKLIST, PROHIBITED_CONTENT, SPII, IMAGE_SAFETY, IMAGE_PROHIBITED_CONTENT, IMAGE_RECITATION, or a `prompt_feedback.block_reason` (64-88).
- **Other quirks:**
  - A provider-level unknown task string silently becomes AUTO (481-491). The facade validates first, so this only affects direct provider calls.
  - `requires_api_key` is False (811-814).
  - The `max_workers` arg of `batch_process_images` is unused (458, 465).

**Tesseract (`tesseract.py`).**
- **No key.** Construction checks nothing (67-94); pytesseract is imported lazily (96-111).
- **Engine check.** It runs once before any image (284, 440-475) and raises **`OCREngineError`** in these cases (probe covered the first, third and fourth):
  - pytesseract is missing (ImportError is wrapped at 450-454);
  - the binary cannot be run (`--list-langs` fails, 413-432);
  - the language value is invalid;
  - the traineddata is missing.
- **Language mapping.** `LANGUAGE_ALIASES` (26-38), case-insensitive:
  - english→eng, chinese→chi_sim+chi_tra, chinese_simplified→chi_sim, chinese_traditional→chi_tra;
  - spanish→spa, french→fra, german→deu, japanese→jpn, korean→kor, russian→rus, arabic→ara.
  - Native codes are accepted (regex at 40; `deu_latf` and `script/Latin` pass), as are `+` combinations, including mixed aliases and codes: `english+chinese` gives `eng+chi_sim+chi_tra`.
  - None or `""` gives `eng`. `中文`, `eng+` and `french german` raise ValueError, which becomes OCREngineError.
  - Codes are matched against the installed list case-insensitively (459-468).
- **Output.** `OCRPage(raw=RawExtraction(text, detected_language=config.language), interpretation=None)` (226-232), with `text = to_markdown()`, which is escaped (235).
- **Config string:** `--psm 3|6 --oem 3` plus the `tesseract_config` kwarg (477-509).
- **Concurrency:** `ThreadPoolExecutor(max_workers=4)`; batches of 2 images or fewer run sequentially (261, 308-315).
- **`OCR_MAX_IMAGE_DIM`** applies only when an image has to be converted, i.e. non-PIL formats such as EMF/WMF (159-161).

**Downscaling (`utils/image_utils.py`).**
- `convert_image_to_supported_format` reads `OCR_MAX_IMAGE_DIM` (281-294). Both LLM providers call it for every image (openai.py:948, vertex_ai.py:619).
- `downscale_image` (163-217) resizes only when the longest side is greater than the bound, keeping the aspect ratio. A resized image is re-encoded as **PNG**.
- A non-int value logs a warning; `0` and negative values are silently ignored.

**Batch sizes.**
- PDF: at most `max(32, 2×max_concurrency)` images and 128 MiB per provider call (pymupdf_advanced_pipeline.py:48-49, 2285-2292).
- Office: one call per document with all of its images (office_advanced_pipeline.py:790).
- Standalone image: one call with one image (formats/image.py:143).

### A5. How loader params reach each provider (`_create_ocr_provider`, loader.py:245-335)

| loader param (default) | `"openai"` (285-308) | `"vertex_ai"` (310-328) | `"gemini"`, `"tesseract"` (factory path, 330-335) |
|---|---|---|---|
| `ocr_config` | goes through `_resolve_ocr_config` (task/structured/detail overrides, 218-243) | same | same |
| `model` ("gpt-5.4-mini") | passed; **beats `OCRConfig.model`** (probe) | swapped to `gemini-3.1-flash-lite-preview` only when it equals "gpt-5.4-mini" (314) | **ignored** (probe: gemini + model gives the default) |
| `temperature` (0), `max_tokens` (8192) | passed; beat OCRConfig (probe) | passed | ignored |
| `base_url` (None) | passed; **beats `OCRConfig.base_url`** (probe: the config value was dropped) | n/a | ignored |
| `max_workers` (5) | passed, unused | not passed | ignored |
| `prompt_template` (DEFAULT), `default_prompt` | passed (free-form only / never used) | passed (same) | ignored |
| `timeout` (30), `max_retries` (3) | passed to ChatOpenAI | **not passed** (stays 30/3; probe) | ignored |
| `top_p`, `frequency_penalty`, `presence_penalty` | go into `model_kwargs`, never sent (probe) | not passed | ignored |
| `project`, `location` | n/a | passed | **ignored** (probe: `location` stays `global`) |

**Other loader behaviour:**
- `ocr_images=True` forces `extract_images=True` when OCR is configured (632-634).
- The provider is wrapped in `UsageAggregatingOCR` (207-215, 434), plus an optional `CachedOCR` (354-378).
- The judge's `non_content_judge` is attached to the config only when the config has none (337-344).
- **`load()` wraps every exception in `ProcessingError("Processing failed: …")` (746-748).** That includes the `OCREngineError` that `pop_document_issues` re-raises. Probe: the raised type is ProcessingError and `__cause__` is OCREngineError. (`OCRError` subclasses `ProcessingError`, core/base.py:196.)

### A6. Prompts

**Structured mode** always uses `TASK_PROMPTS` (base.py:152-180):
- `AUTO` is the self-routing router preamble (111-139).
- Routed requests without a context PDF also get `_ROUTER_NO_CONTEXT_CLAUSE` (145-150; openai.py:173-176; vertex_ai.py:115-118).
- `detail="raw"` adds `_RAW_DETAIL_INSTRUCTION` (OpenAI) or `_RAW_DETAIL_NOTE` (Vertex).
- `synthesis_markdown` (PDF image-strategy renders only) adds `_SYNTHESIS_MARKDOWN_INSTRUCTION` (220-237). Otherwise `page_markdown` is nulled (openai.py:1034-1035; vertex_ai.py:752-753).

**`PromptTemplate`** values (prompts.py:7-17): `default, table_focused, document_focused, multilingual, form_focused, receipt_focused, handwriting_focused, code_focused`. An invalid name raises ValueError at provider construction (openai.py:456-461; vertex_ai.py:353-360).
- **`prompt_template` affects only `_build_prompt`**, which serves two paths: the legacy `structured=False` path and the **free-form recovery** of empty or refused structured answers (openai.py:865-868, 933-936; vertex_ai.py:562-574, 612).
- Probe: with `table_focused` set, the structured prompt still starts with the router preamble, while the free-form prompt is the table prompt.
- **`default_prompt` never reaches a model** (probe: the "CUSTOM" string never appeared in either path).

### A7. Refusals and "no readable text" answers (`refusal.py`, `base.py`)

**Constants** (50-58): `MAX_PATTERN_CHARS=400`, `MAX_PATTERN_LINES=3`, `MAX_JUDGE_CHARS=600`, `JUDGE_THRESHOLD=0.5`, `SUSPECT_THRESHOLD=0.3`.

**Deterministic check** (328-342).
- It is a whole-answer `fullmatch` against patterns in EN, ZH, JA, KO, DE, ES and FR.
- Before matching it strips:
  - wrapper characters;
  - stock courtesy tails;
  - reason sentences made only of image-quality or sensitivity words;
  - "but I can summarize" offers.
- An answer that addresses a user ("please", "can you", "re-send", 請, ください, bitte, …) is left to the judge (299-304).
- All 24 examples from the docs behave as documented (probe).

**Judge** (367-390).
- It is called only when the patterns did not fire, the answer is 600 characters or fewer, and the judge is not `available=False`.
- Probability ≥0.5 means no content (reason `judge`).
- Probability from 0.3 to below 0.5 keeps the answer and flags `non_content_suspected`.
- None, a bool, a non-number or an exception keeps the answer and flags `non_content_unjudged`; such a result is not cached.
- An optional `judge.prefetch(answers)` is supported (400-415).
- The cache key uses the judge's qualified name plus its `version` (cache.py:152-165).

**Flow.**
1. **Native refusal.** OpenAI `message.refusal` / `OpenAIRefusalError`, or a Gemini block, empties the page and sets `non_content="provider_refusal"` (openai.py:1019-1022; vertex_ai.py:732-735).
2. **Text-only refusals are emptied.** A structured answer whose only content is refusal text is emptied (base.py:400-429). "Only content" means nothing else is present: no tables, fields, headings, metrics, dates, figures, sections, entities, relations, definitions, findings or actions, and no title, summary, message, visual note or page_markdown that is itself content (381-398).
3. **Free-form recovery** runs next (openai.py:839-873).
4. **`_apply_recovered`** (base.py:473-543): if the recovered answer is also a refusal, the result becomes `text=""`, `OCRPage()`, `ocr_refusal=True`.

**Free-form path:** a refusal becomes `text=""` with `ocr_refusal=True` and `non_content` ∈ {pattern, judge, provider_refusal} (base.py:450-471).

**Quirk:** a natively refused structured answer that is later recovered still carries `metadata["refusal"]` (probe).

### A8. Recovery and the router firewall

**Recovery.**
- Trigger: a structured result with empty `text` and nothing in `raw.text`, `tables` or `fields` (base.py:357-366). It is re-OCR'd in free-form mode with the legacy prompt and tagged `structured_fallback="free_form"`.
- The result's `raw.text` becomes **the free-form answer** (526-530). With the DEFAULT prompt that answer has "Extracted Text / Visual Analysis / Document Summary" sections (prompts.py:69-72), **so `raw.text` is then not verbatim-only**.
- If the recovery fails, the result keeps `failed=True` (516-519).

**Router firewall** (base.py:546-606).
- `withholding_violations` (schema.py:1931-1966) runs on every structured result.
- A violating result is redone with `Task.DOCUMENT`.
- A clean redo gets `router_fallback="verbatim"`. Otherwise the result is `"unresolved"` and gets the `[N illustrative rows not transcribed]` marker.
- Token usage is summed over both calls (probe).

### A9. Sanitisation and `to_markdown`

**`Table.html`** is validated as `normalize_table_html(sanitize_table_html(v))` (schema.py:1233-1239). Every sanitisation and escaping bullet in docs/ocr.rst was reproduced (probe):
- the tag and attribute allowlist;
- script, style and iframe removed together with their content;
- comments dropped;
- `<br>`, `<p>`, `<li>` and newlines turned into `<br>`;
- caption and tail text kept;
- pipe tables converted to HTML;
- `rowspan="0"` expanded, colspan capped;
- aligned padding (`Cost|80` stays under `2024`);
- double-counted blank cells dropped.

**`OCRPage.to_markdown()` order** (1686-1736):
1. The `page_markdown` fast path, when it covers at least 85% of the tokens (1697-1707, 1900). Uncovered tokens go into a hidden `<!-- raw-verbatim-tail … -->`.
2. Otherwise:
   - `# page_title`, only when `raw.text` does not already start with it;
   - the escaped `raw.text`;
   - each table (html, else the sanitised markdown, else a headers/rows pipe table), followed by its withheld marker;
   - a table of the non-illustrative metrics;
   - figures;
   - the marker for withheld fields, metrics and figures;
   - a section outline, only when some section has a summary or key points.

**`raw.fields`, headings, dates, summary, entities and relations are never rendered** (probe: the receipt `KeyValue(label="Store")` is absent from the Markdown).

**Free-form answers** go through `_sanitize_markdown` once (base.py:471).

### A10. Document-level `token_usage` / `ocr_issues` (`usage.py`, loader.py:703-718)

**`extra["token_usage"]`.**
- The shape is **exactly** `{"input_tokens","output_tokens","total_tokens"}` (usage.py:47-49, 78-92).
- Aliases are read: `prompt_*`, `completion_*` and camelCase (42-44).
- The total is derived when it is missing.
- Cache hits and dedup copies are skipped (293-295).
- It is stamped only when nonzero (loader.py:707-710).
- On a `cache_dir` replay it is renamed to `token_usage_cached` (1344-1358).

**`extra["ocr_issues"]`** (usage.py:72-75, 174-208).
- Shape: `{"refused","provider_refused","failed","withheld","suspected": int, "errors": [≤5 distinct str], "locations": [≤100 {"issue","image", + "page"|"slide"|"sheet"}]}`.
- The 5 counters and `errors` are always present. `locations` is **omitted when empty**. `engine_error` is never present, because it is raised instead.
- The dict exists only when refused, failed, withheld, suspected or errors is nonzero (203-205).
- **Counting rules** (247-295):
  - `ocr_refusal` → refused, and also provider_refused when `non_content=="provider_refusal"`;
  - `failed` → failed, and its `error` is added to `errors`;
  - `router_fallback=="unresolved"` → withheld;
  - `non_content_suspected` → suspected;
  - a provider exception → an `errors` entry of the form `"Type: msg"` only (224-234).
- **Location labels:**
  - PDF: `{"page": n+1}` (pymupdf_advanced_pipeline.py:935-937);
  - PPTX: `{"slide"}`; XLSX: `{"sheet"}` (office_advanced_pipeline.py:604-611, 792-794);
  - DOCX and standalone images get no label (probe).

**Caching.**
- The OCR cache never stores results that are `failed`, `router_fallback=="unresolved"` or `non_content_unjudged` (cache.py:197-215).
- A `provider_refusal` is cached for at most `REFUSAL_TTL_SECONDS = 600` (44, 218-222).
- `cache_dir` skips documents with failed images, `unread_pages` or `provider_refused` (loader.py:1290-1310).

### A11. Contextual OCR (`pipelines/pymupdf_advanced_pipeline.py`)

**Constants and locations:**
- `_CONTEXT_PDF_MAX_BYTES = 18*1024*1024` and `_WINDOW_CACHE_MAXLEN = 4` (55-56).
- The tier is resolved at line 680.
- Gating is at 2244 (renders, tier ≥1) and 2254 (pictures, tier ≥2).
- The window is built at 2151-2178. `context_pdfs` is injected only when some image has one (924-927).

**Probes:**
- Tier 0: no `context_pdfs` kwarg.
- Tier 1, on forced image-route pages: windows of 2, 3 and 2 pages.
- Tier 2: pictures get per-page windows of 2, 3 and 2.
- A render with ink that returns nothing becomes `[page 2: OCR returned no content]` with `unread_pages: [2]` (2438).

**18 MiB check.** The size check is on the **raw PDF bytes**, before base64 (2168-2169). The encoded payload can therefore reach about 24 MiB.

### A12. Incidental code issues found

These are code issues, not doc claims, but they shape the rewrite.
1. OpenAI plus a BYO `response_model` raises OCRError (openai.py:1034).
2. The free-form recovery call's tokens are never billed (base.py:531-541).
3. The Vertex facade ignores `model`, `temperature` and `max_tokens`, and cannot set `project` or `location`.
4. Loader path:
   - `"gemini"` ignores every loader model knob;
   - `"vertex_ai"` drops `timeout` and `max_retries`;
   - `"openai"` silently overrides `OCRConfig.model`, `temperature`, `max_tokens` and `base_url`.
5. `top_p`, `frequency_penalty`, `presence_penalty`, `max_workers` and `default_prompt` are inert.
6. **The OCR cache key omits `OCRConfig.task`** (and `on_parse_error`). `_slim_llm_config` (cache.py:168-182) gives equal keys for task `receipt` vs `table` (probe). A shared cache can therefore replay an answer across tasks.
7. The DeprecationWarning is invisible under Python's default warning filters.
8. When a standalone image's OCR raises (e.g. a missing key), the exception lands only in `ocr_issues.errors` with `failed=0`. `_ocr_incomplete` then lets `cache_dir` store the "OCR extraction failed" text, and it is replayed after the key is fixed (probe; loader.py:1300-1310; formats/image.py:164-168).
9. The comment at base.py:275 is stale.
10. In directory mode the CLI lists failed files but exits 0 (cli.py:696-717). Only single-file mode reaches `sys.exit(1)` (723-728).

---

## B. Claim verdicts

### B1. `docs/ocr.rst`

| L | claim | verdict | evidence |
|---|---|---|---|
| 4-7 | structured by default; text docs need no credentials; OCR only when needed | TRUE | base.py:264; openai.py:485-487; probe (no-key construction) |
| 24-36 | facade example, `from doc2mark import OCR` | TRUE | doc2mark/__init__.py:30; probes |
| 26 | creds from `OPENAI_API_KEY` | TRUE | openai.py:413 (read at construction) |
| 33 | `interpretation.summary  # (None for detail="raw")` | PARTLY | With Vertex the *interpretation* is None, so `.summary` raises AttributeError. With OpenAI it survives if the model fills it (probe) |
| 46 | constructor signature | TRUE | __init__.py:82-88 |
| 48-53 | provider names; kwargs go to OCRConfig; `task` coerced; `detail` validated; ValueError | TRUE | __init__.py:91-97. Also: names are case-insensitive; a non-field kwarg raises TypeError |
| 59-60 | one result per image, in input order | TRUE | openai.py:334; vertex_ai.py:258; tesseract.py:305-323 |
| 60-61 | `text` always populated | PARTLY | Always a str, but `""` for refusals, failures and blanks (base.py:378, 464-470) |
| 61-63 | `document` is OCRPage for LLMs "and for Tesseract, with an empty interpretation"; None for free-form | PARTLY | Tesseract's interpretation is **None**. `document` is also None for a failed Tesseract image (tesseract.py:295-303) and for a Vertex BYO model (vertex_ai.py:789) |
| 74 | raw "always present, always verbatim" | PARTLY | Always present: TRUE (schema.py:1669). Verbatim is prompt-enforced only; after recovery `raw.text` is the verbose free-form answer (base.py:526-530; prompts.py:69-72) |
| 86, 96-99 | 16 `document_type` values | TRUE | schema.py:1537-1541 (probe: 16) |
| 91-94 | interpretation None for `detail="raw"`, Tesseract, parse failure | PARTLY | Vertex strips it (746-747). OpenAI only prompts for it (openai.py:66-69; probe). Tesseract: TRUE. Parse failure: TRUE (openai.py:1032; vertex_ai.py:742) |
| 101-110 | list of the other raw/interpretation fields | PARTLY | Omits `key_findings` and `visual_notes` (schema.py:1546-1552) |
| 116-123 | task at construction / per call | TRUE | Ignored by Tesseract |
| 125-126 | `tasks` length must equal `len(images)`; `tasks` wins | PARTLY | Wins: TRUE. A mismatch raises ValueError (OpenAI) or OCRError (Vertex), and Tesseract does not check (probe) |
| 134-140 | task values | TRUE | base.py:70-76 |
| 142-144 | `language` is a config field with a per-call override | PARTLY | TRUE for LLMs; Tesseract ignores the per-call value (probe) |
| 150-155 | `detail="raw"` makes interpretation None, raw fully populated | PARTLY | Vertex: TRUE. OpenAI: the model is only asked (UNVERIFIABLE-WITHOUT-KEY whether it complies) |
| 157-162 | `structured=False` gives free-form Markdown and `document=None` | TRUE | Probe (text is sanitised) |
| 164-165 | detail / structured on the facade and per call | TRUE | __init__.py:91-94, 119-127 |
| 171-175 | html preferred; flat grid and `markdown` "remain populated" | PARTLY | headers/rows are requested by the prompt (base.py:88). `Table.markdown` is never requested (schema.py:1223); whether it is filled is UNVERIFIABLE-WITHOUT-KEY |
| 177-183 | tables code | TRUE | schema.py:1205-1231 |
| 192-194 | text rendered safe; structured fields keep what the model returned | PARTLY | `Table.html` itself is rewritten at validation (schema.py:1233-1239), as the next paragraph says |
| 196-214 | Table.html cleaning bullets | TRUE | Probe (all reproduced) |
| 216-220 | `<` escaping rules, control characters, entities | TRUE | Probe; schema.py:786-803, 83-88 |
| 222-244 | code spans, links and images, line-start escapes, model Markdown, flat table, Office `[Image: …]` | TRUE | Probe; office_advanced_pipeline.py:704, 811, 1697 |
| 250-256 | provider refusal signals | TRUE | openai.py:186-195; vertex_ai.py:64-88 (the listed Gemini reasons are a subset) |
| 257-274 | pattern check and all its examples | TRUE | Probe: 24/24 as documented |
| 275-281 | structured no-content goes to recovery; content besides text; `ocr_refusal`; page marker | TRUE | base.py:381-429, 473-543; pipeline 2438; probe |
| 287-292 | judge via `OCR(...)` and `OCRConfig` | TRUE | base.py:293 |
| 294-299 | ≤600 chars, patterns first, 0.5 threshold, None/exception keeps, cache key | TRUE | refusal.py:367-390; cache.py:152-165 (such results are also not cached) |
| 301-311 | `ocr_issues` keys and meaning | TRUE | usage.py:72-75, 174-208, 247-295; probe (locations ≤100, omitted when empty) |
| 311-314 | OCR-cache and `cache_dir` rules, 10-minute refusal TTL | PARTLY | TRUE for provider-flagged results (cache.py:44, 197-222; loader.py:1290-1310). A standalone image whose OCR *raised* is cached in `cache_dir` (probe; A12.8) |
| 314-315 | per-image failures flagged `failed` | TRUE | openai.py:345-366; vertex_ai.py:269-282; tesseract.py:295-303 |
| 315-318 | engine failure "raises `OCREngineError` from `load()`", CLI exits non-zero | PARTLY | `load()` raises **ProcessingError** with `__cause__` OCREngineError (loader.py:746-748; probe). CLI: exit 1 for a single file; directory mode exits 0 (cli.py:696-728) |
| 327-330 | OpenAI: json_schema, key + extra required, default model | TRUE | openai.py:289-291, 512-523, 440 |
| 339-341 | `model=` / `base_url=` examples | TRUE | Probe (facade) |
| 343-344 | `base_url` or `OPENAI_BASE_URL` | TRUE | openai.py:445-446 |
| 344-345 | precedence: constructor arg, then OCRConfig, then default | PARTLY | TRUE inside OpenAIOCR (432-446). The loader always passes model/temperature/max_tokens/base_url, so OCRConfig's values lose (probe) |
| 350-351 | `vertex_ai` and `gemini` are the same implementation | TRUE | vertex_ai.py:820-821 (but the loader treats "gemini" differently, A5) |
| 352-355 | ADC, not an API key; default model and location | TRUE | vertex_ai.py:164-178, 301-302 |
| 357-361 | extra + `GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT` | TRUE | pyproject; vertex_ai.py:329 |
| 365 | `OCR("gemini")` | TRUE | Probe |
| 366 | `OCR("vertex_ai", project="my-gcp-project", model=…)` | **FALSE** | TypeError: `project` is not an OCRConfig field (probe). `model` is ignored by Vertex anyway |
| 371-374 | Tesseract raw-only, interpretation None, language mapping, English default | TRUE | tesseract.py:26-38, 226-232, 381-411 (native codes and `+` also accepted) |
| 376-378 | `pip install "doc2mark[ocr]"` | PARTLY | Installs pytesseract only. The binary and traineddata are separate; missing ones raise OCREngineError |
| 382-385 | Tesseract example | TRUE | Probe (fake engine) |
| 391-401 | concurrency precedence; None = CPU-tied pool "typically ~12" | PARTLY | Precedence: TRUE (base.py:36-54). The pool is `min(32, cpu+4)`, 20 on the audit Mac; ~12 only on 8 cores |
| 407-426 | loader usage; disabling OCR; `--ocr none` | TRUE | loader.py:270-272; cli.py:207-210 |
| 432-436 | inert fields; "single DeprecationWarning at construction" for non-default values | PARTLY | Emitted by OpenAIOCR/VertexAIOCR (openai.py:421-429; vertex_ai.py:341-349) but **hidden by default filters** (probe) |
| 436-437 | `enhance_image` and `detect_layout` live for Tesseract | TRUE | tesseract.py:170-174, 490-495 |
| 437-440 | live knobs: model, task, language, max_concurrency, structured controls | PARTLY | `model` is ignored by Vertex/Gemini; `response_model` is broken on OpenAI |

### B2. `docs/api/ocr.rst`

| L | claim | verdict | evidence |
|---|---|---|---|
| 5-16 | facade overview; structured default; text always populated | TRUE | (text can be `""`) |
| 37-45, 52 | example and signature | TRUE | __init__.py:82-132 |
| 54-57 | provider values; default `openai` | TRUE | |
| 59-63 | api_key env fallback; Vertex ADC; Tesseract keyless | TRUE | Vertex also ignores an explicit `api_key` |
| 65-71 | any OCRConfig field; task/detail validated with ValueError | TRUE | Non-field kwarg raises TypeError |
| 73-77, 84-90 | note; read/read_one | TRUE | __init__.py:99-132 |
| 92-94 | `task` per call | TRUE | Ignored by Tesseract |
| 96-99 | `tasks` length mismatch raises ValueError | PARTLY | OCRError on Vertex/Gemini; no check on Tesseract (probe) |
| 101-102 | `language` appended to the prompt, overrides config | TRUE | For LLMs; Tesseract ignores the per-call value |
| 104-111 | structured / detail | TRUE | Vertex also drops the interpretation |
| 115-126, 160-164 | Task examples; `Task("table") is Task.TABLE` | TRUE | Probe |
| 136-156 | Task str-enum table; no multilingual task | TRUE | base.py:65-76 (`PromptTemplate.MULTILINGUAL` still exists for free-form) |
| 181-195 | "Live LLM knobs" table | PARTLY | Defaults TRUE. model/temperature/max_tokens are ignored by Vertex; base_url is OpenAI-only; temperature is dropped for gpt-5*; response_model is broken on OpenAI; `context_pages` and `non_content_judge` are missing from the table |
| 197-202 | Tesseract-only fields; `detect_tables` unused | TRUE | tesseract.py:94, 170, 490 |
| 204-211 | deprecated fields; single warning; `deprecated_llm_overrides` | PARTLY | Hidden by default filters (probe); the rest is TRUE |
| 213-221 | `max_concurrency` semantics; ~12 threads | PARTLY | See B1 391-401 |
| 223-227 | `OCR_MAX_IMAGE_DIM` resizes every image before it is sent; invalid values leave images untouched | PARTLY | TRUE for both LLM providers. Only images larger than the bound change, and they are re-encoded as PNG, which can grow a JPEG. Tesseract downscales only converted formats. `0` and negative values are silently ignored |
| 229-236 | example | TRUE | |
| 246-249 | Tesseract document "with an empty interpretation" | PARTLY | It is None |
| 251-259 | OCRResult table: confidence "self-confidence or avg score (Tesseract)"; metadata "model, token usage, batch index" | PARTLY | Free-form confidence is 1.0. Tesseract gives None via `OCR.read`, since the average needs `with_confidence`. Tesseract metadata has no model or token_usage |
| 261-283 | results example | TRUE | |
| 293-311 | registry; "gemini behaves identically" | TRUE | For the facade. Loader `"gemini"` bypasses loader knobs (probe) |
| 320-324 | lookup case-insensitive; unknown raises ValueError | TRUE | base.py:672-679 |
| 330 | `list_providers()` e.g. `['openai','vertex_ai','gemini','tesseract']` | PARTLY | Actual: `['openai','tesseract','vertex_ai','gemini']` (probe) |
| 331-332, 341-344 | factory create; BaseOCR abstract method | TRUE | base.py:319-333, 653-682 |
| 357-363 | OpenAIOCR summary; precedence | PARTLY | Precedence as B1 344-345 |
| 369-371 | example | TRUE | |
| 380-385 | VertexAIOCR: set `GOOGLE_CLOUD_PROJECT` "or pass `project=`" | PARTLY | `project=` works on `VertexAIOCR(...)` and `UnifiedDocumentLoader(project=)`, **not** on `OCR(...)` |
| 391-393 | `OCR("gemini", project="my-gcp-project")` | **FALSE** | TypeError (probe) |
| 402-417 | TesseractOCR text and example | TRUE | Probe (fake engine) |

### B3. `docs/api/schema.rst`

| L | claim | verdict | evidence |
|---|---|---|---|
| 4-7 | every image becomes an OCRPage | PARTLY | Not for `structured=False`, a failed Tesseract image, or a Vertex BYO model |
| 16-18 | raw is always present, verbatim, the auditable record | PARTLY | See B1 L74 (recovery puts free-form text in `raw.text`) |
| 18-24 | interpretation None cases | PARTLY | OpenAI `detail="raw"` is not enforced |
| 26-27 | all Pydantic models; **every field is defaulted** | TRUE | Probe: 13 models, 0 required fields, all construct with no args |
| 27-30 | strict mode needs all properties; Optional fields are `anyOf [T, null]` | TRUE | Probe: `to_strict_json_schema` required == all properties; `interpretation` / `page_title` / `row_count` are anyOf-null |
| 34-37 | import from `doc2mark.ocr.schema`; `to_markdown` powers `text` | TRUE | openai.py:1053; vertex_ai.py:754 |
| 48-53 | interpretation never invents or moves a printed value | UNVERIFIABLE-WITHOUT-KEY | Instructed via field descriptions only |
| 55-57 | invariant "mechanically enforced" by `router_invariants`, meant for CI/eval | PARTLY | Nothing calls it at runtime. Only the withholding subset is enforced (schema.py:1931-1966; openai.py:1048-1051) |
| 57-62 | list of the checks | TRUE | schema.py:1986-2054 |
| 78-82 | OCRPage signature | TRUE | schema.py:1669-1671 |
| 84-87 | `to_markdown` "prefers structured tables/fields over the flat text dump" | **FALSE** | Always emits `raw.text` first, then appends tables; **`raw.fields` are never rendered** (schema.py:1715-1717; probe) |
| 87-92 | overlay order; page_markdown replaces `raw.text` when it covers it; hidden tail | TRUE | ≥0.85 token coverage (1697-1707, 1900); the withheld marker and title heading are also emitted |
| 102-155 | RawExtraction / Table (validator sanitises + normalises) / KeyValue / Metric | TRUE | schema.py:1197-1317 |
| 165-170 | depth 4, no recursion, no unions, all defaulted | TRUE | Probe: depth 4 |
| 170-172 | BM42 invariant "enforced by `router_invariants`" | PARTLY | A checker, not runtime enforcement |
| 174-210 | Figure / DataPoint / DiagramNode / DiagramEdge / Section / Entity / Relation / page_markdown | TRUE | Field descriptions, schema.py:1326-1661 |
| 248-257 | 16 `document_type` values and the anchor list | TRUE | schema.py:1537-1661 |
| 264-328 | receipt example validates and asserts pass | TRUE | Probe. Rendered output = `raw.text` + "Items" table + metrics table; the fields are absent |

### B4. `docs/contextual_ocr.rst`

| L | claim | verdict | evidence |
|---|---|---|---|
| 8-11, 21-22 | `{k-1,k,k+1}` window attached as context; the image is the only target | TRUE | 2151-2178; base.py:208-215 |
| 13-16 | consistency and language benefits | UNVERIFIABLE-WITHOUT-KEY | |
| 23-25 | both providers "prepend" `_CONTEXT_PDF_INSTRUCTION` (base.py) | PARTLY | The name and location are right. It is a text part placed *after* the image and before the PDF (openai.py:155-172; vertex_ai.py:102-114) |
| 27-32 | quoted instruction | TRUE | base.py:208-215 |
| 34-39 | `_ROUTER_CONFIDENCE_CLAUSE` appended; ≥0.7 and "high" | TRUE | base.py:103-109; openai.py:164; vertex_ai.py:108 |
| 44-50 | `context_pages: int = 0` is a scope tier | TRUE | base.py:276 |
| 52-54 | tier 0 off, byte-identical | TRUE | The kwarg is omitted (924-927; probe) |
| 56-65 | tier 1 = renders; tier 2 = pictures too, one request per page | TRUE | 2244, 2254, 2261; probe (windows 2/3/2) |
| 67-73 | tier resolved once (quoted line); gating in `_ocr_jobs` / `_picture_jobs` | TRUE | 680 (the real line adds `if (self.ocr and cfg) else 0`), 2244, 2254 |
| 78-89 | clamped `insert_pdf`; `tobytes(deflate=True, garbage=3)` | TRUE | 2160-2165 |
| 91-94 | LRU of 4 keyed by page; "overlapping windows on adjacent pages do not rebuild work" | PARTLY | Per-page reuse: TRUE. Adjacent pages each build their own window (the key is the page index; nothing is shared) |
| 96-101 | 18 MB guard "to stay under Gemini's ~20 MB inline cap"; None on failure | PARTLY | Mechanics TRUE (55, 2168-2174). The cap applies to raw bytes, and base64 makes up to ~24 MiB, so the rationale does not hold near the cap (the Gemini limit itself is UNVERIFIABLE-WITHOUT-KEY) |
| 103-104 | raw base64 | TRUE | 2169 |
| 109-113 | `context_pdfs` positional, only when some image has context | TRUE | 916-927 |
| 115-132 | OpenAI `file` block; prefix gate; default model qualifies; non-PDF models skip it | TRUE | openai.py:78-84, 161-172; probe. When gated off, the no-context router clause is sent instead |
| 134-146 | Gemini `media` part; image first | TRUE | vertex_ai.py:102-114 (no PDF gate) |
| 151-157 | additive to the verbatim layer | TRUE | (read) |
| 162-180 | PDF sources only; loader example | TRUE | Only the PDF pipeline builds context; probe via loader + `OCRConfig(context_pages)` |
| 182-200 | tier 2 and cost notes | TRUE | "18 MB" = raw bytes, 18 MiB |

### B5. `README.md` (OCR sections)

| L | claim | verdict | evidence |
|---|---|---|---|
| 195-196 | "OCR providers are initialized only when OCR is requested" | PARTLY | The provider object is built with the loader (loader.py:149); only the LangChain client and key check are lazy |
| 200-228 | facade quick example; `read_one` | TRUE | Probes |
| 232-234 | every result carries an OCRPage | PARTLY | See B3 4-7 |
| 236-265 | OCRPage example | TRUE | Probe: it validates. It needs `from doc2mark.ocr import …`: `Table` and `KeyValue` are not exported from `doc2mark`. The `...` inside `html` becomes a `<caption>` |
| 267-270 | three table views | TRUE | Schema; `markdown` is rarely filled (UNVERIFIABLE-WITHOUT-KEY) |
| 274-275 | "Tasks replace the old prompt templates" | PARTLY | Only in structured mode. `prompt_template` still drives `structured=False` and the free-form recovery; the loader still accepts it |
| 277-289 | tasks, per call, per image, values | TRUE | |
| 293-298 | `detail="raw"` makes interpretation None | PARTLY | OpenAI: prompt-only |
| 300-305 | `structured=False` | TRUE | |
| 311-321 | OpenAI default model, key, extra, examples | TRUE | Probe |
| 324-326 | `base_url` works for Ollama / vLLM / LM Studio / gateways | PARTLY | The wiring is TRUE, but an API key is still mandatory (RuntimeError without one). Support for the `json_schema` structured output is UNVERIFIABLE-WITHOUT-KEY |
| 330-331 | `vertex_ai` / `gemini` names | TRUE | |
| 333-336 | `GOOGLE_APPLICATION_CREDENTIALS` + extra | UNVERIFIABLE-WITHOUT-KEY | Omits `GOOGLE_CLOUD_PROJECT`, which docs/ocr.rst lists; whether ADC alone supplies the project was not tested |
| 339 | `OCR("gemini")` | TRUE | |
| 340 | `OCR("vertex_ai", model="gemini-2.0-flash")` | **FALSE** | The model is silently ignored; `gemini-3.1-flash-lite-preview` is used (probe; vertex_ai.py:302, 331) |
| 345, 352 | Tesseract raw-only; `language="eng"` | TRUE | Native codes accepted |
| 347-349 | Tesseract install = `doc2mark[ocr]` | PARTLY | Binary and traineddata are also needed |
| 357-361 | provider comparison | PARTLY | openai and tesseract rows TRUE. "Highest accuracy" is UNVERIFIABLE. Vertex needs any ADC credential, not specifically a "service account" |
| 365-372 | concurrency; LangChain default when unset | TRUE | base.py:36-54 |
| 374-381 | `OCR_MAX_IMAGE_DIM` off by default; images within the bound untouched | TRUE | image_utils.py:163-193, 281-306 (LLM providers) |
| 385-393 | loader with OCR | TRUE | |
| 397-401 | deprecation: "setting them now emits a DeprecationWarning" | PARTLY | Only for non-default values, only with the OpenAI/Vertex/Gemini providers, and hidden by default filters |
| 533-546 | OCR tasks section | TRUE | |
| 550-551 | per-result token usage for OpenAI/Gemini | TRUE | `token_usage` is always present (`{}` on failure); recovery-call tokens are missing (A3) |
| 553-562 | printed dict has exactly the 3 keys | PARTLY | Per result, the raw LangChain `usage_metadata` may add `input_token_details` / `output_token_details`. The 3-key shape is the document-level `extra["token_usage"]` |

### B6. `docs/api/loader.rst` (OCR / model tuning, plus related lines)

| L | claim | verdict | evidence |
|---|---|---|---|
| 24-48 | constructor signature | PARTLY | Omits `legibility_judge`, `boilerplate_judge`, `judge` (loader.py:66-71) |
| 50-52 | provider built eagerly; None / "none" / "disabled" turn OCR off | TRUE | loader.py:149, 270-272 |
| 61-66 | api_key fallback; default ocr_config created | TRUE | loader.py:233. For openai, its model/temperature/max_tokens/base_url are overridden |
| 79-81 | `model` default; vertex_ai swaps in a Gemini model | PARTLY | The swap is TRUE only for `"vertex_ai"` and only when the value equals "gpt-5.4-mini" (314). `"gemini"` ignores `model` (probe) |
| 82-83 | temperature default 0 | TRUE | LangChain drops it for gpt-5* (probe) |
| 84-85 | `max_tokens` default **4096** | **FALSE** | Default is 8192 (loader.py:44; the same page's signature at L32 says 8192) |
| 86-89, 119-124 | `max_workers` is the OCR-internal concurrency (default 5) | **FALSE** | Stored on OpenAIOCR, never used (openai.py:450); not passed to Vertex. The real knob is `OCRConfig.max_concurrency` / `OCR_MAX_CONCURRENCY` |
| 90-92 | `prompt_template` presets | PARTLY | Names are valid (plus form_/handwriting_/code_focused), but they only affect `structured=False` and the free-form recovery (probe) |
| 93-94 | `timeout` / `max_retries` per request | PARTLY | Forwarded for openai (probe); **not** forwarded for vertex_ai or gemini (stays 30/3) |
| 95-96 | `top_p` / `frequency_penalty` / `presence_penalty` are OpenAI sampling controls | **FALSE** | Never sent to the model (probe: `ChatOpenAI.top_p=None`, `model_kwargs={}`) |
| 97-98 | `base_url` | TRUE | openai.py:445-446 (it beats `OCRConfig.base_url`) |
| 99-101 | project / location | PARTLY | vertex_ai: TRUE. Ignored for `"gemini"` (probe) |
| 102-103 | `default_prompt` overrides the built-in templates | **FALSE** | Never used in any prompt; only enters the OCR cache key (cache.py:260; probe) |
| 108-117 | task / structured / detail override the config | TRUE | loader.py:218-243 |
| 151-153 | `ocr_images` "requires `extract_images=True`" | **FALSE** | `ocr_images=True` implies `extract_images=True` when OCR is configured (loader.py:630-634) |
| 175-179 | example with `prompt_template='table_focused'` | PARTLY | No effect on the default structured output |
