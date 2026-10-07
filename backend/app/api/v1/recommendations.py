"""
推荐相关 API 端点
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.capacity import CapacityExceeded, busy_response
from app.models.paper import PaperEntity
from app.models.recommendation import Recommendation, RecommendationFeedback
from app.schemas.paper import Paper
from app.schemas.similar_papers import SimilarPapersRequest, SimilarPapersResponse
from app.services.similar_papers import recommend_similar
from app.services.paper_persistence import persist_papers
from app.schemas.search import (
    RecommendationFeedback as RecommendationFeedbackSchema,
    RecommendationItem,
    RecommendationRequest,
    RecommendationResponse,
    SuccessResponse,
)

router = APIRouter()


def _paper_snapshot(paper: PaperEntity) -> Paper:
    return Paper(
        id=paper.id, title=paper.title, abstract=paper.abstract, authors=paper.author_names,
        year=paper.year, venue=paper.venue, doi=paper.doi, url=paper.url,
        pdf_url=paper.pdf_url, source=paper.source, citation_count=paper.citation_count,
        is_open_access=paper.is_open_access,
    )


async def _persist_similar(db: AsyncSession, response: SimilarPapersResponse) -> None:
    # Only expose actionable IDs: paper detail, PDF analysis and knowledge-save use this table.
    papers = await persist_papers(db, [item.paper for item in response.items])
    await db.commit()
    for item, paper in zip(response.items, papers):
        item.paper = paper


@router.post("/similar", response_model=SimilarPapersResponse)
async def get_similar_papers(
    request: SimilarPapersRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
) -> SimilarPapersResponse:
    try:
        async with http_request.app.state.search_capacity.reserve():
            entity = (await db.execute(select(PaperEntity).where(PaperEntity.id == request.paper_id))).scalar_one_or_none()
            if entity is None:
                raise HTTPException(status_code=404, detail="种子论文不存在，请重新打开搜索结果。")
            seed, keywords = _paper_snapshot(entity), list(entity.keywords or [])
            # Do not hold a SQLite read/write transaction while waiting for external sources.
            await db.commit()
            response = await recommend_similar(seed, request.mode, request.limit, keywords=keywords)
            try:
                await _persist_similar(db, response)
            except SQLAlchemyError:
                await db.rollback()
                raise HTTPException(status_code=503, detail="候选论文暂未能保存，请稍后重试。") from None
            return response
    except CapacityExceeded:
        return busy_response()


@router.post("", response_model=RecommendationResponse)
async def get_recommendations(
    request: RecommendationRequest,
    db: AsyncSession = Depends(get_db),
) -> RecommendationResponse:
    """
    获取文献推荐

    基于用户历史和偏好获取个性化文献推荐
    """
    # 查询推荐
    query = select(Recommendation).where(
        Recommendation.is_dismissed == False,
    )

    if request.user_id:
        # 如果有用户 ID，可以关联用户历史
        pass

    query = query.order_by(Recommendation.score.desc()).limit(request.limit)

    result = await db.execute(query)
    recommendations = result.scalars().all()

    return RecommendationResponse(
        recommendations=[
            RecommendationItem(
                id=rec.id,
                paper=Paper(
                    id=rec.paper.id,
                    title=rec.paper.title,
                    authors=rec.paper.author_names,
                    abstract=rec.paper.abstract,
                    year=rec.paper.year,
                    venue=rec.paper.venue,
                    citation_count=rec.paper.citation_count,
                    doi=rec.paper.doi,
                    url=rec.paper.url,
                    pdf_url=rec.paper.pdf_url,
                    source=rec.paper.source,
                    is_open_access=rec.paper.is_open_access,
                ),
                score=rec.score,
                reason=rec.reason,
            )
            for rec in recommendations
        ],
        has_more=len(recommendations) == request.limit,
    )


@router.post("/feedback", response_model=SuccessResponse)
async def submit_feedback(
    feedback: RecommendationFeedbackSchema,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    """
    提交推荐反馈

    用户对推荐结果的反馈，用于改进推荐算法
    """
    # 检查推荐是否存在
    result = await db.execute(
        select(Recommendation).where(Recommendation.id == feedback.recommendation_id)
    )
    recommendation = result.scalar_one_or_none()

    if not recommendation:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    # 创建反馈记录
    new_feedback = RecommendationFeedback(
        id=uuid.uuid4(),
        recommendation_id=feedback.recommendation_id,
        feedback_type=feedback.feedback_type,
        comment=feedback.comment,
    )
    db.add(new_feedback)

    # 如果是忽略反馈，更新推荐状态
    if feedback.feedback_type == "dismissed":
        recommendation.dismiss()

    await db.commit()

    return SuccessResponse(
        success=True,
        message="反馈已记录",
    )
