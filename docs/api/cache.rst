OCR cache
=========

How the caches behave (what is stored, for how long, what is never stored) is described in
:doc:`/caching`. Pass a cache as ``ocr_cache=`` to :class:`~doc2mark.UnifiedDocumentLoader` or to
the convenience functions; the loader wraps its provider in :class:`~doc2mark.ocr.cache.CachedOCR`.

.. autofunction:: doc2mark.create_ocr_cache

.. autoclass:: doc2mark.OCRCache
   :members:
   :show-inheritance:

.. autoclass:: doc2mark.MemoryOCRCache
   :members:
   :show-inheritance:

.. autoclass:: doc2mark.RedisOCRCache
   :members:
   :show-inheritance:

.. autoclass:: doc2mark.NoOpOCRCache
   :members:
   :show-inheritance:

.. autoclass:: doc2mark.ocr.cache.CachedOCR
   :members: process_image, batch_process_images
   :show-inheritance:
