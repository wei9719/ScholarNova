"""Local Zotero reads and explicitly requested, verified Connector writes."""

from __future__ import annotations

import asyncio
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

ZOTERO_LOCAL_API = "http://127.0.0.1:23119/api"
ZOTERO_CONNECTOR_API = "http://127.0.0.1:23119/connector"
ZOTERO_API_VERSION = "3"
_COLLECTION_KEY = re.compile(r"^[A-Za-z0-9]+$")
_WRITE_LOCK = asyncio.Lock()


def _doi_key(value: Any) -> str:
    return re.sub(
        r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
        "",
        str(value or "").strip(),
        flags=re.IGNORECASE,
    ).casefold()


def _title_key(value: Any) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKC", str(value or "")).casefold() if c.isalnum()
    )


def _paper_metadata_identity(wanted: dict[str, Any], existing: dict[str, Any]) -> bool | None:
    """Without two matching DOIs, require a verifiable year and author set."""
    years = [re.search(r"\b(?:18|19|20|21)\d{2}\b", str(data.get("date") or ""))
             for data in (wanted, existing)]
    authors = [frozenset(
        _title_key(creator.get("name") or f"{creator.get('firstName') or ''} {creator.get('lastName') or ''}")
        for creator in (data.get("creators") or [])
        if isinstance(creator, dict) and creator.get("creatorType", "author") == "author"
        and (creator.get("name") or creator.get("firstName") or creator.get("lastName"))
    ) for data in (wanted, existing)]
    if (all(years) and years[0].group() != years[1].group()) or (all(authors) and authors[0] != authors[1]):
        return False
    return True if all(years) and all(authors) else None


class ZoteroUnavailableError(RuntimeError):
    """Raised when Zotero is not running or its local API is disabled."""


class ZoteroAccessDeniedError(RuntimeError):
    """Raised when Zotero is running but its local API is disabled."""


class ZoteroClientError(RuntimeError):
    """Raised when Zotero returns an invalid or unsuccessful response."""


class ZoteroWriteUnverifiedError(ZoteroClientError):
    """A write was attempted, but its final destination could not be verified."""


@dataclass(slots=True)
class ZoteroStatus:
    connected: bool
    server_id: str | None = None
    zotero_version: str | None = None


class ZoteroLocalClient:
    """A small wrapper around fixed localhost Web API and Connector endpoints."""

    def __init__(self, timeout: float = 4.0) -> None:
        self.timeout = timeout

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                base_url=ZOTERO_LOCAL_API,
                timeout=self.timeout,
                trust_env=False,
                headers={"Zotero-API-Version": ZOTERO_API_VERSION},
            ) as client:
                response = await client.get(path, params=params)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise ZoteroUnavailableError(
                "未检测到 Zotero。请先启动 Zotero，并在设置 → 高级中启用本地 API。"
            ) from exc

        if response.status_code in {401, 403}:
            raise ZoteroAccessDeniedError(
                "Zotero 已运行，但拒绝了本地 API 访问。请检查 Zotero 的本地 API 设置。"
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ZoteroClientError(f"Zotero 本地 API 返回 HTTP {response.status_code}") from exc
        return response

    async def _post(
        self,
        path: str,
        payload: list[dict[str, Any]],
        *,
        api_key: str | None = None,
    ) -> httpx.Response:
        headers = {"Zotero-API-Version": ZOTERO_API_VERSION}
        if api_key:
            headers["Zotero-API-Key"] = api_key
        try:
            async with httpx.AsyncClient(
                base_url=ZOTERO_LOCAL_API,
                timeout=10.0,
                trust_env=False,
                headers=headers,
            ) as client:
                response = await client.post(path, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise ZoteroUnavailableError(
                "未检测到 Zotero。请先启动 Zotero，并在设置 → 高级中启用本地 API。"
            ) from exc
        if response.status_code in {401, 403}:
            raise ZoteroAccessDeniedError(
                "Zotero 拒绝了写入请求。请在 Zotero 设置 → 高级 → 允许其他应用程序"
                "与 Zotero 通信（并开启写入），或提供本地 API 密钥。"
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ZoteroClientError(
                f"Zotero 本地 API 返回 HTTP {response.status_code}: {response.text[:160]}"
            ) from exc
        return response

    async def _connector_post(
        self,
        path: str,
        payload: dict[str, Any],
        expected_status: int = 200,
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                base_url=ZOTERO_CONNECTOR_API,
                timeout=10.0,
                trust_env=False,
            ) as client:
                response = await client.post(path, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise ZoteroUnavailableError(
                "未检测到 Zotero。请先启动 Zotero，并在设置 → 高级中启用本地 API。"
            ) from exc
        if response.status_code in {401, 403}:
            raise ZoteroAccessDeniedError(
                "Zotero 拒绝了写入请求。请在 Zotero 设置 → 高级中启用"
                "“允许此计算机上的其他应用程序与 Zotero 通讯”。"
            )
        if response.status_code != expected_status:
            raise ZoteroClientError(
                f"Zotero Connector 写入失败: HTTP {response.status_code} {response.text[:160]}"
            )
        return response

    async def _save_target(self, collection_key: str | None) -> str:
        """Resolve a unique full collection path, never a guessed name or ID."""
        if collection_key and not _COLLECTION_KEY.fullmatch(collection_key):
            raise ValueError("Zotero 文件夹标识不合法")
        response = await self._connector_post("/getSelectedCollection", {})
        try:
            selected = response.json()
            targets = selected["targets"]
            # Zotero Libraries.getAll() orders My Library before group libraries.
            root = targets[0]
            root_id = root["id"]
            if root.get("level") != 0 or not re.fullmatch(r"L\d+", root_id):
                raise ValueError
            if selected.get("libraryID") != int(root_id[1:]):
                raise ZoteroClientError("请先在 Zotero 中选择个人文库，再同步；不会写入群组文库。")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ZoteroClientError("无法确认 Zotero 个人文库，未写入任何条目。") from exc
        if not collection_key:
            return root_id

        collections = await self.collections()
        by_key = {item["key"]: item for item in collections}

        def collection_path(key: str) -> tuple[str, ...]:
            names: list[str] = []
            seen: set[str] = set()
            while key:
                if key in seen or key not in by_key:
                    raise ZoteroClientError("无法确认 Zotero 文件夹层级，未写入任何条目。")
                seen.add(key)
                item = by_key[key]
                names.append(item["name"])
                key = item.get("parent_collection")
            return tuple(reversed(names))

        wanted = collection_path(collection_key)
        if sum(collection_path(item["key"]) == wanted for item in collections) != 1:
            raise ZoteroClientError(
                "Zotero 中存在同路径同名文件夹，请先重命名后同步；未写入任何条目。"
            )
        path: list[str] = []
        matches: list[str] = []
        for target in targets[1:]:
            level = target.get("level")
            if level == 0:
                break  # Do not match collections from a group library.
            if not isinstance(level, int) or level < 1 or level > len(path) + 1:
                raise ZoteroClientError("Zotero 文件夹树无法解析，未写入任何条目。")
            path = path[: level - 1] + [target.get("name", "")]
            if tuple(path) == wanted and re.fullmatch(r"C\d+", target.get("id", "")):
                matches.append(target["id"])
        if len(matches) != 1:
            raise ZoteroClientError("无法唯一确认所选 Zotero 文件夹，未写入任何条目。")
        return matches[0]

    async def status(self) -> ZoteroStatus:
        response = await self._get(
            "/users/0/items/top",
            params={"limit": 1, "format": "json"},
        )
        try:
            response.json()
        except ValueError as exc:
            raise ZoteroClientError("Zotero 本地 API 返回了无效数据") from exc
        return ZoteroStatus(
            connected=True,
            server_id=response.headers.get("Zotero-Server-ID"),
            zotero_version=(
                response.headers.get("Zotero-Version") or response.headers.get("X-Zotero-Version")
            ),
        )

    async def collections(self) -> list[dict[str, Any]]:
        response = await self._get(
            "/users/0/collections",
            params={
                "limit": 100,
                "sort": "title",
                "direction": "asc",
                "format": "json",
            },
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ZoteroClientError("Zotero 文献库返回了无效数据") from exc
        if not isinstance(payload, list):
            raise ZoteroClientError("Zotero 文献库数据格式不正确")

        collections: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            key = str(item.get("key") or data.get("key") or "").strip()
            name = str(data.get("name") or "").strip()
            if key and name:
                collections.append(
                    {
                        "key": key,
                        "name": name,
                        "parent_collection": data.get("parentCollection") or None,
                        "version": item.get("version") or data.get("version"),
                    }
                )
        return collections

    async def items(
        self,
        *,
        collection_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if collection_key and not _COLLECTION_KEY.fullmatch(collection_key):
            raise ValueError("Zotero 文件夹标识不合法")
        safe_limit = min(max(limit, 1), 100)
        path = "/users/0/items/top"
        if collection_key:
            path = f"/users/0/collections/{collection_key}/items/top"

        response = await self._get(
            path,
            params={
                "limit": safe_limit,
                "sort": "dateModified",
                "direction": "desc",
                "format": "json",
            },
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ZoteroClientError("Zotero 文献数据无法解析") from exc
        if not isinstance(payload, list):
            raise ZoteroClientError("Zotero 文献数据格式不正确")

        excluded = {"attachment", "note", "annotation"}
        return [
            item
            for item in payload
            if isinstance(item, dict)
            and isinstance(item.get("data"), dict)
            and item["data"].get("itemType") not in excluded
        ]

    async def search_items(
        self,
        query: str,
        *,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        """Search top-level bibliographic items in the live local library."""
        clean_query = query.strip()
        if not clean_query:
            return []
        response = await self._get(
            "/users/0/items/top",
            params={
                "q": clean_query[:300],
                "qmode": "everything",
                "limit": min(max(limit, 1), 20),
                "sort": "dateModified",
                "direction": "desc",
                "format": "json",
            },
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ZoteroClientError("Zotero 搜索结果无法解析") from exc
        if not isinstance(payload, list):
            raise ZoteroClientError("Zotero 搜索结果格式不正确")
        excluded = {"attachment", "note", "annotation"}
        return [
            item
            for item in payload
            if isinstance(item, dict)
            and isinstance(item.get("data"), dict)
            and item["data"].get("itemType") not in excluded
        ]

    async def create_paper(
        self,
        *,
        title: str,
        creators: list[dict[str, Any]] | None = None,
        year: str | None = None,
        venue: str | None = None,
        doi: str | None = None,
        url: str | None = None,
        abstract: str | None = None,
        collection_key: str | None = None,
        pdf_path: str | None = None,
        api_key: str | None = None,  # noqa: ARG002 - legacy parameter; local Connector uses no API key
    ) -> dict[str, Any]:
        """Save metadata, move this Connector session, and verify its collection."""
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("标题不能为空")

        item: dict[str, Any] = {
            "itemType": "journalArticle",
            "title": clean_title[:500],
            "creators": [
                {
                    "creatorType": "author",
                    "firstName": (c.get("firstName") or "").strip()[:100],
                    "lastName": (c.get("lastName") or "").strip()[:100],
                }
                for c in (creators or [])
                if (c.get("firstName") or "").strip() or (c.get("lastName") or "").strip()
            ],
            "tags": [],
        }
        if year:
            item["date"] = str(year).strip()[:16]
        if venue:
            item["publicationTitle"] = venue.strip()[:255]
        if doi:
            item["DOI"] = doi.strip()[:255]
        if url:
            item["url"] = url.strip()[:1000]
        if abstract:
            item["abstractNote"] = abstract.strip()[:5000]
        async with _WRITE_LOCK:
            # Connector target discovery also touches the single-user Zotero UI;
            # keep preparation inside the same write lock to avoid probe bursts.
            target = await self._save_target(collection_key)
            return await self._save_paper(item, collection_key, target, pdf_path)

    async def _find_existing_paper(
        self,
        item: dict[str, Any],
        collection_key: str | None,
    ) -> dict[str, Any] | None:
        """Reuse only an identical paper already in the requested destination."""
        path = (
            f"/users/0/collections/{collection_key}/items/top"
            if collection_key
            else "/users/0/items/top"
        )
        wanted_doi = _doi_key(item.get("DOI"))
        queries = list(dict.fromkeys(q for q in [wanted_doi, item["title"]] if q))
        same_doi: dict[str, dict[str, Any]] = {}
        title_candidates: dict[str, dict[str, Any]] = {}
        for query in queries:
            response = await self._get(
                path, params={"q": query, "qmode": "everything", "limit": 100, "format": "json"}
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ZoteroClientError("Zotero 查重返回了无效数据，未写入条目") from exc
            if not isinstance(payload, list):
                raise ZoteroClientError("Zotero 查重数据格式不正确，未写入条目")
            for existing in payload:
                if not isinstance(existing, dict) or not isinstance(existing.get("data"), dict):
                    continue
                data = existing["data"]
                collections = data.get("collections", [])
                in_target = collection_key in collections if collection_key else not collections
                if not in_target or data.get("itemType") in {"attachment", "note", "annotation"}:
                    continue
                key = str(existing.get("key") or data.get("key") or "")
                existing_doi = _doi_key(data.get("DOI"))
                if wanted_doi and existing_doi:
                    if existing_doi == wanted_doi:
                        same_doi[key] = existing
                    # Different explicit DOIs must not fall back to title.
                    continue
                if _title_key(data.get("title")) == _title_key(item["title"]):
                    title_candidates[key] = existing
        if same_doi:
            if len(same_doi) != 1 or "" in same_doi:
                raise ZoteroClientError("目标文件夹中有多个相同 DOI 或条目标识不完整，请先检查 Zotero；未新增或合并文献。")
            return next(iter(same_doi.values()))
        confirmed = []
        uncertain = False
        for key, candidate in title_candidates.items():
            identity = _paper_metadata_identity(item, candidate["data"])
            if identity is True and key:
                confirmed.append(candidate)
            elif identity is None or not key:
                uncertain = True
        if uncertain or len(confirmed) > 1:
            raise ZoteroClientError("目标文件夹有同题名文献，但年份/作者信息不足或存在多个可能相同的条目，请先检查 Zotero；未新增或合并文献。")
        return confirmed[0] if confirmed else None

    async def _save_paper(
        self,
        item: dict[str, Any],
        collection_key: str | None,
        target: str,
        pdf_path: str | None,
    ) -> dict[str, Any]:
        existing = await self._find_existing_paper(item, collection_key)
        if existing:
            return {
                "item_key": str(existing.get("key") or existing["data"]["key"]),
                "attachment_key": "",
                "collection_key": collection_key,
                "collection_verified": True,
                "pdf_imported": False,
                "deduplicated": True,
                "warnings": ["此论文已在目标文件夹中，已复用原条目；PDF 未导入。"]
                if pdf_path
                else [],
            }
        previous_keys = {
            entry.get("key") or entry["data"].get("key")
            for entry in await self.search_items(item["title"], limit=20)
        }
        session_id = str(uuid.uuid4())
        item["id"] = session_id
        try:
            await self._connector_post(
                "/saveItems",
                {"sessionID": session_id, "items": [item]},
                201,
            )
            # saveItems uses the Zotero UI selection, not item.collections.
            # updateSession changes only items created in this exact session.
            await self._connector_post(
                "/updateSession",
                {"sessionID": session_id, "target": target, "tags": []},
            )
            new_items = [
                entry
                for entry in await self.search_items(item["title"], limit=20)
                if (entry.get("key") or entry["data"].get("key")) not in previous_keys
                and entry["data"].get("title", "").strip() == item["title"]
                and (
                    not item.get("DOI")
                    or _doi_key(entry["data"].get("DOI")) == _doi_key(item["DOI"])
                )
            ]
            expected_collections = [collection_key] if collection_key else []
            if (
                len(new_items) != 1
                or new_items[0]["data"].get("collections", []) != expected_collections
            ):
                raise ZoteroClientError("新增条目或目标文件夹未通过回读确认")
            parent_key = str(new_items[0].get("key") or new_items[0]["data"].get("key") or "")
            if not parent_key:
                raise ZoteroClientError("新增条目缺少标识")
        except (
            ZoteroClientError,
            ZoteroUnavailableError,
            ZoteroAccessDeniedError,
            httpx.RequestError,
        ) as exc:
            raise ZoteroWriteUnverifiedError(
                "Zotero 条目可能已经写入，但无法确认已保存到指定位置。"
                "请先检查 Zotero，避免重复同步。" + str(exc)
            ) from exc

        return {
            "item_key": parent_key,
            "attachment_key": "",
            "collection_key": collection_key,
            "collection_verified": True,
            "pdf_imported": False,
            "deduplicated": False,
            "warnings": ["已同步论文元数据；PDF 未导入，请在 Zotero 中手动添加附件。"]
            if pdf_path
            else [],
        }
