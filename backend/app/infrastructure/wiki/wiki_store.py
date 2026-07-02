from __future__ import annotations

import json
from pathlib import Path

from app.domain.wiki import ContractWiki


def write_contract_wiki(wiki: ContractWiki, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)

    for page in wiki.pages:
        (root / page.name).write_text(page.content, encoding="utf-8")

    facts_payload = {
        key: fact.model_dump(mode="json")
        for key, fact in sorted(wiki.facts.items())
    }
    (root / "facts.json").write_text(
        json.dumps(facts_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    source_map = {
        key: [source.model_dump(mode="json") for source in fact.source_refs]
        for key, fact in sorted(wiki.facts.items())
    }
    (root / "source_map.json").write_text(
        json.dumps(source_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
