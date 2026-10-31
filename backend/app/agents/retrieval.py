"""Bounded company/JD/CV retrieval adapted from T10's RAG interview agent.

Retains overlapping chunks and source diversity. Uses lexical relevance instead of
an independent embedding client, keeping all provider access in the run gateway.
Excerpts are always unchanged substrings so host quotation validation still applies.
"""

import re


def chunks(text, size=1600, overlap=200):
    text = text.strip()
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size - overlap)]


def evidence_context(sources, case, request):
    words = set(
        re.findall(
            r"\w+", request.lower() + " " + " ".join(c["text"] for c in case.get("criteria", []))
        )
    )
    items = []
    for source in sources:
        for piece in chunks(source["excerpt"]):
            score = len(words & set(re.findall(r"\w+", piece.lower())))
            items.append((score, dict(source, excerpt=piece)))
    # Preserve at least one relevant excerpt per source key, as the original RAG
    # retriever preserved company/JD/CV diversity before filling its top-k budget.
    items.sort(key=lambda item: item[0], reverse=True)
    selected, seen = [], set()
    for _, source in items:
        if source["key"] not in seen:
            selected.append(source)
            seen.add(source["key"])
    for _, source in items:
        if source not in selected and len(selected) < 60:
            selected.append(source)
    return selected[:60]
