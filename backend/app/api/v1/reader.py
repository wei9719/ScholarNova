"""Authorized PDF page reader and confirmation-only AI assistance."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import check_rate_limit
from app.database import get_db
from app.schemas.reader import (
    ReaderAssistRequest,
    ReaderAssistResponse,
    ReaderDocument,
    ReaderLibraryResponse,
    ReaderPage,
    WritingSamplesRequest,
)
from app.services.reader import (
    MAX_PAGE_TEXT,
    assist_page,
    compare_writing_samples,
    document_snapshot,
    locate_document,
    reader_library,
    reader_url,
)

router = APIRouter()
HashQuery = Query(pattern=r"^[a-f0-9]{64}$")


@router.get("/library", response_model=ReaderLibraryResponse)
async def get_reader_library(db: AsyncSession = Depends(get_db)):
    return await reader_library(db)


@router.post("/samples/writing", response_model=ReaderAssistResponse)
async def create_writing_samples(request: WritingSamplesRequest, http_request: Request,
                                 db: AsyncSession = Depends(get_db)):
    limited = check_rate_limit(http_request, endpoint_type="analysis")
    if limited:
        return limited
    return await compare_writing_samples(db, request)


async def _local_pdf(db: AsyncSession, paper_id: str):
    info, path, source = await locate_document(db, paper_id)
    if path is None:
        raise HTTPException(status_code=404, detail="尚无本地全文，请导入有权使用的 PDF，或先执行全文分析以缓存开放全文")
    return info, path, source


@router.get("/{paper_id}/document", response_model=ReaderDocument)
async def get_document(paper_id: str, db: AsyncSession = Depends(get_db)):
    info, path, source = await locate_document(db, paper_id)
    if path is None:
        return ReaderDocument(paper_id=paper_id, title=info["title"], available=False, warnings=[
            "尚无本地全文。可导入有权使用的 PDF，或先执行全文分析以获取开放全文；打开伴读不会自动下载或调用模型。",
        ])
    snapshot = await document_snapshot(path)
    return ReaderDocument(
        paper_id=paper_id, title=info["title"], available=True, source=source,
        page_count=snapshot["page_count"], content_hash=snapshot["content_hash"],
        pdf_url=reader_url(paper_id, "file", snapshot["content_hash"]),
        warnings=["PDF 页码按文件页序定位，可能不同于论文印刷页码；可提取文字未经 OCR。"],
    )


@router.get("/{paper_id}/file")
async def get_file(paper_id: str, content_hash: str = HashQuery,
                   db: AsyncSession = Depends(get_db)):
    import asyncio

    from app.services.reader import _read_bytes

    _, path, _ = await _local_pdf(db, paper_id)
    content, _ = await asyncio.to_thread(_read_bytes, path, content_hash)
    return Response(content, media_type="application/pdf", headers={
        "Content-Disposition": 'inline; filename="paper.pdf"', "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/{paper_id}/pages/{page_number}", response_model=ReaderPage)
async def get_page(paper_id: str, page_number: int, content_hash: str = HashQuery,
                   db: AsyncSession = Depends(get_db)):
    _, path, _ = await _local_pdf(db, paper_id)
    snapshot = await document_snapshot(path, content_hash, page_number)
    return ReaderPage(
        paper_id=paper_id, page_number=page_number, page_count=snapshot["page_count"],
        content_hash=content_hash, text=snapshot["text"][:MAX_PAGE_TEXT],
        text_truncated=len(snapshot["text"]) > MAX_PAGE_TEXT,
        text_verified=snapshot["text_verified"],
        image_url=reader_url(paper_id, f"pages/{page_number}/image", content_hash),
        coverage="本页可提取文字；未执行 OCR，图表需查看原页图像",
        warnings=[] if snapshot["text_verified"] else ["本页可提取文字不足，可能是扫描页；请结合原页图像阅读。"],
    )


@router.get("/{paper_id}/pages/{page_number}/image")
async def get_page_image(paper_id: str, page_number: int, content_hash: str = HashQuery,
                         db: AsyncSession = Depends(get_db)):
    _, path, _ = await _local_pdf(db, paper_id)
    snapshot = await document_snapshot(path, content_hash, page_number, image=True)
    return Response(snapshot["image"], media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/{paper_id}/assist", response_model=ReaderAssistResponse)
async def create_assist(paper_id: str, request: ReaderAssistRequest, http_request: Request,
                        db: AsyncSession = Depends(get_db)):
    limited = check_rate_limit(http_request, endpoint_type="analysis")
    if limited:
        return limited
    info, path, _ = await _local_pdf(db, paper_id)
    snapshot = await document_snapshot(path, request.content_hash, request.page_number,
                                       image=request.mode in {"figure", "math"})
    return await assist_page(paper_id, info["title"], request, snapshot)
