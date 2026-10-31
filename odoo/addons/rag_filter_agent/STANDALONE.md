# Standalone usage

`models/matcher.py` has no Odoo dependency and can be embedded in another Python
application. The recommended entry point is `Matcher`:

```python
from matcher import Matcher

matcher = Matcher(
    model="your-pdf-capable-model",
    api_key="...",
    # base_url="https://api.openai.com/v1",  # or another compatible endpoint
)

requirements, results = matcher.rank(
    ("job.pdf", jd_pdf_bytes),
    [("alice.pdf", alice_pdf_bytes), ("bob.pdf", bob_pdf_bytes)],
)
```

For a web service or queue worker, inject a callable with this signature instead
of using HTTP inside the module:

```python
matcher = Matcher("model", chat_fn=my_chat_function)
```

The callable receives `(model, system, parts, schema)` and must return a Python
dictionary matching the supplied JSON schema.

For a SaaS application, use the application-layer DTOs and service instead of
passing unvalidated dictionaries between controllers and the matcher:

```python
from rag_filter_application.dto.requests import CandidateDocumentRequest, ScreeningRequest
from rag_filter_application.services.screening_service import ScreeningService
from matcher import rank

request = ScreeningRequest(
    jd_filename="job.pdf",
    jd_content=jd_pdf_bytes,
    candidates=(CandidateDocumentRequest("cv.pdf", cv_pdf_bytes),),
    model="your-pdf-capable-model",
)
response = ScreeningService(rank).execute(request)
payload = response.to_dict()
```

The controller owns request validation/serialization; the application service
owns the use case; the matcher/OpenAI client remains an injected infrastructure
dependency.
