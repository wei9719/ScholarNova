"""Verify Connector save sessions never claim an unverified collection."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.integrations.zotero import (
    ZoteroClientError,
    ZoteroLocalClient,
    ZoteroWriteUnverifiedError,
)


def saved_item(key="NEW123", collections=None):
    return {"key": key, "data": {"title": "Test paper", "collections": collections or []}}


def connector_selection(library_id=1, targets=None):
    return httpx.Response(
        200,
        json={
            "libraryID": library_id,
            "targets": targets
            or [
                {"id": "L1", "name": "My Library", "level": 0},
                {"id": "C7", "name": "Parent", "level": 1},
                {"id": "C8", "name": "Papers", "level": 2},
                {"id": "C9", "name": "Other", "level": 1},
                {"id": "C10", "name": "Papers", "level": 2},
            ],
        },
    )


def target_client():
    client = ZoteroLocalClient()
    client._connector_post = AsyncMock(return_value=connector_selection())
    client.collections = AsyncMock(
        return_value=[
            {"key": "PARENT1", "name": "Parent", "parent_collection": None},
            {"key": "CHILD1", "name": "Papers", "parent_collection": "PARENT1"},
            {"key": "OTHER1", "name": "Other", "parent_collection": None},
            {"key": "CHILD2", "name": "Papers", "parent_collection": "OTHER1"},
        ]
    )
    return client


@pytest.mark.asyncio
async def test_target_uses_full_path_not_leaf_name():
    client = target_client()
    assert await client._save_target("CHILD2") == "C10"
    assert await client._save_target("CHILD1") == "C8"
    assert await client._save_target(None) == "L1"


@pytest.mark.asyncio
async def test_ambiguous_collection_is_rejected_before_writing():
    client = target_client()
    client.collections.return_value.append(
        {"key": "DUPLICATE", "name": "Papers", "parent_collection": "PARENT1"},
    )
    with pytest.raises(ZoteroClientError, match="同路径同名"):
        await client.create_paper(title="Test paper", collection_key="CHILD1")
    assert [call.args[0] for call in client._connector_post.await_args_list] == [
        "/getSelectedCollection"
    ]


@pytest.mark.asyncio
async def test_group_library_selection_is_rejected_before_writing():
    client = target_client()
    client._connector_post.return_value = connector_selection(library_id=2)
    with pytest.raises(ZoteroClientError, match="不会写入群组"):
        await client.create_paper(title="Test paper")
    assert client._connector_post.await_count == 1


@pytest.mark.asyncio
async def test_invalid_collection_is_rejected_before_connector_call():
    client = target_client()
    with pytest.raises(ValueError, match="不合法"):
        await client.create_paper(title="Test paper", collection_key="../private")
    client._connector_post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("collection_key,target", [("CHILD1", "C8"), (None, "L1")])
async def test_save_moves_exact_session_and_verifies_new_item(collection_key, target):
    client = ZoteroLocalClient()
    client._find_existing_paper = AsyncMock(return_value=None)
    client._save_target = AsyncMock(return_value=target)
    client._connector_post = AsyncMock()
    existing = saved_item("OLD123", [collection_key] if collection_key else [])
    new = saved_item(collections=[collection_key] if collection_key else [])
    client.search_items = AsyncMock(side_effect=[[existing], [existing, new]])

    result = await client.create_paper(title="Test paper", collection_key=collection_key)

    save, move = client._connector_post.await_args_list
    assert save.args[0] == "/saveItems"
    assert save.args[2] == 201
    assert move.args[0] == "/updateSession"
    assert move.args[1]["sessionID"] == save.args[1]["sessionID"]
    assert move.args[1]["target"] == target
    assert "collections" not in save.args[1]["items"][0]
    assert result["item_key"] == "NEW123"
    assert result["collection_verified"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "after", [[saved_item("OLD123", ["CHILD1"])], [saved_item(collections=["WRONG"])], []]
)
async def test_old_item_or_wrong_target_never_reports_success(after):
    client = ZoteroLocalClient()
    client._find_existing_paper = AsyncMock(return_value=None)
    client._save_target = AsyncMock(return_value="C8")
    client._connector_post = AsyncMock()
    client.search_items = AsyncMock(side_effect=[[saved_item("OLD123", ["CHILD1"])], after])
    with pytest.raises(ZoteroWriteUnverifiedError, match="避免重复"):
        await client.create_paper(title="Test paper", collection_key="CHILD1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [ZoteroClientError("HTTP 500"), httpx.RemoteProtocolError("Disconnected")]
)
async def test_failed_session_move_reports_possible_partial_write(failure):
    client = ZoteroLocalClient()
    client._find_existing_paper = AsyncMock(return_value=None)
    client._save_target = AsyncMock(return_value="C8")
    client.search_items = AsyncMock(return_value=[])
    client._connector_post = AsyncMock(side_effect=[None, failure])
    with pytest.raises(ZoteroWriteUnverifiedError, match="可能已经写入"):
        await client.create_paper(title="Test paper", collection_key="CHILD1")


@pytest.mark.asyncio
async def test_pdf_request_preserves_metadata_save_and_reports_warning():
    client = ZoteroLocalClient()
    client._find_existing_paper = AsyncMock(return_value=None)
    client._save_target = AsyncMock(return_value="L1")
    client._connector_post = AsyncMock()
    client.search_items = AsyncMock(side_effect=[[], [saved_item()]])
    result = await client.create_paper(title="Test paper", pdf_path="paper.pdf")
    assert result["item_key"] == "NEW123"
    assert result["pdf_imported"] is False
    assert "PDF 未导入" in result["warnings"][0]
    assert client._connector_post.await_count == 2


@pytest.mark.asyncio
async def test_repeat_save_reuses_only_target_collection_without_connector_write():
    client = ZoteroLocalClient()
    client._save_target = AsyncMock(return_value="C8")
    client._connector_post = AsyncMock()
    client._get = AsyncMock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "key": "OTHER1",
                    "data": {
                        "title": "Test paper",
                        "DOI": "10.1234/example",
                        "collections": ["OTHER"],
                    },
                },
                {
                    "key": "SAME1",
                    "data": {
                        "title": "Translated title",
                        "DOI": "https://doi.org/10.1234/EXAMPLE",
                        "collections": ["CHILD1"],
                    },
                },
            ],
        )
    )
    result = await client.create_paper(
        title="Test paper", doi="doi:10.1234/example", collection_key="CHILD1"
    )
    assert result["item_key"] == "SAME1"
    assert result["deduplicated"] is True
    assert result["collection_verified"] is True
    client._connector_post.assert_not_called()
    assert client._get.await_args.args[0] == "/users/0/collections/CHILD1/items/top"


@pytest.mark.asyncio
async def test_title_fallback_normalises_punctuation_without_merging_conflicting_doi():
    client = ZoteroLocalClient()
    client._get = AsyncMock(
        return_value=httpx.Response(
            200,
            json=[
                {"key": "MATCH1", "data": {"title": "TEST: Paper", "date": "2024-06-01",
                                          "creators": [{"firstName": "Jane", "lastName": "Smith"}],
                                          "collections": ["CHILD1"]}},
            ],
        )
    )
    item = {"title": "Test paper", "date": "2024", "creators": [{"firstName": "Jane", "lastName": "Smith"}]}
    assert (await client._find_existing_paper(item, "CHILD1"))["key"] == "MATCH1"
    client._get.return_value = httpx.Response(
        200,
        json=[
            {
                "key": "WRONG1",
                "data": {
                    "title": "Test paper",
                    "DOI": "10.1234/reprint",
                    "collections": ["CHILD1"],
                },
            },
        ],
    )
    assert (
        await client._find_existing_paper(
            {"title": "Test paper", "DOI": "10.1234/original"}, "CHILD1"
        )
        is None
    )


@pytest.mark.asyncio
async def test_root_destination_does_not_reuse_an_item_from_other_collection():
    client = ZoteroLocalClient()
    client._get = AsyncMock(
        return_value=httpx.Response(
            200,
            json=[
                {"key": "OTHER1", "data": {"title": "Test paper", "collections": ["OTHER"]}},
            ],
        )
    )
    assert await client._find_existing_paper({"title": "Test paper"}, None) is None


@pytest.mark.asyncio
async def test_invalid_dedup_response_fails_before_save():
    client = ZoteroLocalClient()
    client._save_target = AsyncMock(return_value="C8")
    client._connector_post = AsyncMock()
    client._get = AsyncMock(return_value=httpx.Response(200, json={"unexpected": []}))
    with pytest.raises(ZoteroClientError, match="查重数据格式"):
        await client.create_paper(title="Test paper", collection_key="CHILD1")
    client._connector_post.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_clients_do_not_create_duplicate_items():
    state = {"item": None}
    writes = []

    async def find_existing(*_args):
        return state["item"]

    async def search_items(*_args, **_kwargs):
        return [state["item"]] if state["item"] else []

    async def connector_post(path, *_args):
        writes.append(path)
        await asyncio.sleep(0.01)
        if path == "/updateSession":
            state["item"] = saved_item(collections=["CHILD1"])

    clients = [ZoteroLocalClient(), ZoteroLocalClient()]
    for client in clients:
        client._save_target = AsyncMock(return_value="C8")
        client._find_existing_paper = find_existing
        client.search_items = search_items
        client._connector_post = connector_post
    results = await asyncio.gather(
        *(client.create_paper(title="Test paper", collection_key="CHILD1") for client in clients)
    )
    assert writes == ["/saveItems", "/updateSession"]
    assert sorted(result["deduplicated"] for result in results) == [False, True]


def metadata_item(key="SAME1", **updates):
    data = {"title": "Test paper", "date": "2024", "collections": ["CHILD1"],
            "creators": [{"creatorType": "author", "firstName": "Jane", "lastName": "Smith"}]}
    data.update(updates)
    return {"key": key, "data": data}


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [
    {"date": "2025"},
    {"creators": [{"firstName": "John", "lastName": "Jones"}]},
    {"date": "2025", "creators": [{"firstName": "John", "lastName": "Jones"}]},
])
async def test_no_doi_same_title_different_year_or_authors_is_not_reused(updates):
    client = ZoteroLocalClient()
    client._get = AsyncMock(return_value=httpx.Response(200, json=[metadata_item(**updates)]))
    assert await client._find_existing_paper(metadata_item()["data"], "CHILD1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("wanted_updates,existing_updates", [
    ({}, {"date": ""}),
    ({"date": ""}, {}),
    ({}, {"creators": None}),
    ({"creators": []}, {}),
    ({"date": "", "creators": []}, {"date": "", "creators": []}),
    ({"DOI": "10.1234/example", "date": ""}, {}),
    ({}, {"DOI": "10.1234/example", "creators": []}),
])
async def test_insufficient_title_identity_requires_manual_check_before_writing(wanted_updates, existing_updates):
    client = ZoteroLocalClient()
    client._save_target = AsyncMock(return_value="C8")
    client._connector_post = AsyncMock()
    client._get = AsyncMock(return_value=httpx.Response(200, json=[metadata_item(**existing_updates)]))
    wanted = metadata_item(**wanted_updates)["data"]
    with pytest.raises(ZoteroClientError, match="先检查 Zotero"):
        await client._find_existing_paper(wanted, "CHILD1")
    client._connector_post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("doi_side", ["wanted", "existing"])
async def test_one_sided_doi_reuse_requires_confirmed_year_and_author_identity(doi_side):
    client = ZoteroLocalClient()
    wanted = metadata_item()["data"]
    existing = metadata_item()
    (wanted if doi_side == "wanted" else existing["data"])["DOI"] = "10.1234/example"
    client._get = AsyncMock(return_value=httpx.Response(200, json=[existing]))
    assert (await client._find_existing_paper(wanted, "CHILD1"))["key"] == "SAME1"


@pytest.mark.asyncio
@pytest.mark.parametrize("doi", [None, "10.1234/example"])
async def test_multiple_possible_identical_items_are_not_silently_chosen(doi):
    client = ZoteroLocalClient()
    updates = {"DOI": doi} if doi else {}
    client._get = AsyncMock(return_value=httpx.Response(200, json=[
        metadata_item("ONE", **updates), metadata_item("TWO", **updates),
    ]))
    with pytest.raises(ZoteroClientError, match="先检查 Zotero"):
        await client._find_existing_paper(metadata_item(**updates)["data"], "CHILD1")


@pytest.mark.asyncio
async def test_different_explicit_doi_allows_a_separate_new_item():
    client = ZoteroLocalClient()
    client._save_target = AsyncMock(return_value="C8")
    client._connector_post = AsyncMock()
    existing = metadata_item("OLD", DOI="10.1234/original")
    new = metadata_item("NEW", DOI="10.1234/reprint")
    client._get = AsyncMock(return_value=httpx.Response(200, json=[existing]))
    client.search_items = AsyncMock(side_effect=[[existing], [existing, new]])
    result = await client.create_paper(title="Test paper", doi="10.1234/reprint", collection_key="CHILD1")
    assert result["deduplicated"] is False and result["item_key"] == "NEW"
    assert [call.args[0] for call in client._connector_post.await_args_list] == ["/saveItems", "/updateSession"]


@pytest.mark.asyncio
async def test_concurrent_requests_serialize_target_discovery_as_well_as_save(monkeypatch):
    # pytest gives each async test its own loop; the application uses one loop.
    monkeypatch.setattr("app.services.integrations.zotero._WRITE_LOCK", asyncio.Lock())
    active = 0
    maximum = 0

    async def target(*_args):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "C8"

    clients = [ZoteroLocalClient() for _ in range(6)]
    for client in clients:
        client._save_target = target
        client._save_paper = AsyncMock(return_value={"deduplicated": True})
    await asyncio.gather(*(client.create_paper(title="Test paper", collection_key="CHILD1") for client in clients))
    assert maximum == 1
