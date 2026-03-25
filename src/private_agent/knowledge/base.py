from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


def _tokenize(text: str) -> list[str]:
    lowered = text.lower()
    raw_tokens = re.findall(r"[a-z0-9_]+", lowered)
    tokens: list[str] = []
    for token in raw_tokens:
        tokens.append(token)
        if len(token) > 4 and token.endswith("s"):
            tokens.append(token[:-1])
    tokens.extend(re.findall(r"[\u4e00-\u9fff]{1,8}", text))
    return tokens


@dataclass(slots=True)
class KnowledgeSnippet:
    path: str
    text: str
    score: int


class LocalKnowledgeBase:
    def __init__(
        self,
        root: Path,
        *,
        max_snippets: int = 4,
        max_chars_per_snippet: int = 700,
        chunk_chars: int = 1000,
        chunk_overlap: int = 120,
    ) -> None:
        self._root = root
        self._max_snippets = max_snippets
        self._max_chars_per_snippet = max_chars_per_snippet
        self._chunk_chars = chunk_chars
        self._chunk_overlap = chunk_overlap

    def retrieve(self, query: str) -> list[KnowledgeSnippet]:
        if not query.strip():
            return []
        if not self._root.exists():
            return []

        query_tokens = set(_tokenize(query))
        if not query_tokens:
            return []

        scored: list[KnowledgeSnippet] = []
        for path in self._iter_documents():
            text = self._read_document(path)
            if not text.strip():
                continue
            for chunk in self._chunk_document(text):
                score = self._score_chunk(chunk, query_tokens)
                if score <= 0:
                    continue
                scored.append(
                    KnowledgeSnippet(
                        path=str(path),
                        text=chunk[: self._max_chars_per_snippet].strip(),
                        score=score,
                    )
                )

        scored.sort(key=lambda item: (-item.score, len(item.text), item.path))
        return scored[: self._max_snippets]

    @property
    def root(self) -> Path:
        return self._root

    def _iter_documents(self) -> list[Path]:
        paths: list[Path] = []
        for pattern in ("*.md", "*.txt", "*.json"):
            paths.extend(self._root.rglob(pattern))
        return sorted(path for path in paths if path.is_file())

    def _read_document(self, path: Path) -> str:
        if path.suffix.lower() == ".json":
            try:
                parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except json.JSONDecodeError:
                return path.read_text(encoding="utf-8", errors="replace")
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        return path.read_text(encoding="utf-8", errors="replace")

    def _chunk_document(self, text: str) -> list[str]:
        compact = text.replace("\r\n", "\n").strip()
        if len(compact) <= self._chunk_chars:
            return [compact]

        chunks: list[str] = []
        start = 0
        while start < len(compact):
            end = min(len(compact), start + self._chunk_chars)
            chunks.append(compact[start:end])
            if end >= len(compact):
                break
            start = max(start + 1, end - self._chunk_overlap)
        return chunks

    def _score_chunk(self, chunk: str, query_tokens: set[str]) -> int:
        chunk_tokens = set(_tokenize(chunk))
        if not chunk_tokens:
            return 0
        overlap = chunk_tokens & query_tokens
        if not overlap:
            return 0
        return len(overlap)
