import time

import fakeredis
import httpx

from app.services.politeness import Politeness

ROBOTS = "User-agent: *\nDisallow: /private/\n"


def _http() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        return httpx.Response(200, text="ok")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_robots_txt_is_respected():
    p = Politeness(fakeredis.FakeAsyncRedis(), _http(), "NewsAgent")
    assert await p.allowed("https://news.example/a.html") is True
    assert await p.allowed("https://news.example/private/b.html") is False


async def test_same_host_requests_are_spaced_out():
    p = Politeness(fakeredis.FakeAsyncRedis(), _http(), "NewsAgent", min_interval_ms=200)
    t0 = time.monotonic()
    await p.wait_turn("https://news.example/1")
    await p.wait_turn("https://news.example/2")
    assert time.monotonic() - t0 >= 0.15
    t1 = time.monotonic()
    await p.wait_turn("https://other.example/1")  # host khác không phải chờ
    assert time.monotonic() - t1 < 0.1
