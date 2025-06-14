# doc2mark RAG Pipeline Architecture

## Table Extraction Flow for RAG Applications

```mermaid
graph TD
    A[Complex Documents] --> B{doc2mark}
    
    subgraph "Input Documents"
        A1[Excel with Merged Cells]
        A2[Scanned PDF Tables]
        A3[Word with Nested Tables]
        A4[PowerPoint Data]
    end
    
    A1 --> B
    A2 --> B
    A3 --> B
    A4 --> B
    
    B --> C[Table Detection & Extraction]
    
    subgraph "doc2mark Processing"
        C --> D[Structure Preservation]
        D --> E[AI-Powered OCR]
        E --> F[Format Conversion]
    end
    
    F --> G{Output Formats}
    
    G --> H[Markdown Tables]
    G --> I[Structured JSON]
    G --> J[Plain Text]
    
    subgraph "RAG Integration"
        H --> K[LangChain Processing]
        I --> L[Direct Vector DB]
        J --> M[Traditional NLP]
        
        K --> N[Embedding & Chunking]
        L --> N
        M --> N
        
        N --> O[Vector Database]
        O --> P[RAG Application]
    end
    
    P --> Q[Accurate Table Q&A]
    P --> R[Cross-Document Analysis]
    P --> S[Data Insights]
```

## Example: Financial Report Processing

### Input: Complex Excel with Merged Cells
```
┌─────────────┬─────────────────────┬─────────────────────┐
│ Department  │      Q1 2024        │      Q2 2024        │
│             ├──────────┬──────────┼──────────┬──────────┤
│             │ Revenue  │   Cost   │ Revenue  │   Cost   │
├─────────────┼──────────┼──────────┼──────────┼──────────┤
│ Sales       │          │          │          │          │
│ - North     │  125.5   │   45.2   │  142.3   │   48.7   │
│ - South     │   89.2   │   32.1   │   95.1   │   35.2   │
└─────────────┴──────────┴──────────┴──────────┴──────────┘
```

### doc2mark Output: Preserved Structure
```markdown
| Department      | Q1 2024        | Q2 2024        |
|                 | Revenue | Cost | Revenue | Cost |
|-----------------|---------|------|---------|------|
| **Sales**       |         |      |         |      |
| - North Region  | 125.5   | 45.2 | 142.3   | 48.7 |
| - South Region  | 89.2    | 32.1 | 95.1    | 35.2 |
```

### RAG Query Examples
1. "What was the North Region's revenue growth from Q1 to Q2?"
2. "Compare costs across all departments for Q1"
3. "Which region had the highest revenue in Q2?"

All queries return accurate results because the table structure is preserved!