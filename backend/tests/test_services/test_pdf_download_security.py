import httpx
import pytest

from app.services.pdf import fetcher


@pytest.mark.asyncio
async def test_each_redirect_is_validated_before_request(monkeypatch):
    requested, checked = [], []
    def validate(url):
        checked.append(url)
        return (False, "private address") if "127.0.0.1" in url else (True, None)
    monkeypatch.setattr(fetcher, "validate_url", validate)
    def response(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response), follow_redirects=False) as client:
        with pytest.raises(ValueError, match="private"):
            await fetcher._pdf_bytes(client, "https://example.org/paper")
    assert len(requested) == 1
    assert len(checked) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("body,headers,error", [
    (b"<html>login</html>", {}, "不是 PDF"),
    (b"%PDF-test", {"content-length": str(fetcher.MAX_FILE_SIZE + 1)}, "50 MB"),
])
async def test_pdf_response_is_checked(monkeypatch, body, headers, error):
    monkeypatch.setattr(fetcher, "validate_url", lambda url: (True, None))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body, headers=headers))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match=error):
            await fetcher._pdf_bytes(client, "https://example.org/paper")


@pytest.mark.asyncio
async def test_pdf_redirect_and_content_success(monkeypatch):
    monkeypatch.setattr(fetcher, "validate_url", lambda url: (True, None))
    transport = httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "/file.pdf"})
                                   if request.url.path == "/paper" else httpx.Response(200, content=b"%PDF-safe"))
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        assert await fetcher._pdf_bytes(client, "https://example.org/paper") == b"%PDF-safe"


@pytest.mark.asyncio
async def test_credential_url_never_contacts_server():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail("network must not run"))) as client:
        with pytest.raises(ValueError, match="账号密码"):
            await fetcher._pdf_bytes(client, "https://secret:password@example.org/paper")
