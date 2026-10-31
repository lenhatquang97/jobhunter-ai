"""English fictional fixture content and dependency-free readable document files."""

import io
import textwrap
import zipfile
from xml.sax.saxutils import escape

ROLES = [
    {"key": "python", "title": "Python Backend Engineer", "skills": ["Python", "FastAPI", "PostgreSQL", "REST API", "Docker"], "work": "Build reliable recruitment APIs, validate requests, maintain database migrations, and document operational decisions."},
    {"key": "flutter", "title": "Flutter Engineer", "skills": ["Dart", "Flutter", "Riverpod", "Widget Testing", "Mobile CI/CD"], "work": "Build accessible mobile screens, manage application state, handle offline errors, and maintain release pipelines."},
    {"key": "android", "title": "Android Engineer", "skills": ["Kotlin", "Jetpack Compose", "Coroutines", "Room", "Android Testing"], "work": "Develop native Android features, implement local persistence, handle asynchronous work, and verify lifecycle behavior."},
    {"key": "qa", "title": "QA Automation Engineer", "skills": ["Playwright", "API Testing", "Pytest", "Test Design", "GitHub Actions"], "work": "Design reproducible checks, automate critical browser and API journeys, diagnose failures, and maintain reliable CI runs."},
    {"key": "design", "title": "Product Designer", "skills": ["Figma", "Prototyping", "User Research", "Interaction Design", "Design Systems"], "work": "Research recruitment workflows, prototype accessible interactions, document design decisions, and collaborate with engineers."},
]
NAMES = ("Alex", "Blair", "Casey", "Dana", "Ellis", "Frankie", "Gray", "Harper", "Indigo", "Jules", "Kai", "Logan", "Morgan", "Noel", "Oakley")


def criteria(role):
    return "\n".join(f"Describe personal project work using {skill}." for skill in role["skills"])


def candidate(role_index, scenario):
    role = ROLES[role_index]
    number = role_index * 3 + scenario
    name = f"[DEMO] {NAMES[number]} - {role['title']}"
    skills = role["skills"][:(5 if scenario == 0 else 2 if scenario == 1 else 0)]
    lines = [name, "[DEMO] Fictional person and experience; synthetic testing data only.", "Desired role: " + role["title"], "Experience: Fictional Studio, 2023-2026. All claims below are synthetic self-report."]
    if skills:
        lines += [f"I personally used {skill} to deliver and document a small project component, including a reproducible example and a review with teammates." for skill in skills]
        lines += ["My teammate owned infrastructure operations. I explained my own design and debugging decisions in a written project note."]
    else:
        lines += ["The submitted profile says I collaborated with a product team. It provides no specific tools, project outcomes, or individual implementation details."]
    if scenario == 1:
        lines += ["The profile does not document the other requested tools. Further questions are needed to establish any additional experience."]
    return {"name": name, "email": f"demo.candidate{number + 1:02d}@example.invalid", "skills": skills, "body": "\n".join(lines)}


def document(text, extension):
    if extension == "txt":
        return text.encode(), "text/plain"
    if extension == "docx":
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            paragraphs = "".join("<w:p><w:r><w:t>" + escape(line) + "</w:t></w:r></w:p>" for line in text.splitlines())
            archive.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + paragraphs + "<w:sectPr/></w:body></w:document>")
        return stream.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if extension != "pdf":
        raise ValueError("Unsupported demo document type")
    lines = [part for line in text.splitlines() for part in textwrap.wrap(line, 92)]
    if len(lines) > 60:
        raise ValueError("Demo PDF exceeds its single page")
    commands = ["BT /F1 10 Tf 36 805 Td 12 TL"]
    for line in lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"({safe}) Tj T*")
    commands.append("ET")
    content = "\n".join(commands).encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(result), "application/pdf"
