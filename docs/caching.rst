Caching
=======

doc2mark has two independent caches, both off by default:

- the **OCR cache** (``ocr_cache=``) keeps provider answers per image, so an image seen again
  (in this document, another document, or another run with Redis) is not sent again;
- the **document cache** (``cache_dir=``) keeps whole converted documents on disk, so loading
  an unchanged file with the same options returns the stored result.

OCR cache
---------

.. code-block:: python

   from doc2mark import MemoryOCRCache, UnifiedDocumentLoader

   cache = MemoryOCRCache(ttl_seconds=3600, max_entries=1024)
   loader = UnifiedDocumentLoader(ocr_provider="tesseract", ocr_cache=cache)
   first = loader.load("scan.pdf", ocr_images=True)
   again = loader.load("scan.pdf", ocr_images=True)       # answered from the cache
   print(cache.stats()["hits"], cache.stats()["sets"])

The same ``ocr_cache`` argument is accepted by :func:`~doc2mark.load`,
:func:`~doc2mark.document_to_markdown` and the batch functions. The loader wraps its provider in
:class:`~doc2mark.ocr.cache.CachedOCR`, which also sends identical images of one batch once. Keys
are built from the image bytes and every setting that changes an answer: the provider class, the
model, temperature, token limit and endpoint it sends, its project and location, its prompt
template and ``default_prompt``, its sampling settings, the ``OCRConfig`` task, language,
structured mode, detail, response model (with its schema) and parse-error mode, the per-call
options (including a neighbour-page PDF sent as context), the non-content judge, and the text of
doc2mark's own prompts and page schema (so another doc2mark version with other wording does not
replay these answers). API keys enter only as a hash. The request timeout, retries, concurrency
and client objects such as a rate limiter are not part of the key. The schema version
``ocr-cache-v7`` is part of every key: entries written by older versions are never read.

Backends
~~~~~~~~

- :class:`~doc2mark.MemoryOCRCache`: in-process, thread-safe LRU with ``max_entries`` (1024).
- :class:`~doc2mark.RedisOCRCache`: shared between processes and machines (``pip install
  "doc2mark[redis]"``); it pings Redis when created and raises if it cannot connect. Keys start
  with ``key_prefix`` (default ``doc2mark:ocr:ocr-cache-v7``) and expire through Redis itself.
- :class:`~doc2mark.NoOpOCRCache`: never stores anything.

:func:`~doc2mark.create_ocr_cache` builds one by name and can fall back when Redis is not
reachable:

.. code-block:: python

   from doc2mark import create_ocr_cache

   memory = create_ocr_cache("memory", ttl_seconds=7200)
   shared = create_ocr_cache("redis", redis_url="redis://localhost:6379/0", fallback="memory")
   off = create_ocr_cache("none")                        # None: no cache
   print(type(memory).__name__, type(shared).__name__, off)

``provider`` is ``"memory"`` (also ``"in-memory"``, ``"in_memory"``), ``"redis"``, ``"noop"``
(``"no-op"``) or ``"none"`` (also ``"off"``, ``"false"``, ``"disabled"``, ``""``: returns
``None``). ``fallback`` (``"memory"``, ``"none"`` or ``"raise"``) decides what happens when the
Redis cache cannot be created (server unreachable, ``redis`` not installed, no ``redis_url``);
the first two log a warning.

Lifetimes
~~~~~~~~~

``ttl_seconds`` (default 3600)
   How long an entry lives. A hit sets its expiry to ``ttl_seconds`` from now again, at most
   ``max_refreshes`` times (default 10; ``None``: no limit) ...
``max_age_seconds`` (default 43200)
   ... and never beyond this age from when it was stored (``None``: no limit).
``refusal_ttl_seconds`` (default 600)
   A provider's own refusal or safety block (OpenAI's refusal field, a Gemini SAFETY or
   RECITATION block) may not last, so it is kept this long only (or ``ttl_seconds`` when that is
   shorter), and a hit does not extend it.

What is cached
~~~~~~~~~~~~~~

Every answer, including an answer with no text and a "no readable text" statement: a blank page
or a photo without words gets the same answer every time, and asking again would cost a call.
Never cached, so the next run asks again: an answer the provider flagged ``failed`` (timeout,
rate limit, server error), a result that still withholds values after the router firewall's
verbatim redo, and an answer the optional judge could not screen. An entry of such a kind already
in a cache is treated as a miss. Each skipped write is logged at ``INFO``.

``cache.stats()`` returns the counters ``hits``, ``misses``, ``sets``, ``refreshes``,
``refresh_skipped``, ``expired``, ``evictions``, ``deletes``, ``errors`` and the backend's
settings.

Document cache
--------------

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider=None, cache_dir=".doc2mark-cache")
   first = loader.load("report.pdf")        # converted and stored
   second = loader.load("report.pdf")       # read back from .doc2mark-cache
   print(first.content == second.content)

Each entry is a JSON file named by a hash of the file's path, modification time and size, the
output format, the ``load()`` options (``extract_images``, ``ocr_images``, ``encoding``,
``delimiter``), the table style, the OCR provider with the settings that change its answers (the
same ones as in the OCR cache key above: model, task, language, structured mode, detail,
prompts, ...) and the neighbour-page context tier (``OCRConfig.context_pages``), the judges in use
and the routing version of the PDF pipeline, so a changed OCR setting converts the file again. The schema ``doc2mark-document-cache-v2`` is part of the name:
entries written by older versions are never read. Entries never expire; delete the folder to
clear it. A replayed document's ``metadata.extra["token_usage"]`` is
renamed ``token_usage_cached``, because it cost nothing this time (``metadata.extra["judge"]`` is
replayed as it was).

A document is **not** stored when its OCR is not a final answer: an image whose OCR failed
(flagged failed by the provider, or its request raised, for example without an API key), a PDF
picture that could not be extracted, a PDF page that shows content but whose OCR returned
nothing (``ocr_images["unread_pages"]``), an image the provider itself refused or blocked
(``ocr_issues["provider_refused"]``), or, with the optional judge, a question the judge could not
answer. An ``INFO`` log line names the reason; the next load converts it again, so a run after
the key or the provider is fixed reads the pictures.
