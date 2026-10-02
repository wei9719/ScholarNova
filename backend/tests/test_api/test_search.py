"""
搜索端点测试
"""

import pytest
from httpx import AsyncClient
from unittest.mock import Mock


@pytest.fixture(autouse=True)
def isolate_search_worker(monkeypatch):
    """Endpoint contract tests must not start a worker against the user's DB/APIs."""
    worker = Mock(side_effect=lambda run_id, request, lease: lease.release())
    monkeypatch.setattr("app.api.v1.search._start_search_task", worker)
    return worker


class TestCreateSearch:
    """POST /api/v1/search 测试套件"""

    async def test_search_normal_query(self, client: AsyncClient, isolate_search_worker):
        """正常查询应返回 200 和 SearchResponse"""
        response = await client.post(
            "/api/v1/search",
            json={"query": "transformer attention mechanism"},
        )
        assert response.status_code == 202
        data = response.json()
        assert "run_id" in data
        assert "status" in data
        assert data["status"] == "pending"
        assert "message" in data
        isolate_search_worker.assert_called_once()
        assert isolate_search_worker.call_args.args[0] == data["run_id"]

    async def test_search_with_all_fields(self, client: AsyncClient):
        """包含所有可选字段的请求应成功"""
        response = await client.post(
            "/api/v1/search",
            json={
                "query": "deep learning for NLP",
                "max_results": 20,
                "sources": ["semantic_scholar", "openalex"],
                "date_from": "2020-01-01",
                "date_to": "2024-12-31",
                "min_citations": 10,
                "open_access_only": True,
            },
        )
        assert response.status_code == 202

    async def test_search_empty_query(self, client: AsyncClient):
        """空查询应返回 422 验证错误"""
        response = await client.post(
            "/api/v1/search",
            json={"query": ""},
        )
        assert response.status_code == 422

    async def test_search_missing_query(self, client: AsyncClient):
        """缺少 query 字段应返回 422"""
        response = await client.post(
            "/api/v1/search",
            json={"max_results": 10},
        )
        assert response.status_code == 422

    async def test_search_very_long_query(self, client: AsyncClient):
        """超长查询（>2000字符）应返回 422"""
        response = await client.post(
            "/api/v1/search",
            json={"query": "a" * 2001},
        )
        assert response.status_code == 422

    async def test_search_max_results_boundary_low(self, client: AsyncClient):
        """max_results < 1 应返回 422"""
        response = await client.post(
            "/api/v1/search",
            json={"query": "test", "max_results": 0},
        )
        assert response.status_code == 422

    async def test_search_max_results_boundary_high(self, client: AsyncClient):
        """max_results > 500 应返回 422"""
        response = await client.post(
            "/api/v1/search",
            json={"query": "test", "max_results": 501},
        )
        assert response.status_code == 422

    async def test_search_invalid_source(self, client: AsyncClient):
        """无效的数据源应返回 422"""
        response = await client.post(
            "/api/v1/search",
            json={"query": "test", "sources": ["invalid_source"]},
        )
        assert response.status_code == 422

    async def test_search_default_max_results(self, client: AsyncClient):
        """不指定 max_results 时应使用默认值 50"""
        response = await client.post(
            "/api/v1/search",
            json={"query": "test query"},
        )
        assert response.status_code == 202

    async def test_search_response_has_run_id_uuid(self, client: AsyncClient):
        """返回的 run_id 应为合法的 UUID 格式"""
        response = await client.post(
            "/api/v1/search",
            json={"query": "test query"},
        )
        data = response.json()
        run_id = data["run_id"]
        # UUID 格式验证
        parts = run_id.split("-")
        assert len(parts) == 5


class TestGetSearchRun:
    """GET /api/v1/search/{run_id} 测试套件"""

    async def test_get_nonexistent_run(self, client: AsyncClient):
        """获取不存在的搜索运行应返回 404"""
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = await client.get(f"/api/v1/search/{fake_id}")
        assert response.status_code == 404

    async def test_get_run_invalid_uuid(self, client: AsyncClient):
        """无效的 UUID 应返回 422"""
        response = await client.get("/api/v1/search/not-a-uuid")
        assert response.status_code == 404
