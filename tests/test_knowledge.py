from pathlib import Path

from private_agent.knowledge import LocalKnowledgeBase


def test_local_knowledge_base_retrieves_relevant_snippets(tmp_path: Path) -> None:
    kb_dir = tmp_path / "knowledge"
    kb_dir.mkdir()
    (kb_dir / "stockholm.md").write_text(
        "Central Stockholm neighborhoods include Vasastan, Norrmalm, and Ostermalm.",
        encoding="utf-8",
    )
    (kb_dir / "other.md").write_text(
        "This document talks about Python packaging and virtual environments.",
        encoding="utf-8",
    )

    kb = LocalKnowledgeBase(kb_dir, max_snippets=2)
    results = kb.retrieve("central stockholm vasastan housing")

    assert results
    assert results[0].path.endswith("stockholm.md")
    assert "Vasastan" in results[0].text
