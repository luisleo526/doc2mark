OCR Result Caching
==================

doc2mark can cache OCR results so that repeated processing of the same images
skips the OCR provider call entirely. Caching is opt-in: pass an ``ocr_cache``
instance to :func:`~doc2mark.load`, :class:`~doc2mark.UnifiedDocumentLoader`,
or any of the batch helpers.

Two backends are included:

* **MemoryOCRCache** -- thread-safe, in-process LRU cache with TTL.
* **RedisOCRCache** -- persistent cache backed by Redis (requires
  ``pip install doc2mark[redis]``).

A ``NoOpOCRCache`` is also available for testing; it accepts writes but always
returns a miss.

Every answer is cached, including an answer with no text and a refusal or "no
readable text" statement: a blank page or a photo without words gets that answer
every time. Only a failed answer (flagged ``failed`` in its metadata by the
provider: a timeout, a rate limit, a server error) and a result that still
withholds values after the router firewall's redo are asked again on the next
run; an entry of either kind already in a cache is a miss, and each skipped write
is logged at INFO. The provider's own refusal or safety block (OpenAI's refusal
field, a Gemini SAFETY or RECITATION block: ``metadata["non_content"] ==
"provider_refusal"``) may not last, so it is cached for ``refusal_ttl_seconds``
only (default 600, ten minutes), which a hit does not extend; so is an empty
structured answer whose free-form recovery the provider refused or blocked.
Refusals cached before this TTL existed had none of their own, so the cache key
version moved to ``ocr-cache-v6``: entries written under an earlier version are
never read (a one-time miss for every image cached before). Likewise
``cache_dir`` (the loader's cache of converted documents, which never expires)
does not store a document whose OCR failed somewhere, left a page showing content
unread, or holds a provider's own refusal or block (see
``metadata.extra["ocr_images"]`` and ``["ocr_issues"]`` in :doc:`ocr_policy`).

Quick start
-----------

.. code-block:: python

   from doc2mark import load, MemoryOCRCache

   cache = MemoryOCRCache(
       ttl_seconds=3600,          # time-to-live per entry
       max_age_seconds=43200,     # hard upper bound on entry lifetime
       max_entries=1024,          # LRU eviction limit
       max_refreshes=10,          # how many times a hit extends the TTL
   )

   result = load(
       "scan.pdf",
       extract_images=True,
       ocr_images=True,
       ocr_cache=cache,
   )

Factory helper
--------------

:func:`~doc2mark.create_ocr_cache` creates a backend by name and handles
fallback when Redis is unavailable:

.. code-block:: python

   from doc2mark import create_ocr_cache

   # In-memory cache
   cache = create_ocr_cache("memory", ttl_seconds=7200)

   # Redis cache (falls back to memory if Redis is unreachable)
   cache = create_ocr_cache(
       "redis",
       redis_url="redis://localhost:6379/0",
       ttl_seconds=3600,
       max_age_seconds=43200,
       max_refreshes=10,
       key_prefix="doc2mark:ocr:ocr-cache-v6",  # default prefix
       fallback="memory",                        # "memory", "none", or "raise"
   )

   # Disable caching explicitly
   cache = create_ocr_cache("none")

Using with UnifiedDocumentLoader
--------------------------------

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader, create_ocr_cache

   cache = create_ocr_cache("redis", redis_url="redis://localhost:6379/0")

   loader = UnifiedDocumentLoader(
       ocr_provider="openai",
       ocr_cache=cache,
   )
   result = loader.load("scan.pdf", extract_images=True, ocr_images=True)

The same ``ocr_cache`` parameter is accepted by the convenience functions
:func:`~doc2mark.load`, :func:`~doc2mark.document_to_markdown`,
:func:`~doc2mark.batch_convert_to_markdown`, and
:func:`~doc2mark.batch_process_documents`.

MemoryOCRCache
--------------

.. code-block:: python

   from doc2mark import MemoryOCRCache

   cache = MemoryOCRCache(
       ttl_seconds=3600,
       max_age_seconds=43200,
       max_entries=1024,
       max_refreshes=10,
   )

Constructor parameters:

``ttl_seconds`` (float, default ``3600``)
    Time-to-live for each entry. A cache hit extends the expiry by this amount
    (up to ``max_refreshes`` times).

``max_age_seconds`` (float or ``None``, default ``43200``)
    Absolute maximum lifetime measured from creation. Set to ``None`` for no
    hard limit.

``max_entries`` (int, default ``1024``)
    Maximum number of cached entries. The least-recently-used entry is evicted
    when the limit is exceeded.

``max_refreshes`` (int or ``None``, default ``10``)
    Maximum number of times a hit can extend the TTL. Set to ``None`` for
    unlimited refreshes.

``refusal_ttl_seconds`` (float, default ``600``)
    How long a provider's own refusal or safety block is replayed. A hit does
    not extend it (an entry stored with its own ``ttl_seconds`` through
    ``set(key, result, ttl_seconds=...)`` is never extended by hits).
    ``create_ocr_cache`` accepts it too.

RedisOCRCache
-------------

Requires the ``redis`` extra:

.. code-block:: bash

   pip install doc2mark[redis]

.. code-block:: python

   from doc2mark import RedisOCRCache

   cache = RedisOCRCache(
       redis_url="redis://localhost:6379/0",
       ttl_seconds=3600,
       max_age_seconds=43200,
       max_refreshes=10,
       key_prefix="doc2mark:ocr:ocr-cache-v6",
   )

The constructor verifies the connection with ``ping()`` and raises on failure.
Redis handles expiry natively via ``EX`` on each key, so ``cleanup()`` is a
no-op.

Constructor parameters:

``redis_url`` (str, required)
    Redis connection URL, e.g. ``redis://localhost:6379/0``.

``ttl_seconds`` (float, default ``3600``)
    Same semantics as ``MemoryOCRCache``.

``max_age_seconds`` (float or ``None``, default ``43200``)
    Same semantics as ``MemoryOCRCache``.

``max_refreshes`` (int or ``None``, default ``10``)
    Same semantics as ``MemoryOCRCache``.

``refusal_ttl_seconds`` (float, default ``600``)
    Same semantics as ``MemoryOCRCache``.

``key_prefix`` (str, default ``"doc2mark:ocr:ocr-cache-v6"``)
    Prefix for all Redis keys. Useful for namespacing when multiple
    applications share a Redis instance.

Cache statistics
----------------

All backends expose a ``stats()`` method:

.. code-block:: python

   print(cache.stats())
   # {'hits': 12, 'misses': 3, 'sets': 3, 'backend': 'memory', ...}

Counters include ``hits``, ``misses``, ``sets``, ``refreshes``,
``refresh_skipped``, ``expired``, ``evictions``, ``deletes``, and ``errors``.
