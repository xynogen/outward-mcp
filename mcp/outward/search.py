"""Small, offline BM25 index. Names are the public keys; game IDs never enter the index."""
from __future__ import annotations

import heapq
import math
import re
import unicodedata
from collections import Counter, defaultdict


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    return re.findall(r"[^\W_]+", re.sub(r"\b([^\W_]+)'s\b", r"\1", text))


def without_ids(value):
    if isinstance(value, dict):
        return {k: without_ids(v) for k, v in value.items()
                if k not in {"id", "uid", "objectid", "scenename", "save"} and not k.endswith("_id")}
    if isinstance(value, list):
        return [without_ids(v) for v in value]
    return value


def words(value) -> str:
    if isinstance(value, dict):
        return " ".join(f"{k} {words(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(words(v) for v in value)
    return str(value) if value is not None else ""


class SearchIndex:
    def __init__(self, documents: list[dict]):
        self.documents = documents
        self.postings = defaultdict(dict)
        self.lengths = []
        for number, doc in enumerate(documents):
            # Weight names without introducing a separate field-scoring algorithm.
            terms = Counter(tokens(f"{doc['name']} {doc['name']} {doc['name']} "
                                   f"{words(doc['facts'])}"))
            self.lengths.append(sum(terms.values()))
            for term, count in terms.items():
                self.postings[term][number] = count
        self.average_length = sum(self.lengths) / len(documents) if documents else 1

    def search(self, query: str, kind: str = "all", limit: int = 10, offset: int = 0,
               eligible: set[int] | None = None) -> tuple[list[tuple[dict, float]], int]:
        allowed = {n for n, doc in enumerate(self.documents)
                   if (kind == "all" or doc['kind'] == kind) and (eligible is None or n in eligible)}
        terms = set(tokens(query))
        if not terms:
            rows = sorted(allowed, key=lambda n: (self.documents[n]["name"].casefold(), n))
            return [(self.documents[n], 0.0) for n in rows[offset:offset + limit]], len(rows)
        scores = defaultdict(float)
        count = len(self.documents)
        for term in sorted(terms):
            posting = self.postings.get(term, {})
            idf = math.log1p((count - len(posting) + 0.5) / (len(posting) + 0.5))
            for number, frequency in posting.items():
                if number in allowed:
                    length = self.lengths[number] / self.average_length
                    scores[number] += idf * frequency * 2.5 / (frequency + 1.5 * (0.25 + 0.75 * length))
        phrase = tokens(query)
        for number in scores:
            if tokens(self.documents[number]["name"]) == phrase:
                scores[number] += 10  # exact names should beat mentions in descriptions
        best = heapq.nsmallest(offset + limit, scores, key=lambda n: (-scores[n], n))
        return [(self.documents[n], round(scores[n], 4)) for n in best[offset:]], len(scores)
