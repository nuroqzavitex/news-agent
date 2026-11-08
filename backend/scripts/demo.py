"""Run the 7 demo scenarios of spec §12 against a running stack and check each one.

Start the stack with the mock agents first (`docker compose up -d`, with COMPOSE_PROFILES=mock in .env), then:

  uv run python -m scripts.demo                     # all scenarios
  uv run python -m scripts.demo --only 3,4,5        # some of them
  uv run python -m scripts.demo --skip-faults       # everything except §9 faults (scenario 6)
  uv run python -m scripts.demo --api https://news.example --dashboard https://dash.example --password ...

Every fault the script switches on is switched off again at the end, also when a check fails or on Ctrl+C.
Exit code 0 when every check passed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from app.adapters.local.identity import LocalIdentity
from app.contracts import Envelope
from app.settings import get_settings

FINANCE = ("mock-finance-a", "mock-finance-b")
MOCKS = (*FINANCE, "mock-weather")
CAPABILITIES = {"news_search", "news_summary", "sentiment", "trending_topics"}
# Tickers the planner recognises. Each price question uses one that is not in memory yet, so the agent really has
# to call the finance agents instead of answering from what it learned on an earlier run (TTL 15 minutes).
TICKERS = (
    "TCB VCB HPG FPT MWG VNM MSN VIC VHM ACB MBB SSI VPB STB HDB GAS PLX POW REE DGC PNJ VRE BID CTG SAB GVR BCM VJC "
    "KDH NVL DXG PDR VND HCM VCI SHB EIB LPB TPB OCB"
).split()
TCB_QUESTION = "Tin tức ảnh hưởng thế nào tới cổ phiếu {symbol} tuần này?"
PRICE_QUESTION = "Giá cổ phiếu {symbol} hôm nay thế nào?"
SENDER = "demo-other-team"


class Demo:
    def __init__(self, http: httpx.AsyncClient, args: argparse.Namespace, secret: str) -> None:
        self.http = http
        self.args = args
        self.identity = LocalIdentity(secret)
        self.agent_id = ""
        self.passed = 0
        self.failed: list[str] = []
        self.scenario = ""
        self._admin: dict[str, str] | None = None
        self._used: set[str] = set()
        self.coordinate: dict[str, Any] | None = None  # scenario 3's answer, reused by 4, 5 and 7

    # --- output ---------------------------------------------------------------------------------------------

    def title(self, number: int, name: str, goal: str) -> None:
        self.scenario = f"{number}. {name}"
        print(f"\n=== {self.scenario} " + "=" * max(3, 100 - len(self.scenario)))
        print(f"    {goal}")

    def check(self, ok: bool, text: str) -> bool:
        print(f"  {'✓' if ok else '✗'} {text}")
        if ok:
            self.passed += 1
        else:
            self.failed.append(f"{self.scenario}: {text}")
        return ok

    @staticmethod
    def info(text: str) -> None:
        for line in text.splitlines():
            print(f"    {line}")

    # --- calls ----------------------------------------------------------------------------------------------

    async def envelope(self, capability: str, payload: dict[str, Any], token: str | None = None) -> Envelope:
        return Envelope(
            from_=SENDER, to=self.agent_id, capability=capability, payload=payload,
            token=token or await self.identity.issue_token(SENDER),
        )

    async def send(
        self, capability: str, payload: dict[str, Any], *, token: str | None = None
    ) -> tuple[httpx.Response, int]:
        """Send a message as another team's agent would."""
        return await self.post(await self.envelope(capability, payload, token))

    async def post(self, env: Envelope) -> tuple[httpx.Response, int]:
        started = time.perf_counter()
        resp = await self.http.post(self.args.api + "/messages", json=env.wire())
        return resp, round((time.perf_counter() - started) * 1000)

    async def ask(self, question: str) -> tuple[dict[str, Any], int]:
        started = time.perf_counter()
        resp = await self.http.post(self.args.api + "/api/v1/ask", json={"question": question})
        resp.raise_for_status()
        return resp.json(), round((time.perf_counter() - started) * 1000)

    async def trace(self, trace_id: str) -> list[dict[str, Any]]:
        return (await self.http.get(f"{self.args.api}/api/v1/traces/{trace_id}")).json()

    async def health(self) -> dict[str, Any]:
        return (await self.http.get(self.args.api + "/health")).json()

    async def versions(self, key: str) -> list[dict[str, Any]]:
        return (await self.http.get(f"{self.args.api}/api/v1/memory/{key}/versions")).json()

    async def admin(self) -> dict[str, str]:
        if self._admin is None:
            resp = await self.http.post(self.args.api + "/api/v1/admin/login", json={"password": self.args.password})
            if resp.status_code != 200:
                raise SystemExit(f"Admin login failed ({resp.status_code}): pass --password or set ADMIN_PASSWORD.")
            self._admin = {"Authorization": f"Bearer {resp.json()['token']}"}
        return self._admin

    async def set_faults(self, **faults: Any) -> None:
        resp = await self.http.put(self.args.api + "/api/v1/admin/faults", json=faults, headers=await self.admin())
        if resp.status_code == 403:
            raise SystemExit("Fault injection is off: set FAULTS_ENABLED=true for agent-api, or use --skip-faults.")
        resp.raise_for_status()
        await asyncio.sleep(1.2)  # every process caches the switches for 1 second

    async def mock_fault(self, agent_id: str, **fault: Any) -> None:
        resp = await self.http.put(
            f"{self.args.api}/api/v1/admin/mocks/{agent_id}/fault", json=fault, headers=await self.admin()
        )
        if resp.status_code != 200:
            raise SystemExit(f"Cannot control {agent_id} ({resp.status_code}): is the compose profile `mock` on?")

    async def reset_faults(self) -> None:
        await self.set_faults()
        for agent_id in MOCKS:
            await self.mock_fault(agent_id)

    async def fresh_ticker(self, preferred: str | None = None) -> str:
        """A ticker with nothing fresh in memory, so asking about it makes the agent call the finance agents."""
        now = datetime.now(UTC)
        for symbol in ([preferred] if preferred else []) + [t for t in TICKERS if t != preferred]:
            if symbol in self._used:
                continue
            fresh = [
                v for v in await self.versions(f"stock_price:{symbol}")
                if v["superseded_by"] is None and v["expires_at"] and datetime.fromisoformat(v["expires_at"]) > now
            ]
            if not fresh:
                self._used.add(symbol)
                return symbol
        raise SystemExit("Every demo ticker is in memory already: wait 15 minutes for them to expire.")

    # --- scenarios ------------------------------------------------------------------------------------------

    async def discover(self) -> None:
        self.title(1, "Discover", "Agent khởi động, đăng ký, và agent khác tìm thấy nó qua capability news_search.")
        card = (await self.http.get(self.args.api + "/card")).json()
        self.check(CAPABILITIES <= set(card["capabilities"]), f"/card của {card['agent_id']}: {card['capabilities']}")
        health = await self.health()
        self.check(health["status"] in ("online", "degraded"), f"/health: {health['status']}, deps {health['deps']}")
        agents = (await self.http.get(self.args.api + "/api/v1/agents")).json()
        me = next((a for a in agents if a["agent_id"] == self.agent_id), None)
        self.check(me is not None and "news_search" in me["capabilities"],
                   "Registry trả về agent này khi tìm theo news_search")
        others = [a for a in agents if a["agent_id"] != self.agent_id]
        self.check(len(others) > 0, f"Agent này thấy {len(others)} agent khác: "
                   + ", ".join(f"{a['agent_id']} ({', '.join(a['capabilities'])})" for a in others))
        if health["modes"].get("registry") == "local":
            self.info("Registry đang ở chế độ local (YAML). Bước find_by_capability từ nhóm khác cần adapter Team 1.")

    async def serve(self) -> None:
        self.title(2, "Phục vụ", 'Agent nhóm khác gửi news_summary {query: "giá vàng"}, nhận tóm tắt kèm nguồn < 5 s.')
        resp, ms = await self.send("news_summary", {"query": "giá vàng"})
        body = resp.json()
        payload = body.get("payload", {})
        self.check(resp.status_code == 200 and body.get("type") == "response",
                   f"HTTP {resp.status_code}, type={body.get('type')}, reply_to={body.get('reply_to')}")
        self.check(ms < 5000, f"Trả lời trong {ms} ms")
        sources = payload.get("sources", [])
        self.check(len(sources) > 0, f"{len(sources)} nguồn, tạo bởi {payload.get('generated_by')}")
        self.info(f"Tóm tắt: {str(payload.get('summary', ''))[:300]}")
        for s in sources[:3]:
            self.info(f"  - {s.get('source')}: {s.get('url')}")

    async def coordinate_step(self) -> None:
        self.title(3, "Coordinate", "Hỏi về cổ phiếu: lấy tin + sentiment của mình, song song gọi Finance, gộp lại.")
        symbol = await self.fresh_ticker("TCB")
        if symbol != "TCB":
            self.info(f"TCB đã có trong memory từ lần chạy trước (TTL 15 phút), nên dùng {symbol} thay thế.")
        question = TCB_QUESTION.format(symbol=symbol)
        self.info(f"Câu hỏi: {question}")
        result, ms = await self.ask(question)
        self.coordinate = result
        steps = {s["capability"]: s for s in result["steps"]}
        self.check(len(result["agents"]) >= 3, f"{len(result['agents'])} agent tham gia: {', '.join(result['agents'])}")
        stock = steps.get("stock_price", {})
        self.check(stock.get("status") == "ok" and set(FINANCE) <= set(stock.get("agents", [])),
                   f"stock_price: {stock.get('status')} từ {stock.get('agents')}")
        local = [s for s in result["steps"] if s["capability"] in CAPABILITIES]
        self.check(local and all(s["status"] == "ok" for s in local),
                   "Capability của mình: " + ", ".join(f"{s['capability']} {s['status']}" for s in local))
        events = await self.trace(result["trace_id"])
        spent = (datetime.fromisoformat(events[-1]["ts"]) - datetime.fromisoformat(events[0]["ts"])).total_seconds()
        total_steps = sum(s["latency_ms"] for s in result["steps"])
        self.check(spent * 1000 < total_steps,
                   f"Các bước chạy song song: cả câu hỏi {spent * 1000:.0f} ms, cộng từng bước {total_steps} ms")
        self.info(f"Thời gian phía client: {ms} ms")
        self.info(f"Trả lời ({result['generated_by']}): {result['answer']}")
        self.info(f"Trace: {self.args.dashboard}/traces/{result['trace_id']}")

    async def learn(self) -> None:
        self.title(4, "Learn", "Giá được lưu vào knowledge_items (TTL 15 phút); hỏi lại trong TTL thì dùng memory.")
        if not self.coordinate:
            await self.coordinate_step()
        assert self.coordinate
        stock = next(s for s in self.coordinate["steps"] if s["capability"] == "stock_price")
        key = f"stock_price:{stock['request']['symbol'].upper()}"
        current = [v for v in await self.versions(key) if v["superseded_by"] is None]
        for v in current:
            ttl = (datetime.fromisoformat(v["expires_at"]) - datetime.fromisoformat(v["created_at"])).total_seconds()
            self.check(v["source_agent"] in FINANCE and abs(ttl - 900) < 5,
                       f"{key} v{v['version']} từ {v['source_agent']}: {v['content'].get('price')} VND, "
                       f"TTL {ttl:.0f} s")
        result, ms = await self.ask(self.coordinate["question"])
        again = next(s for s in result["steps"] if s["capability"] == "stock_price")
        events = await self.trace(result["trace_id"])
        sends = [e for e in events if e["event"] == "broker.send" and e["attrs"].get("capability") == "stock_price"]
        self.check(again["status"] == "memory" and not sends,
                   f"Hỏi lại: stock_price {again['status']}, {len(sends)} lần gọi finance ({ms} ms)")

    async def conflict(self) -> None:
        self.title(5, "Conflict", "Hai agent finance báo giá khác nhau: memory ghi conflict_group, câu trả lời nêu rõ.")
        if not self.coordinate:
            await self.coordinate_step()
        assert self.coordinate
        stock = next(s for s in self.coordinate["steps"] if s["capability"] == "stock_price")
        key = f"stock_price:{stock['request']['symbol'].upper()}"
        current = [v for v in await self.versions(key) if v["superseded_by"] is None]
        groups = {v["conflict_group"] for v in current}
        self.check(len(current) >= 2 and len(groups) == 1 and None not in groups,
                   f"{len(current)} giá đang hiệu lực cho {key}, cùng conflict_group {next(iter(groups), None)}")
        for v in current:
            self.info(f"  {v['source_agent']}: {v['content'].get('price')} VND, confidence {v['confidence']}")
        answer = self.coordinate["answer"].lower()
        self.check(self.coordinate["has_conflict"] and any(w in answer for w in ("xung đột", "khác nhau", "chênh")),
                   "Câu trả lời nói rõ là có xung đột")

    async def survive(self) -> None:
        self.title(
            6, "Survive", "Bật lần lượt từng lỗi ở §9: agent vẫn trả lời (có thể thiếu), trace cho thấy cách xử lý."
        )
        cases: list[tuple[str, Callable[[], Awaitable[None]]]] = [
            ("Message trùng (gửi lại cùng message_id)", self._duplicate),
            ("Agent đích không trả lời (mock-finance-a chậm 5 s)", self._slow_target),
            ("Lỗi tạm thời, retry giữ nguyên message_id (mock-finance-a lỗi 503 một lần)", self._retry_same_id),
            ("Agent đích down (mock-finance-a luôn trả 503)", self._target_down),
            ("Không còn agent nào (cả hai finance trả 503)", self._all_down),
            ("Registry down", self._registry_down),
            ("Identity down", self._identity_down),
            ("Observability down", self._tracer_down),
            ("Gemini down", self._llm_down),
            ("Agent này chậm / lỗi (fault inbound)", self._self_faults),
        ]
        try:
            await self.reset_faults()
            for name, case in cases:
                print(f"\n  -- {name}")
                try:
                    await case()
                finally:
                    await self.reset_faults()
        finally:
            await self.reset_faults()
        health = await self.health()
        self.check(health["faults"] == [], "Đã tắt hết lỗi giả lập")
        self.info("\nCrawler / processor chết: chạy tay `docker compose stop crawler`, /health báo crawler stale sau")
        self.info("2 chu kỳ crawl, tin cũ vẫn được trả. `docker compose start crawler` để bật lại.")

    def _stock(self, result: dict[str, Any]) -> dict[str, Any]:
        return next((s for s in result["steps"] if s["capability"] == "stock_price"), {})

    async def _price(self) -> tuple[dict[str, Any], list[str]]:
        result, _ = await self.ask(PRICE_QUESTION.format(symbol=await self.fresh_ticker()))
        return result, [e["event"] for e in await self.trace(result["trace_id"])]

    async def _duplicate(self) -> None:
        env = await self.envelope("news_search", {"query": "lãi suất"})
        first, _ = await self.post(env)
        second, _ = await self.post(env)  # a retry resends the same envelope, message_id included
        events = [e["event"] for e in await self.trace(first.json()["trace_id"])]
        self.check(first.json() == second.json() and "msg.duplicate" in events,
                   "Lần 2 nhận lại đúng reply cũ, không xử lý lại (msg.duplicate)")

    async def _slow_target(self) -> None:
        await self.mock_fault("mock-finance-a", delay_s=5)
        result, events = await self._price()
        stock = self._stock(result)
        self.check(stock.get("status") == "ok" and stock.get("agents") == ["mock-finance-b"],
                   f"Vẫn có giá, từ {stock.get('agents')}; broker.retry x{events.count('broker.retry')}")

    async def _retry_same_id(self) -> None:
        await self.mock_fault("mock-finance-a", fail_status=503, fail_times=1)
        result, _ = await self._price()
        events = await self.trace(result["trace_id"])
        tries = [e for e in events if e["event"] in ("broker.send", "broker.retry")
                 and e["attrs"].get("to_agent") == "mock-finance-a"]
        ids = {e["message_id"] for e in tries}
        self.check("mock-finance-a" in self._stock(result).get("agents", []) and len(tries) >= 2 and len(ids) == 1,
                   f"{len(tries)} lần gửi tới mock-finance-a, cùng message_id {next(iter(ids), None)}")

    async def _target_down(self) -> None:
        await self.mock_fault("mock-finance-a", fail_status=503)
        result, _ = await self._price()
        stock = self._stock(result)
        self.check(stock.get("status") == "ok" and stock.get("agents") == ["mock-finance-b"],
                   f"Giá lấy từ agent còn lại: {stock.get('agents')}")

    async def _all_down(self) -> None:
        for agent_id in FINANCE:
            await self.mock_fault(agent_id, fail_status=503)
        result, _ = await self._price()
        self.check(result["partial"] and "stock_price" in result["missing"] and bool(result["answer"]),
                   f"Trả lời một phần, thiếu {result['missing']}")
        self.info(f"Trả lời: {result['answer']}")

    async def _registry_down(self) -> None:
        await self.set_faults(registry_down=True)
        result, events = await self._price()
        how = "agent_cache trong DB" if "registry.fallback_cache" in events else "cache Redis của lần discover trước"
        self.check(self._stock(result).get("status") == "ok", f"Vẫn tìm được agent finance, nhờ {how}")
        self.check((await self.health())["deps"]["registry"] == "down", "/health báo registry down")

    async def _identity_down(self) -> None:
        known = await self.identity.issue_token(SENDER)
        await self.send("news_search", {"query": "TCB"}, token=known)
        await self.set_faults(identity_down=True)
        ok, _ = await self.send("news_search", {"query": "TCB"}, token=known)
        unknown = self.identity.sign(SENDER, time.time() + 300)
        refused, _ = await self.send("news_search", {"query": "TCB"}, token=unknown)
        self.check(ok.status_code == 200, f"Token đã validate trong 60 s qua vẫn được nhận: HTTP {ok.status_code}")
        self.check(refused.status_code == 401, f"Token chưa gặp bao giờ bị từ chối: HTTP {refused.status_code}")

    async def _tracer_down(self) -> None:
        await self.set_faults(tracer_down=True)
        resp, _ = await self.send("news_search", {"query": "vàng"})
        events = [e["event"] for e in await self.trace(resp.json()["trace_id"])]
        self.check(resp.status_code == 200 and "msg.replied" in events,
                   f"Vẫn trả lời, trace vẫn ghi local: {events}")

    async def _llm_down(self) -> None:
        await self.set_faults(llm_down=True)
        result, events = await self._price()
        self.check(bool(result["answer"]) and result["generated_by"] != "gemini" and "llm.fallback" in events,
                   f"Vẫn trả lời, soạn bằng {result['generated_by']} (llm.fallback)")

    async def _self_faults(self) -> None:
        await self.set_faults(inbound_delay_s=1.5)
        resp, ms = await self.send("news_search", {"query": "vàng"})
        self.check(resp.status_code == 200 and ms >= 1500, f"Chậm 1,5 s: HTTP {resp.status_code} sau {ms} ms")
        await self.set_faults(inbound_fail_status=503)
        resp, _ = await self.send("news_search", {"query": "vàng"})
        self.check(resp.status_code == 503, f"Lỗi 503 để agent khác thử retry/failover: HTTP {resp.status_code}")

    async def trace_view(self) -> None:
        self.title(7, "Trace", "Dựng lại toàn bộ đường đi của kịch bản 3.")
        if not self.coordinate:
            await self.coordinate_step()
        assert self.coordinate
        events = await self.trace(self.coordinate["trace_id"])
        peers = {e["attrs"]["to_agent"] for e in events if e["event"] == "broker.send"}
        names = [e["event"] for e in events]
        self.check(names[:1] == ["ask.received"] and names[-1:] == ["ask.answered"],
                   f"{len(events)} event, từ ask.received tới ask.answered")
        agents = sorted(peers | {self.agent_id})
        self.check(len(agents) >= 3, f"Agent trong trace: {', '.join(agents)}")
        start = datetime.fromisoformat(events[0]["ts"])
        for e in events:
            offset = (datetime.fromisoformat(e["ts"]) - start).total_seconds() * 1000
            attrs = json.dumps(e["attrs"], ensure_ascii=False)
            self.info(f"  +{offset:6.0f} ms  {e['event']:<16} {attrs[:90]}")
        self.info(f"Mở trên dashboard: {self.args.dashboard}/traces/{self.coordinate['trace_id']}")


async def main() -> None:
    settings = get_settings()
    p = argparse.ArgumentParser(description="Run the spec §12 demo scenarios against a running stack.")
    p.add_argument("--api", default="http://localhost:8008")
    p.add_argument("--dashboard", default="http://localhost:3000")
    p.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD", settings.admin_password))
    p.add_argument("--secret", default=settings.local_shared_secret, help="LOCAL_SHARED_SECRET of the agent")
    p.add_argument("--only", default="1,2,3,4,5,6,7", help="comma-separated scenario numbers")
    p.add_argument("--skip-faults", action="store_true", help="skip scenario 6")
    args = p.parse_args()
    args.api, args.dashboard = args.api.rstrip("/"), args.dashboard.rstrip("/")
    only = {int(n) for n in args.only.split(",") if n.strip()} - ({6} if args.skip_faults else set())

    async with httpx.AsyncClient(timeout=30) as http:
        demo = Demo(http, args, args.secret)
        try:
            demo.agent_id = (await http.get(args.api + "/card")).json()["agent_id"]
        except httpx.HTTPError as exc:
            raise SystemExit(f"Cannot reach {args.api} ({type(exc).__name__}): is the stack running?") from exc
        scenarios = {1: demo.discover, 2: demo.serve, 3: demo.coordinate_step, 4: demo.learn, 5: demo.conflict,
                     6: demo.survive, 7: demo.trace_view}
        for number in sorted(only):
            try:
                await scenarios[number]()
            except (httpx.HTTPError, KeyError, StopIteration, ValueError) as exc:
                demo.check(False, f"lỗi khi chạy: {type(exc).__name__}: {exc}")

    print(f"\n{demo.passed} kiểm tra đạt, {len(demo.failed)} không đạt.")
    for failure in demo.failed:
        print(f"  ✗ {failure}")
    sys.exit(1 if demo.failed else 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # the Windows console defaults to cp1252
    asyncio.run(main())
