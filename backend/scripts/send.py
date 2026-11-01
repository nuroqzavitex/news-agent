"""Gửi message thử tới một agent (đóng vai agent khác). Dùng LOCAL_SHARED_SECRET để ký token.

Ví dụ:
  python -m scripts.send news_search '{"query": "giá vàng"}'
  python -m scripts.send news_search '{"query": "giá vàng"}' --dup        # gửi 2 lần cùng message_id
  python -m scripts.send news_search '{"query": "giá vàng"}' --bad-token  # mong đợi 401
  python -m scripts.send news_search '{"query": "x"}' --spoof             # token của agent khác → 401
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import httpx

from app.adapters.local.identity import LocalIdentity
from app.contracts import Envelope
from app.settings import get_settings


async def main() -> None:
    settings = get_settings()
    p = argparse.ArgumentParser()
    p.add_argument("capability")
    p.add_argument("payload", nargs="?", default="{}")
    p.add_argument("--url", default="http://localhost:8008")
    p.add_argument("--from-agent", default="cli-tester")
    p.add_argument("--to", default=settings.agent_id)
    p.add_argument("--dup", action="store_true", help="gửi lại cùng message_id")
    p.add_argument("--bad-token", action="store_true")
    p.add_argument("--spoof", action="store_true", help="token hợp lệ nhưng của agent khác")
    args = p.parse_args()

    identity = LocalIdentity(settings.local_shared_secret)
    token = await identity.issue_token("someone-else" if args.spoof else args.from_agent)
    if args.bad_token:
        token = token[:-4] + "AAAA"
    env = Envelope(from_=args.from_agent, to=args.to, capability=args.capability,
                   payload=json.loads(args.payload), token=token)

    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(2 if args.dup else 1):
            t0 = time.perf_counter()
            resp = await client.post(args.url.rstrip("/") + "/messages", json=env.wire())
            ms = (time.perf_counter() - t0) * 1000
            print(f"--- attempt {i + 1}: HTTP {resp.status_code} in {ms:.0f} ms")
            print(json.dumps(resp.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252
    asyncio.run(main())
