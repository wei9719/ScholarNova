"""
测试配置

提供通用的测试 fixtures，使用 SQLite 内存数据库实现测试隔离。
所有外部服务（LLM、学术数据源、Redis）通过 Mock 替代。
"""

import asyncio
import uuid
from datetime import datetime
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db


@pytest.fixture(autouse=True)
def isolate_runtime_files(tmp_path, monkeypatch):
    """Prevent API tests from overwriting a developer's local model settings."""
    from app.config import settings

    monkeypatch.setattr(settings, "RUNTIME_DIR", str(tmp_path))


# ---------------------------------------------------------------------------
# 事件循环
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """创建会话级别的事件循环"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# SQLite 内存数据库
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

def set_sqlite_pragma(dbapi_connection, connection_record):
    """SQLite 连接时启用外键约束"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest_asyncio.fixture
async def test_engine():
    """每例独立数据库，允许被测 API 提交而不污染后续测试。"""
    # Ensure every ORM model is registered on Base.metadata before create_all.
    # Importing Base alone leaves metadata empty when an API test runs first.
    import app.models  # noqa: F401

    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """创建测试数据库会话，清理未提交事务；引擎按测试隔离。"""
    session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_factory() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# FastAPI TestClient
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(db_session) -> AsyncGenerator[AsyncClient, None]:
    """创建异步测试客户端，注入测试数据库会话"""

    # 延迟导入，确保 app 在 fixture 运行前可用
    from app.main import create_app

    test_app = create_app()

    async def override_get_db():
        yield db_session

    test_app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac

    test_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Mock LLM 网关
# ---------------------------------------------------------------------------

class MockLLMGateway:
    """模拟 LLM 网关，返回预设响应"""

    def __init__(self, response: str = '{"intent":"literature_search","keywords":["test"],"sub_queries":[],"strategy":"mock"}'):
        self.response = response
        self.call_count = 0
        self.last_messages = None

    async def chat(self, messages, model=None, temperature=0.7, max_tokens=4096, **kwargs) -> str:
        self.call_count += 1
        self.last_messages = messages
        return self.response

    async def test_connection(self) -> dict:
        return {"success": True, "model": "mock", "response": "hello"}


@pytest.fixture
def mock_llm_gateway() -> MockLLMGateway:
    """提供 Mock LLM 网关"""
    return MockLLMGateway()


# ---------------------------------------------------------------------------
# Mock 数据源
# ---------------------------------------------------------------------------

class MockSource:
    """模拟学术数据源"""

    def __init__(self, papers=None, name="mock_source"):
        self._papers = papers or []
        self.name = name
        self.search_call_count = 0

    async def search(self, query: str, max_results: int = 50):
        self.search_call_count += 1
        return self._papers[:max_results]

    async def get_paper(self, paper_id: str):
        for p in self._papers:
            if str(p.id) == paper_id:
                return p
        return None

    async def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def mock_source() -> MockSource:
    """提供空的 Mock 数据源"""
    return MockSource()


# ---------------------------------------------------------------------------
# 示例论文数据
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_paper_data() -> dict:
    """单篇论文样本数据"""
    return {
        "id": str(uuid.uuid4()),
        "title": "Attention Is All You Need",
        "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"],
        "abstract": "The dominant sequence transduction models are based on complex recurrent...",
        "year": 2017,
        "venue": "NeurIPS",
        "citation_count": 120000,
        "doi": "10.48550/arXiv.1706.03762",
        "url": "https://arxiv.org/abs/1706.03762",
        "pdf_url": "https://arxiv.org/pdf/1706.03762",
        "source": "semantic_scholar",
        "is_open_access": True,
    }


@pytest.fixture
def sample_papers_batch(sample_paper_data) -> list[dict]:
    """多篇论文样本数据"""
    papers = [sample_paper_data]
    topics = [
        ("BERT: Pre-training of Deep Bidirectional Transformers", 2019, "NAACL", 80000),
        ("Deep Residual Learning for Image Recognition", 2016, "CVPR", 180000),
        ("Generative Adversarial Nets", 2014, "NeurIPS", 60000),
        ("ImageNet Classification with Deep Convolutional Neural Networks", 2012, "NeurIPS", 100000),
        ("Dropout: A Simple Way to Prevent Overfitting", 2014, "JMLR", 40000),
        ("Batch Normalization", 2015, "ICML", 50000),
        ("Adam: A Method for Stochastic Optimization", 2015, "ICLR", 90000),
        ("Long Short-Term Memory", 1997, "Neural Computation", 70000),
        ("GPT-4 Technical Report", 2023, "OpenAI", 5000),
    ]
    for title, year, venue, citations in topics:
        papers.append({
            "id": str(uuid.uuid4()),
            "title": title,
            "authors": ["Author A", "Author B"],
            "abstract": f"Abstract for {title}",
            "year": year,
            "venue": venue,
            "citation_count": citations,
            "doi": f"10.{uuid.uuid4().hex[:8]}",
            "url": f"https://example.com/{uuid.uuid4().hex[:8]}",
            "pdf_url": None,
            "source": "semantic_scholar",
            "is_open_access": False,
        })
    return papers
