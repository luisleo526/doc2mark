import os 

os.environ["OPENAI_API_KEY"] = "sk-proj-1234567890"
from doc2mark import UnifiedDocumentLoader

# Basic usage
loader = UnifiedDocumentLoader()

result = loader.load(
    "sample_documents/test-table.pdf",
    extract_images=False,
    ocr_images=False
)

with open("result.md", "w") as f:
    f.write(result.content)