OCR Caching
===========

doc2mark provides a request-scoped OCR result cache that avoids redundant
(and expensive) calls to vision-model providers when the same image is
processed more than once.  Cached values use the **cache v4** schema,
which stores the full structured :class:`~doc2mark.OCRPage` document
alongside the legacy plain-text representation, so downstream consumers
always receive complete results regardless of whether the value was served
from cache or computed live.

Caching is wired into the processing pipeline through the ``ocr_cache``
parameter accepted by :func:`~doc2mark.load` and
:class:`~doc2mark.UnifiedDocumentLoader`.  You can create a cache with the
:func:`~doc2mark.create_ocr_cache` factory, or instantiate a backend class
directly.


Cache backends
--------------

OCRCache (abstract base)
~~~~~~~~~~~~~~~~~~~~~~~~

All cache backends implement the :class:`~doc2mark.OCRCache` interface.

.. code-block:: python

   from doc2mark import OCRCache

**Abstract methods**

``get(key)``
   Return the cached :class:`~doc2mark.ocr.base.OCRResult` for *key*, or
   ``None`` on a miss.

``set(key, result, ttl_seconds=None)``
   Store an :class:`~doc2mark.ocr.base.OCRResult`.  When *ttl_seconds* is
   ``None`` the backend's default TTL applies.

``cleanup()``
   Remove expired entries.  Returns the number of entries removed.

``clear()``
   Drop every entry from the cache.

``stats()``
   Return a ``dict`` of runtime counters (hits, misses, sets, evictions,
   etc.) and configuration metadata such as the backend name.

.. autoclass:: doc2mark.OCRCache
   :members:
   :show-inheritance:


MemoryOCRCache
~~~~~~~~~~~~~~

Thread-safe, in-process LRU cache with TTL, absolute max-age, and
refresh-on-access semantics.  This is the default backend returned by
:func:`~doc2mark.create_ocr_cache` when ``provider="memory"``.

.. code-block:: python

   from doc2mark import MemoryOCRCache

   cache = MemoryOCRCache(
       ttl_seconds=3600,
       max_age_seconds=43200,
       max_entries=1024,
       max_refreshes=10,
   )

**Constructor parameters**

``ttl_seconds`` *(float, default 3600)*
   Time-to-live for each entry in seconds.  An entry that is accessed
   before expiry has its deadline extended (up to *max_refreshes* times).

``max_age_seconds`` *(Optional[float], default 43200)*
   Absolute upper bound on how long an entry may live, regardless of
   refresh activity.  Set to ``None`` to disable the hard ceiling.

``max_entries`` *(int, default 1024)*
   Maximum number of entries.  When exceeded the least-recently-used entry
   is evicted.

``max_refreshes`` *(Optional[int], default 10)*
   Maximum number of times an entry's TTL may be extended on access.
   ``None`` allows unlimited refreshes.

``time_func`` *(Optional[Callable[[], float]], default None)*
   Clock function used for timestamps.  Defaults to
   :func:`time.time`.  Mainly useful in tests.

.. autoclass:: doc2mark.MemoryOCRCache
   :members:
   :show-inheritance:


RedisOCRCache
~~~~~~~~~~~~~

Persistent, cross-process cache backed by Redis.  Requires the optional
``redis`` dependency (install with ``pip install doc2mark[redis]``).

The constructor lazily imports ``redis``, connects using the provided URL,
and verifies reachability with ``ping()``.  If the connection fails at
construction time and a *fallback* is configured via
:func:`~doc2mark.create_ocr_cache`, the factory falls back transparently.

.. code-block:: python

   from doc2mark import RedisOCRCache

   cache = RedisOCRCache(
       redis_url="redis://localhost:6379/0",
       ttl_seconds=3600,
       max_age_seconds=43200,
       max_refreshes=10,
       key_prefix="doc2mark:ocr:ocr-cache-v4",
   )

**Constructor parameters**

``redis_url`` *(str)*
   Redis connection URL (e.g. ``"redis://localhost:6379/0"``).  Required;
   an empty string raises ``ValueError``.

``ttl_seconds`` *(float, default 3600)*
   Per-entry time-to-live in seconds, extended on access.

``max_age_seconds`` *(Optional[float], default 43200)*
   Absolute entry lifetime cap.  ``None`` disables the limit.

``max_refreshes`` *(Optional[int], default 10)*
   Maximum refresh count per entry.

``key_prefix`` *(str, default "doc2mark:ocr:ocr-cache-v4")*
   Prefix prepended to every Redis key.  Change this to namespace
   multiple applications sharing the same Redis instance.

``time_func`` *(Optional[Callable[[], float]], default None)*
   Clock override, as in :class:`~doc2mark.MemoryOCRCache`.

.. note::

   ``cleanup()`` is a no-op for the Redis backend because Redis manages
   key expiration natively via the ``EX`` argument on ``SET``.

.. autoclass:: doc2mark.RedisOCRCache
   :members:
   :show-inheritance:


NoOpOCRCache
~~~~~~~~~~~~

A cache that never stores anything.  Every ``get()`` returns ``None``.
Useful in testing or when you want to satisfy a type constraint without
actually caching.

.. code-block:: python

   from doc2mark import NoOpOCRCache

   cache = NoOpOCRCache()  # always misses

.. autoclass:: doc2mark.NoOpOCRCache
   :members:
   :show-inheritance:


CachedOCR
~~~~~~~~~~

A transparent :class:`~doc2mark.ocr.base.BaseOCR` wrapper that
intercepts ``process_image`` and ``batch_process_images`` calls, checks
the cache first, and only forwards misses to the underlying provider.
Duplicate images within the same batch are de-duplicated so the provider
sees each unique image at most once.

You rarely need to instantiate ``CachedOCR`` yourself -- the loader
creates one internally when you pass ``ocr_cache`` to
:class:`~doc2mark.UnifiedDocumentLoader`.

.. code-block:: python

   from doc2mark.ocr.cache import CachedOCR
   from doc2mark import MemoryOCRCache, OCRFactory, OCRConfig

   provider = OCRFactory.create("openai", api_key="sk-...")
   cache = MemoryOCRCache()
   cached_provider = CachedOCR(wrapped=provider, cache=cache)

   # Use cached_provider like any BaseOCR instance
   result = cached_provider.process_image(image_bytes)

**Constructor parameters**

``wrapped`` *(:class:`~doc2mark.ocr.base.BaseOCR`)*
   The real OCR provider to delegate cache misses to.

``cache`` *(Optional[:class:`~doc2mark.OCRCache`])*
   The cache backend.  If ``None``, a :class:`~doc2mark.NoOpOCRCache` is
   substituted (effectively disabling caching).

``cache_version`` *(str, default "ocr-cache-v4")*
   Schema version embedded in every cache key.  Changing this value
   invalidates all existing entries, which is useful after a breaking
   change to the serialization format.

.. autoclass:: doc2mark.ocr.cache.CachedOCR
   :members:
   :show-inheritance:


Factory function
----------------

create_ocr_cache
~~~~~~~~~~~~~~~~

The recommended way to obtain a cache backend.  It maps a short string
name to the corresponding class, forwarding common tuning parameters.

.. code-block:: python

   from doc2mark import create_ocr_cache

   # In-memory cache (default settings)
   cache = create_ocr_cache("memory")

   # Redis-backed cache (requires pip install doc2mark[redis])
   cache = create_ocr_cache(
       "redis",
       redis_url="redis://localhost:6379/0",
       fallback="memory",
   )

   # Disable caching explicitly
   cache = create_ocr_cache("none")  # returns None

**Parameters**

``provider`` *(Optional[str], default "none")*
   Backend name.  Recognized values:

   - ``"memory"`` / ``"in-memory"`` / ``"in_memory"`` --
     :class:`~doc2mark.MemoryOCRCache`
   - ``"redis"`` -- :class:`~doc2mark.RedisOCRCache`
   - ``"noop"`` / ``"no-op"`` -- :class:`~doc2mark.NoOpOCRCache`
   - ``"none"`` / ``"off"`` / ``"false"`` / ``"disabled"`` / ``""`` --
     returns ``None`` (no caching)

``redis_url`` *(Optional[str], default None)*
   Connection URL when *provider* is ``"redis"``.

``fallback`` *(str, default "memory")*
   Backend to use when Redis is unreachable.  ``"memory"`` silently
   degrades to an in-process cache; ``"none"`` disables caching;
   ``"raise"`` re-raises the original connection error.

``ttl_seconds`` *(float, default 3600)*
   Per-entry time-to-live forwarded to the backend.

``max_age_seconds`` *(Optional[float], default 43200)*
   Absolute entry lifetime forwarded to the backend.

``max_refreshes`` *(Optional[int], default 10)*
   Refresh cap forwarded to the backend.

``max_entries`` *(int, default 1024)*
   Maximum entries (memory backend only).

``key_prefix`` *(str, default "doc2mark:ocr:ocr-cache-v4")*
   Redis key namespace (Redis backend only).

**Returns**

An :class:`~doc2mark.OCRCache` instance, or ``None`` when caching is
disabled.

.. autofunction:: doc2mark.create_ocr_cache


Wiring caching into the pipeline
---------------------------------

Pass the cache to :func:`~doc2mark.load` or construct a
:class:`~doc2mark.UnifiedDocumentLoader` with it:

.. code-block:: python

   from doc2mark import load, create_ocr_cache

   cache = create_ocr_cache("memory")
   doc = load(
       "report.pdf",
       extract_images=True,
       ocr_images=True,
       ocr_cache=cache,
   )

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader, create_ocr_cache

   cache = create_ocr_cache(
       "redis",
       redis_url="redis://localhost:6379/0",
       fallback="memory",
   )
   loader = UnifiedDocumentLoader(
       ocr_provider="openai",
       api_key="sk-...",
       ocr_cache=cache,
   )
   doc = loader.load("report.pdf", extract_images=True, ocr_images=True)
