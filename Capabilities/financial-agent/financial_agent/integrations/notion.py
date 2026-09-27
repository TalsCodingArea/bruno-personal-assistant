"""Project adapter around the async Python ``notion-client`` SDK."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from notion_client import AsyncClient
from notion_client.errors import APIResponseError

NotionObject = dict[str, Any]


class NotionError(RuntimeError):
    """Raised when Notion returns an unsuccessful response."""


class NotionGateway(Protocol):
    """The Notion operations used by finance services."""

    async def query_all(
        self,
        data_source_id: str,
        *,
        filter_: Mapping[str, Any] | None = None,
        sorts: Sequence[Mapping[str, Any]] = (),
        filter_properties: Sequence[str] = (),
        max_results: int | None = None,
    ) -> list[NotionObject]: ...

    async def retrieve_page(self, page_id: str) -> NotionObject: ...

    async def retrieve_data_source(self, data_source_id: str) -> NotionObject: ...

    async def resolve_database_data_source(self, database_id: str) -> str: ...

    async def retrieve_page_text(self, page_id: str, *, max_chars: int) -> str: ...

    async def create_page(
        self,
        data_source_id: str,
        properties: Mapping[str, Any],
        *,
        children: Sequence[Mapping[str, Any]] = (),
    ) -> NotionObject: ...

    async def update_page(
        self, page_id: str, properties: Mapping[str, Any]
    ) -> NotionObject: ...

    async def upload_file(
        self, path: Path, *, filename: str | None = None, content_type: str
    ) -> str: ...


class NotionClient:
    """Expose only the SDK operations needed by the finance application.

    ``notion-client`` owns HTTP details, authentication, retries, and endpoint paths. This
    adapter owns pagination limits and presents our stable ``NotionGateway`` interface.
    """

    def __init__(
        self,
        token: str,
        *,
        api_version: str = "2026-03-11",
        timeout_seconds: float = 30.0,
        client: AsyncClient | None = None,
    ) -> None:
        self._client = client or AsyncClient(
            auth=token,
            notion_version=api_version,
            timeout_ms=int(timeout_seconds * 1000),
        )

    async def __aenter__(self) -> "NotionClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def query_all(
        self,
        data_source_id: str,
        *,
        filter_: Mapping[str, Any] | None = None,
        sorts: Sequence[Mapping[str, Any]] = (),
        filter_properties: Sequence[str] = (),
        max_results: int | None = None,
    ) -> list[NotionObject]:
        """Query and paginate a data source, returning at most Notion's 10k limit."""

        if max_results is not None and max_results <= 0:
            return []
        results: list[NotionObject] = []
        cursor: str | None = None

        while True:
            remaining = max_results - len(results) if max_results is not None else 100
            body: dict[str, Any] = {"page_size": min(100, remaining)}
            if filter_ is not None:
                body["filter"] = dict(filter_)
            if sorts:
                body["sorts"] = [dict(item) for item in sorts]
            if cursor is not None:
                body["start_cursor"] = cursor

            try:
                response = await self._client.data_sources.query(
                    data_source_id=data_source_id,
                    filter_properties=list(filter_properties),
                    **body,
                )
            except APIResponseError as exc:
                raise NotionError(
                    f"Notion data-source query failed; request_id={exc.request_id or 'unknown'}"
                ) from exc
            results.extend(item for item in response.get("results", []) if isinstance(item, dict))

            if max_results is not None and len(results) >= max_results:
                return results[:max_results]

            if not response.get("has_more"):
                return results
            cursor_value = response.get("next_cursor")
            if not isinstance(cursor_value, str):
                raise NotionError("Notion indicated more results without returning a cursor")
            cursor = cursor_value

    async def retrieve_page(self, page_id: str) -> NotionObject:
        result: NotionObject = await self._client.pages.retrieve(page_id=page_id)
        return result

    async def retrieve_data_source(self, data_source_id: str) -> NotionObject:
        result: NotionObject = await self._client.data_sources.retrieve(
            data_source_id=data_source_id
        )
        return result

    async def resolve_database_data_source(self, database_id: str) -> str:
        """Resolve a single-source database without exposing the SDK to domain adapters."""

        database: NotionObject = await self._client.databases.retrieve(
            database_id=database_id
        )
        raw_sources = database.get("data_sources", [])
        sources = raw_sources if isinstance(raw_sources, list) else []
        ids: list[str] = []
        for item in sources:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if isinstance(item_id, str):
                ids.append(item_id)
        if len(ids) != 1:
            raise NotionError(
                "The bank database must expose exactly one data source, or "
                "BANK_ACCOUNT_NOTION_DATA_SOURCE_ID must be configured"
            )
        return ids[0]

    async def create_page(
        self,
        data_source_id: str,
        properties: Mapping[str, Any],
        *,
        children: Sequence[Mapping[str, Any]] = (),
    ) -> NotionObject:
        body: dict[str, Any] = {
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": dict(properties),
        }
        if children:
            body["children"] = [dict(child) for child in children]
        result: NotionObject = await self._client.pages.create(**body)
        return result

    async def update_page(
        self, page_id: str, properties: Mapping[str, Any]
    ) -> NotionObject:
        result: NotionObject = await self._client.pages.update(
            page_id=page_id, properties=dict(properties)
        )
        return result

    async def upload_file(
        self,
        path: Path,
        *,
        filename: str | None = None,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload one local file and return its Notion file-upload ID."""

        resolved = path.resolve()
        if not resolved.is_file():
            raise ValueError(f"File does not exist: {resolved}")
        display_name = filename or resolved.name
        upload = await self._client.file_uploads.create(
            mode="single_part",
            filename=display_name,
            content_type=content_type,
        )
        upload_id = upload.get("id") if isinstance(upload, dict) else None
        if not isinstance(upload_id, str) or not upload_id:
            raise NotionError("Notion created a file upload without returning an ID")
        await self._client.file_uploads.send(
            upload_id,
            file=(display_name, resolved.read_bytes(), content_type),
        )
        return upload_id

    async def retrieve_page_text(self, page_id: str, *, max_chars: int) -> str:
        chunks: list[str] = []
        length = 0

        async def visit(block_id: str, depth: int) -> None:
            nonlocal length
            cursor: str | None = None
            while length < max_chars:
                params: dict[str, Any] = {"page_size": 100}
                if cursor is not None:
                    params["start_cursor"] = cursor
                response = await self._client.blocks.children.list(block_id=block_id, **params)
                for block in response.get("results", []):
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    value = block.get(block_type, {}) if isinstance(block_type, str) else {}
                    rich_text = value.get("rich_text", []) if isinstance(value, dict) else []
                    text = "".join(
                        item.get("plain_text", "") for item in rich_text if isinstance(item, dict)
                    ).strip()
                    if text:
                        remaining = max_chars - length
                        chunks.append(text[:remaining])
                        length += min(len(text), remaining)
                    child_id = block.get("id")
                    if block.get("has_children") and isinstance(child_id, str) and depth < 3:
                        await visit(child_id, depth + 1)
                    if length >= max_chars:
                        return
                if not response.get("has_more"):
                    return
                next_cursor = response.get("next_cursor")
                if not isinstance(next_cursor, str):
                    return
                cursor = next_cursor

        await visit(page_id, 0)
        return "\n".join(chunks)
