"""Local-evidence study drafts; user confirmation is required to save elsewhere."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import check_rate_limit
from app.database import get_db
from app.schemas.study import StudyDraftRequest, StudyDraftResponse
from app.services.study import draft_study_section, load_study_material

router = APIRouter()


@router.post("/draft", response_model=StudyDraftResponse)
async def create_study_draft(
    request: StudyDraftRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
) -> StudyDraftResponse:
    limited = check_rate_limit(http_request, endpoint_type="analysis")
    if limited:
        return limited
    material = await load_study_material(db, request)
    if material is None:
        raise HTTPException(status_code=404, detail="本地论文记录不存在")
    if not material.sources:
        raise HTTPException(
            status_code=422,
            detail="本地没有可读摘要或已解析论文片段，无法生成有依据的草稿；仍可离线手写笔记。",
        )
    return await draft_study_section(request, material)
