"""
API v1 路由注册
"""

from fastapi import APIRouter

from app.api.v1 import (
    agent,
    analysis,
    evidence,
    health,
    integrations,
    knowledge,
    model_config,
    network,
    papers,
    recommendations,
    search,
    study,
    journals,
    reader,
)

# 创建 API 路由器
api_router = APIRouter()
api_router.include_router(journals.router, prefix="/journals", tags=["journals"])
api_router.include_router(reader.router, prefix="/reader", tags=["reader"])

api_router.include_router(
    agent.router,
    prefix="/agent",
    tags=["agent"],
)

# 注册各模块路由
api_router.include_router(
    search.router,
    prefix="/search",
    tags=["search"],
)

api_router.include_router(
    papers.router,
    prefix="/papers",
    tags=["papers"],
)

api_router.include_router(
    analysis.router,
    prefix="/papers",
    tags=["analysis"],
)

api_router.include_router(
    study.router,
    prefix="/study",
    tags=["study"],
)

api_router.include_router(
    evidence.router,
    prefix="/evidence",
    tags=["evidence"],
)

api_router.include_router(
    recommendations.router,
    prefix="/recommendations",
    tags=["recommendations"],
)

api_router.include_router(
    model_config.router,
    prefix="/model",
    tags=["model"],
)

api_router.include_router(
    health.router,
    tags=["health"],
)

api_router.include_router(
    knowledge.router,
    prefix="/knowledge",
    tags=["knowledge"],
)

api_router.include_router(
    network.router,
    prefix="/network",
    tags=["network"],
)

api_router.include_router(
    integrations.router,
    prefix="/integrations",
    tags=["integrations"],
)
