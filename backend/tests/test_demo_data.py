"""Validate the actual synthetic files consumed by Odoo's document bridge."""
import sys
from io import BytesIO
from pathlib import Path

from docx import Document
from pypdf import PdfReader


def test_demo_documents_are_readable_and_explicitly_synthetic():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra"))
    from demo_data import ROLES, candidate, document

    assert len(ROLES) == 5
    assert len({skill for role in ROLES for skill in role['skills']}) == 25
    names = set()
    for role_index, _role in enumerate(ROLES):
        for scenario in range(3):
            item = candidate(role_index, scenario)
            names.add(item['name'])
            assert item['email'].endswith('@example.invalid')
            ext = ('pdf', 'docx', 'txt')[scenario]
            data, mime = document(item['body'], ext)
            if ext == 'pdf':
                text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(data)).pages)
            elif ext == 'docx':
                text = '\n'.join(p.text for p in Document(BytesIO(data)).paragraphs)
            else:
                text = data.decode()
            assert '[DEMO]' in text and 'fictional' in text.lower()
            assert item['name'] in text
            assert mime in ('application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'text/plain')
    assert len(names) == 15
