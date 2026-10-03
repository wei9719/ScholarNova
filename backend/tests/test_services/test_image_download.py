"""Image transport failures never masquerade as valid locally saved diagrams."""

import base64
import asyncio
from unittest.mock import Mock

import httpx
import pytest

from app.config import settings
from app.services.llm.gateway import LLMGateway


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
PROFILE = {'provider': 'sensenova', 'api_key': 'synthetic-test-key',
           'base_url': 'https://image.example/v1', 'model': 'fixture-image'}


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr('app.core.ssrf.validate_base_url', lambda _: (True, None))
    monkeypatch.setattr('app.core.ssrf._resolve_hostname', lambda _: ['93.184.216.34'])
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler),
    ))


@pytest.mark.asyncio
async def test_image_saved_atomically_without_forwarding_api_key_to_cdn(tmp_path, monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == 'POST':
            assert request.headers['authorization'] == 'Bearer synthetic-test-key'
            return httpx.Response(200, json={'data': [{'url': 'https://cdn.example/image.png'}]})
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=PNG)
    transport(monkeypatch, handler)
    path = tmp_path / 'figure.png'
    path.write_bytes(b'previous diagram')
    result = await LLMGateway.from_profile(PROFILE).generate_image('synthetic figure', save_path=str(path))
    assert result['status'] == 'ok'
    assert path.read_bytes() == PNG
    assert len(requests) == 2 and not list(tmp_path.glob('*.part'))


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['html', 'truncated', 'corrupt', 'too_large', 'redirect', 'timeout'])
async def test_bad_download_preserves_existing_image(tmp_path, monkeypatch, outcome):
    def handler(request):
        if request.method == 'POST':
            return httpx.Response(200, json={'data': [{'url': 'https://cdn.example/image.png'}]})
        if outcome == 'timeout':
            raise httpx.ReadTimeout('sensitive URL with token', request=request)
        if outcome == 'redirect':
            return httpx.Response(302, headers={'location': 'http://127.0.0.1/private'})
        content = {'html': b'<html>secret provider body</html>', 'truncated': PNG[:30],
                   'corrupt': PNG[:8] + b'bad-data' + PNG[-12:]}.get(outcome, PNG)
        return httpx.Response(200, content=content, headers=(
            {'content-length': str(21 * 1024 * 1024)} if outcome == 'too_large' else {}
        ))
    transport(monkeypatch, handler)
    path = tmp_path / 'figure.png'
    path.write_bytes(b'previous diagram')
    result = await LLMGateway.from_profile(PROFILE).generate_image('test', save_path=str(path))
    assert result['status'] == 'failed'
    assert path.read_bytes() == b'previous diagram'
    assert not list(tmp_path.glob('*.part'))
    assert 'secret' not in result['error'] and 'token' not in result['error']


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [None, [], {}, {'data': None}, {'data': [None, 1, {}]}, {'data': [{'url': 123}]}])
async def test_malformed_provider_response_is_a_safe_failure(monkeypatch, response):
    transport(monkeypatch, lambda _: httpx.Response(200, json=response))
    result = await LLMGateway.from_profile(PROFILE).generate_image('test')
    assert result['status'] == 'failed'


@pytest.mark.asyncio
async def test_missing_explicit_key_never_borrows_sensenova_key(monkeypatch):
    monkeypatch.setattr(settings, 'SENSENOVA_API_KEY', 'other-provider-secret')
    forbidden = Mock(side_effect=AssertionError('No request is allowed'))
    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    for provider in ('sensenova', 'custom'):
        result = await LLMGateway.from_profile({**PROFILE, 'provider': provider, 'api_key': None}).generate_image('test')
        assert result['status'] == 'failed'
    forbidden.assert_not_called()


@pytest.mark.asyncio
async def test_provider_error_body_and_private_image_urls_are_not_exposed(monkeypatch):
    transport(monkeypatch, lambda _: httpx.Response(401, text='secret raw token value'))
    result = await LLMGateway.from_profile(PROFILE).generate_image('test')
    assert 'HTTP 401' in result['error'] and 'secret' not in result['error']


@pytest.mark.asyncio
@pytest.mark.parametrize('url,addresses', [
    ('file:///tmp/image', []), ('http://cdn.example/image', []),
    ('https://user:secret@cdn.example/image', []),
    ('https://cdn.example/image', ['127.0.0.1']),
    ('https://cdn.example/image', ['93.184.216.34', '10.0.0.1']),
    ('https://cdn.example/image', []),
])
async def test_image_url_must_be_public_https(monkeypatch, url, addresses):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'data': [{'url': url}]})
    transport(monkeypatch, handler)
    monkeypatch.setattr('app.core.ssrf._resolve_hostname', lambda _: addresses)
    result = await LLMGateway.from_profile(PROFILE).generate_image('test')
    assert result['status'] == 'failed' and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('status,body,expected', [(200, PNG, 'ok'), (404, b'Not found', 'failed'), (200, b'<html>error</html>', 'failed')])
async def test_capability_check_validates_download_even_without_save_path(monkeypatch, status, body, expected):
    calls = []
    def handler(request):
        calls.append(request.method)
        if request.method == 'POST':
            return httpx.Response(200, json={'data': [{'url': 'https://cdn.example/image.png'}]})
        return httpx.Response(status, content=body)
    transport(monkeypatch, handler)
    result = await LLMGateway.from_profile(PROFILE).generate_image('test')
    assert result['status'] == expected and calls == ['POST', 'GET']


@pytest.mark.asyncio
async def test_actual_stream_limit_without_content_length(tmp_path, monkeypatch):
    class LargeStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(21):
                yield b'x' * (1024 * 1024)
    def handler(request):
        if request.method == 'POST':
            return httpx.Response(200, json={'data': [{'url': 'https://cdn.example/image.png'}]})
        return httpx.Response(200, stream=LargeStream())
    transport(monkeypatch, handler)
    path = tmp_path / 'figure.png'
    path.write_bytes(PNG)
    result = await LLMGateway.from_profile(PROFILE).generate_image('test', save_path=str(path))
    assert result['status'] == 'failed' and path.read_bytes() == PNG


@pytest.mark.asyncio
async def test_cancelled_download_closes_stream_and_preserves_previous_image(tmp_path, monkeypatch):
    entered, closed = asyncio.Event(), asyncio.Event()
    class WaitingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield PNG

        async def aclose(self):
            closed.set()
    def handler(request):
        if request.method == 'POST':
            return httpx.Response(200, json={'data': [{'url': 'https://cdn.example/image.png'}]})
        return httpx.Response(200, stream=WaitingStream())
    transport(monkeypatch, handler)
    path = tmp_path / 'figure.png'
    path.write_bytes(PNG)
    task = asyncio.create_task(LLMGateway.from_profile(PROFILE).generate_image('test', save_path=str(path)))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and path.read_bytes() == PNG
    assert not list(tmp_path.glob('*.part'))


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['POST', 'GET'])
async def test_compressed_responses_are_rejected_before_reading(monkeypatch, method):
    class ForbiddenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail('Compressed response must not be read or decompressed')
            yield b''
    def handler(request):
        assert request.headers['accept-encoding'] == 'identity'
        if request.method == method:
            return httpx.Response(200, headers={'content-encoding': 'gzip'}, stream=ForbiddenStream())
        return httpx.Response(200, json={'data': [{'url': 'https://cdn.example/image.png'}]})
    transport(monkeypatch, handler)
    result = await LLMGateway.from_profile(PROFILE).generate_image('test')
    assert result['status'] == 'failed'


@pytest.mark.asyncio
async def test_provider_json_response_is_size_bounded(monkeypatch):
    class OversizedJSON(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(3):
                yield b' ' * (1024 * 1024)
            pytest.fail('Response limit must stop reading')
    transport(monkeypatch, lambda _: httpx.Response(200, stream=OversizedJSON()))
    result = await LLMGateway.from_profile(PROFILE).generate_image('test')
    assert result['status'] == 'failed'
