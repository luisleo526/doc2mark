Structured OCR Schema
=====================

doc2mark's OCR layer does not return a single free-form markdown blob. Instead,
every image becomes a structured :class:`~doc2mark.ocr.schema.OCRPage`, carried
on ``OCRResult.document``. The schema enforces a hard boundary between two
concerns:

- **raw** — what is *literally* on the page: a verbatim transcription, any
  tables, and label/value fields. No inference, no commentary.
- **interpretation** — the model's *reading* of the page: document type, a
  short summary, key findings, and confidence. This is the part that requires a
  language model to reason about the content.

This split is the most important idea in the schema. The ``raw`` half is
always present and is the trustworthy, auditable record of the page. The
``interpretation`` half is :data:`None` whenever the model was not asked to —
or could not — reason about the page, specifically:

- ``detail="raw"`` was requested (raw transcription only),
- the provider is non-LLM (e.g. Tesseract, which cannot infer), or
- the structured-output parse failed and the layer fell back gracefully.

All five models are Pydantic ``BaseModel`` subclasses. Every field is
defaulted: the LLM providers emit them through LangChain's
``with_structured_output(method="json_schema")``, and OpenAI strict mode
requires all properties to be present, so each field must be satisfiable
without input. Optional fields serialize as ``anyOf: [T, null]``.

.. note::

   Import these from ``doc2mark.ocr.schema``. The convenience accessor
   :meth:`~doc2mark.ocr.schema.OCRPage.to_markdown` collapses the structured
   page back into a single markdown string, which is what powers the
   back-compatible ``OCRResult.text``.


``OCRPage`` — the top-level result
----------------------------------

One image's structured OCR result. It bundles the always-present ``raw``
extraction with the optional ``interpretation``.

**Signature**

.. code-block:: python

   class OCRPage(BaseModel):
       raw: RawExtraction = Field(default_factory=RawExtraction)
       interpretation: Optional[Interpretation] = None

**Fields**

.. list-table::
   :header-rows: 1
   :widths: 20 28 22 30

   * - Name
     - Type
     - Default
     - Meaning
   * - ``raw``
     - :class:`~doc2mark.ocr.schema.RawExtraction`
     - empty ``RawExtraction``
     - Verbatim page content. Always present.
   * - ``interpretation``
     - ``Optional[`` :class:`~doc2mark.ocr.schema.Interpretation` ``]``
     - ``None``
     - The model's analysis, or ``None`` for ``detail="raw"``, non-LLM
       providers, and parse-error fallback.

**Methods**

``to_markdown() -> str``
    Render a readable markdown view of the page. Prefers structured
    tables/fields over the flat text dump: it emits ``raw.text`` (stripped)
    followed by each table — using the table's own ``markdown`` when present,
    otherwise rendering a markdown grid from ``headers`` + ``rows``. This is the
    string surfaced as the back-compatible ``OCRResult.text``.

.. note::

   Always check ``page.interpretation is not None`` before reading interpretive
   fields. With ``detail="raw"`` or a Tesseract backend it will be ``None``.

**Example**

.. code-block:: python

   from doc2mark.ocr.schema import OCRPage

   page: OCRPage = ...  # obtained from an OCRResult.document

   # The raw half is always safe to read.
   print(page.raw.text)

   # The interpretation half may be absent.
   if page.interpretation is not None:
       print(page.interpretation.document_type)  # e.g. "receipt"
       print(page.interpretation.summary)

   # Collapse to a single markdown string (back-compat OCRResult.text).
   markdown = page.to_markdown()

.. autoclass:: doc2mark.ocr.schema.OCRPage
   :members:
   :show-inheritance:


``RawExtraction`` — verbatim page content
-----------------------------------------

A verbatim transcription of the page. No commentary, no inference. This is the
``raw`` field of :class:`~doc2mark.ocr.schema.OCRPage`. No analysis belongs
here.

**Fields**

.. list-table::
   :header-rows: 1
   :widths: 22 28 20 30

   * - Name
     - Type
     - Default
     - Meaning
   * - ``text``
     - ``str``
     - ``""``
     - All visible text, verbatim, in the original language. No analysis.
   * - ``tables``
     - ``List[`` :class:`~doc2mark.ocr.schema.Table` ``]``
     - ``[]``
     - Tables transcribed from the image.
   * - ``fields``
     - ``List[`` :class:`~doc2mark.ocr.schema.KeyValue` ``]``
     - ``[]``
     - Label/value pairs for forms & receipts.
   * - ``detected_language``
     - ``Optional[str]``
     - ``None``
     - The language actually seen on the page (not an echo of config).
   * - ``has_handwriting``
     - ``bool``
     - ``False``
     - Whether handwriting was detected on the page.

**Example**

.. code-block:: python

   from doc2mark.ocr.schema import RawExtraction, KeyValue

   raw = RawExtraction(
       text="ACME STORE\nThank you for shopping!",
       fields=[
           KeyValue(label="Total", value="$42.50"),
           KeyValue(label="Date", value="2026-06-27"),
       ],
       detected_language="en",
   )

.. autoclass:: doc2mark.ocr.schema.RawExtraction
   :members:
   :show-inheritance:


``Interpretation`` — the model's reading
----------------------------------------

The model's analysis of the page. Never mixed into ``raw``. This is the
``interpretation`` field of :class:`~doc2mark.ocr.schema.OCRPage`, and is
``None`` whenever the model was not asked to (or could not) reason.

**Fields**

.. list-table::
   :header-rows: 1
   :widths: 22 30 18 30

   * - Name
     - Type
     - Default
     - Meaning
   * - ``document_type``
     - ``Literal["document", "table", "form", "receipt", "handwriting",
       "code", "chart", "photo", "mixed", "blank", "other"]``
     - ``"other"``
     - The model's classification of the page.
   * - ``summary``
     - ``str``
     - ``""``
     - 1-3 sentence description of the content and its purpose.
   * - ``key_findings``
     - ``List[str]``
     - ``[]``
     - Notable points the model extracted from the page.
   * - ``reading_order``
     - ``List[int]``
     - ``[]``
     - Block indices in natural reading order, top-to-bottom.
   * - ``visual_notes``
     - ``str``
     - ``""``
     - Layout, branding, and non-text visual elements.
   * - ``self_confidence``
     - ``float`` (``0.0`` ≤ x ≤ ``1.0``)
     - ``0.0``
     - The model's own 0..1 confidence estimate.
   * - ``legibility``
     - ``Literal["high", "medium", "low"]``
     - ``"high"``
     - The model's estimate of how legible the page is.

**Example**

.. code-block:: python

   from doc2mark.ocr.schema import Interpretation

   interpretation = Interpretation(
       document_type="receipt",
       summary="Grocery receipt from ACME Store for three items totaling $42.50.",
       key_findings=["3 line items", "Total: $42.50", "Paid by card"],
       self_confidence=0.92,
       legibility="high",
   )

.. autoclass:: doc2mark.ocr.schema.Interpretation
   :members:
   :show-inheritance:


``Table`` — a transcribed table
-------------------------------

A table transcribed verbatim from the image. Used inside the ``tables`` list of
:class:`~doc2mark.ocr.schema.RawExtraction`.

**Fields**

.. list-table::
   :header-rows: 1
   :widths: 20 24 22 34

   * - Name
     - Type
     - Default
     - Meaning
   * - ``caption``
     - ``str``
     - ``""``
     - Optional caption/title for the table.
   * - ``headers``
     - ``List[str]``
     - ``[]``
     - Column headers.
   * - ``rows``
     - ``List[List[str]]``
     - ``[]``
     - Row data, each row a list of cell strings.
   * - ``markdown``
     - ``str``
     - ``""``
     - Rendered markdown fallback for merged/complex cells the ``headers`` +
       ``rows`` grid cannot capture. When set,
       :meth:`~doc2mark.ocr.schema.OCRPage.to_markdown` prefers it over the
       grid.

**Example**

.. code-block:: python

   from doc2mark.ocr.schema import Table

   table = Table(
       caption="Items",
       headers=["Item", "Qty", "Price"],
       rows=[
           ["Apples", "2", "$3.00"],
           ["Bread", "1", "$2.50"],
       ],
   )

.. autoclass:: doc2mark.ocr.schema.Table
   :members:
   :show-inheritance:


``KeyValue`` — a label/value pair
---------------------------------

A single label/value pair, e.g. for forms and receipts. Used inside the
``fields`` list of :class:`~doc2mark.ocr.schema.RawExtraction`.

**Fields**

.. list-table::
   :header-rows: 1
   :widths: 20 20 20 40

   * - Name
     - Type
     - Default
     - Meaning
   * - ``label``
     - ``str``
     - ``""``
     - The field label, e.g. ``"Total"``.
   * - ``value``
     - ``str``
     - ``""``
     - The field value, e.g. ``"$42.50"``.

**Example**

.. code-block:: python

   from doc2mark.ocr.schema import KeyValue

   field = KeyValue(label="Total", value="$42.50")

.. autoclass:: doc2mark.ocr.schema.KeyValue
   :members:
   :show-inheritance:


A complete filled ``OCRPage`` (receipt)
---------------------------------------

The example below shows a fully populated page for a grocery receipt, with both
the verbatim ``raw`` half and the model's ``interpretation`` half.

.. code-block:: python

   from doc2mark.ocr.schema import (
       OCRPage, RawExtraction, Interpretation, Table, KeyValue,
   )

   page = OCRPage(
       raw=RawExtraction(
           text=(
               "ACME STORE\n"
               "123 Market St\n"
               "Apples  2  $3.00\n"
               "Bread   1  $2.50\n"
               "Milk    1  $4.00\n"
               "Total      $9.50\n"
               "Thank you for shopping!"
           ),
           tables=[
               Table(
                   caption="Items",
                   headers=["Item", "Qty", "Price"],
                   rows=[
                       ["Apples", "2", "$3.00"],
                       ["Bread", "1", "$2.50"],
                       ["Milk", "1", "$4.00"],
                   ],
               ),
           ],
           fields=[
               KeyValue(label="Store", value="ACME STORE"),
               KeyValue(label="Total", value="$9.50"),
           ],
           detected_language="en",
           has_handwriting=False,
       ),
       interpretation=Interpretation(
           document_type="receipt",
           summary=(
               "Grocery receipt from ACME Store listing three items "
               "with a total of $9.50."
           ),
           key_findings=["3 line items", "Total: $9.50"],
           self_confidence=0.94,
           legibility="high",
       ),
   )

   # Always-present raw half:
   assert page.raw.fields[1].value == "$9.50"

   # Interpretation present here because an LLM provider ran with full detail:
   assert page.interpretation is not None
   assert page.interpretation.document_type == "receipt"

   # Single-string markdown view (back-compat OCRResult.text):
   print(page.to_markdown())
