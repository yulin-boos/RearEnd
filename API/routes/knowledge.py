from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request

from Knowledge.schemas import KnowledgeEntry

router = APIRouter()


@router.get("/api/v1/knowledge", response_model=list[KnowledgeEntry], summary="查询已整理的病虫害知识")
def knowledge_search(request: Request, query: Annotated[str | None, Query(min_length=2, max_length=4000)] = None,
                     limit: Annotated[int, Query(ge=1, le=50)] = 5,
                     crop: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
                     offset: Annotated[int, Query(ge=0)] = 0):
    store = request.app.state.chat.knowledge.store
    if not query or not query.strip():
        return store.recent(limit, offset, crop)
    return store.search(query.strip(), limit, crop, offset)


@router.get("/api/v1/knowledge/{entry_id}", response_model=KnowledgeEntry, summary="查看单条病虫害知识")
def knowledge_entry(request: Request, entry_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]):
    entry = request.app.state.chat.knowledge.store.get(entry_id)
    if entry is None:
        raise HTTPException(404, "知识条目不存在")
    return entry


