"""Journal metadata and actionable, persisted same-journal paper candidates."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.capacity import CapacityExceeded, busy_response
from app.database import get_db
from app.schemas.journals import (
    JournalDiscoveryRequest, JournalDiscoveryResponse, JournalPapersRequest, JournalPapersResponse,
)
from app.services.journals import NotJournalError, discover_journals, journal_papers, source_id
from app.services.paper_persistence import persist_papers


router = APIRouter()


@router.post("/discover", response_model=JournalDiscoveryResponse)
async def discover(request: JournalDiscoveryRequest, http_request: Request):
    try:
        async with http_request.app.state.search_capacity.reserve():
            return await discover_journals(request)
    except CapacityExceeded:
        return busy_response()


@router.post("/{journal_id}/papers", response_model=JournalPapersResponse)
async def papers(journal_id: str, request: JournalPapersRequest, http_request: Request,
                 db: AsyncSession = Depends(get_db)):
    sid = source_id(journal_id)
    if sid is None:
        raise HTTPException(status_code=422, detail="期刊 Source ID 格式不正确")
    if request.date_from and request.date_to and request.date_from > request.date_to:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
    try:
        async with http_request.app.state.search_capacity.reserve():
            try:
                result = await journal_papers(sid, request)
            except NotJournalError:
                raise HTTPException(status_code=422, detail="该发表源尚未核验为期刊") from None
            if result.papers:
                try:
                    result.papers = await persist_papers(db, result.papers)
                    await db.commit()
                except SQLAlchemyError:
                    await db.rollback()
                    raise HTTPException(status_code=503, detail="论文暂未能保存，请稍后重试。") from None
            return result
    except CapacityExceeded:
        return busy_response()
