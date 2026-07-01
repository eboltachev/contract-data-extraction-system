from app.domain.documents import DocumentFragment
async def rerank(query: str, fragments: list[DocumentFragment]) -> list[DocumentFragment]:
    words=set(query.lower().split())
    return sorted(fragments, key=lambda f: len(words & set(f.text.lower().split())), reverse=True)
