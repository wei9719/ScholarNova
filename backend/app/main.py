"""
FastAPI 应用入口

创建和配置 FastAPI 应用实例
"""

from contextlib import asynccontextmanager
import os
import secrets
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.v1.router import api_router
from app.config import runtime_path, settings
from app.core.cache import CacheManager
from app.core.capacity import ExpensiveRequestMiddleware, WorkPool
from app.core.exceptions import ScholarNovaException
from app.core.logging import get_logger, setup_logging
from app.core.rate_limiter import get_client_ip
from app.database import close_db, init_db

logger = get_logger(__name__)

# 全局缓存管理器实例
cache_manager = CacheManager()


# ---------------------------------------------------------------------------
# 安全中间件
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware:
    """
    安全响应头中间件

    为所有响应添加安全相关的 HTTP 头。
    """

    def __init__(self, app: FastAPI):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", {}))

                # 安全头
                headers[b"x-content-type-options"] = b"nosniff"
                headers[b"x-frame-options"] = b"DENY"
                headers[b"x-xss-protection"] = b"1; mode=block"
                headers[b"referrer-policy"] = b"strict-origin-when-cross-origin"

                # 如果是 HTTPS，添加 HSTS
                # 在开发环境中不强制 HTTPS
                if not settings.DEBUG:
                    headers[b"strict-transport-security"] = (
                        b"max-age=31536000; includeSubDomains"
                    )

                # 移除可能泄露信息的头
                headers.pop(b"server", None)
                headers.pop(b"x-powered-by", None)

                message["headers"] = list(headers.items())

            await send(message)

        await self.app(scope, receive, send_with_headers)


class DesktopSessionMiddleware:
    """Only the owning Electron proxy can access its private local backend."""

    def __init__(self, app):
        self.app = app
        self.token = os.environ.get("SCHOLARNOVA_DESKTOP_TOKEN", "").encode("ascii")

    async def __call__(self, scope, receive, send):
        supplied = dict(scope.get("headers", [])).get(b"x-scholarnova-session", b"")
        if not self.token or not secrets.compare_digest(supplied, self.token):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            elif scope["type"] == "http":
                await JSONResponse({"detail": "Desktop session required"}, status_code=403)(scope, receive, send)
            else:
                await self.app(scope, receive, send)
            return
        await self.app(scope, receive, send)


class RequestValidationMiddleware:
    """
    请求验证中间件

    检查请求大小等安全约束。
    """

    def __init__(self, app: FastAPI):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 检查请求体大小
        content_length = 0
        for header_name, header_value in scope.get("headers", []):
            if header_name == b"content-length":
                try:
                    content_length = int(header_value)
                except (ValueError, TypeError):
                    pass

        request_path = scope.get("path", "")
        max_request_size = (
            51 * 1024 * 1024
            if request_path.endswith("/fulltext")
            else settings.MAX_REQUEST_SIZE
        )
        if content_length > max_request_size:
            response = JSONResponse(
                status_code=413,
                content={
                    "detail": f"请求体过大，最大允许 {max_request_size} bytes",
                    "code": "REQUEST_TOO_LARGE",
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    应用生命周期管理

    启动时：初始化数据库、Redis 连接
    关闭时：关闭数据库、Redis 连接
    """
    # 启动时执行
    setup_logging()
    logger.info("Starting ScholarNova API", version=__version__, env=settings.APP_ENV)

    # 初始化数据库
    await init_db()
    logger.info("Database initialized")

    # 测试缓存连接（Redis 或内存）
    try:
        client = await cache_manager._get_client()
        await client.ping()
        if cache_manager._use_memory:
            logger.info("Using in-memory cache (Redis not configured)")
        else:
            logger.info("Redis connected")
    except Exception as e:
        logger.warning("Cache initialization failed", error=str(e))

    yield

    # 关闭时执行
    logger.info("Shutting down ScholarNova API")
    from app.api.v1.search import shutdown_search_tasks
    await shutdown_search_tasks()
    await CacheManager.close_all()
    await close_db()
    logger.info("Cleanup completed")


def create_app() -> FastAPI:
    """
    创建 FastAPI 应用实例

    Returns:
        配置好的 FastAPI 应用
    """
    app = FastAPI(
        title=settings.APP_NAME,
        description="ScholarNova - 智能学术论文搜索与推荐 API",
        version=__version__,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )

    # -----------------------------------------------------------------------
    # 中间件（按注册的逆序执行，先注册的后执行）
    # -----------------------------------------------------------------------

    app.state.search_capacity = WorkPool(
        settings.SEARCH_MAX_ACTIVE, settings.SEARCH_MAX_QUEUED, settings.SEARCH_QUEUE_TIMEOUT,
    )
    app.state.ai_capacity = WorkPool(
        settings.AI_MAX_ACTIVE, settings.AI_MAX_QUEUED, settings.AI_QUEUE_TIMEOUT,
    )
    app.add_middleware(ExpensiveRequestMiddleware, pool=app.state.ai_capacity)

    # 1. 安全响应头
    app.add_middleware(SecurityHeadersMiddleware)

    # 2. 请求验证（大小限制）
    app.add_middleware(RequestValidationMiddleware)

    # 3. CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if settings.APP_ENV == "desktop":
        app.add_middleware(DesktopSessionMiddleware)

    # -----------------------------------------------------------------------
    # 异常处理器
    # -----------------------------------------------------------------------

    @app.exception_handler(ScholarNovaException)
    async def scholar_exception_handler(request: Request, exc: ScholarNovaException) -> ORJSONResponse:
        """处理自定义异常"""
        client_ip = get_client_ip(request)
        logger.error(
            "ScholarAgent exception",
            path=request.url.path,
            code=exc.code,
            message=exc.message,
            client_ip=client_ip,
        )
        return ORJSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> ORJSONResponse:
        """处理未捕获的异常"""
        client_ip = get_client_ip(request)
        logger.error(
            "Unhandled exception",
            path=request.url.path,
            error=str(exc),
            exc_type=type(exc).__name__,
            client_ip=client_ip,
        )
        return ORJSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "code": "INTERNAL_ERROR",
            },
        )

    # 注册 API 路由
    app.include_router(api_router, prefix="/api/v1")
    generated_dir = runtime_path("generated")
    generated_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/generated",
        StaticFiles(directory=str(generated_dir)),
        name="generated",
    )

    return app


# 创建应用实例
app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
