import time

from app.adapters.local.identity import LocalIdentity
from app.contracts import Envelope


def test_envelope_uses_from_on_the_wire():
    env = Envelope.model_validate({"from": "a", "to": "b", "capability": "news_search"})
    wire = env.wire()
    assert wire["from"] == "a" and "from_" not in wire
    assert Envelope.model_validate(wire) == env


def test_reply_keeps_conversation_and_trace():
    req = Envelope(from_="a", to="b", capability="x")
    rep = req.reply(sender="b", payload={"ok": True})
    assert rep.conversation_id == req.conversation_id
    assert rep.trace_id == req.trace_id
    assert rep.reply_to == req.message_id
    assert (rep.from_, rep.to, rep.type) == ("b", "a", "response")


async def test_local_token_roundtrip(identity: LocalIdentity):
    token = await identity.issue_token("agent-a")
    principal = await identity.validate(token)
    assert principal is not None and principal.agent_id == "agent-a"


async def test_local_token_rejects_tampered_expired_and_foreign(identity: LocalIdentity):
    token = await identity.issue_token("agent-a")
    body, sig = token.split(".")
    assert await identity.validate(body + "." + sig[:-2] + "xx") is None
    assert await identity.validate(identity.sign("agent-a", time.time() - 1)) is None
    assert await identity.validate(await LocalIdentity("other").issue_token("agent-a")) is None
    assert await identity.validate("garbage") is None
