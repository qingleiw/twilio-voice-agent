"""A phone call, answered by a model, over Twilio Media Streams.

Twilio fetches /voice when a call comes in and is handed a <Connect><Stream> back. It then opens a
WebSocket to /stream and sends the caller's audio as base64 mu-law at 8 kHz, 20 ms at a time; what
we send back on the same socket is what the caller hears. The model is spoken to in 16 kHz PCM and
answers in 24 kHz PCM, so each frame is resampled on the way through.

    uvicorn app:app --port 8000

The call ends in one of three ways: the caller hangs up, the model calls the end_call tool, or the
model calls transfer_call. The last two leave a note in NEXT, which /next reads when Twilio comes
back for the next instruction.
"""
import asyncio
import audioop
import base64
import hashlib
import hmac
import json
import logging
import os
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

import agent

log = logging.getLogger("app")
app = FastAPI()

AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "")
VERIFY = os.getenv("TWILIO_VERIFY", "1") != "0" and bool(AUTH_TOKEN)

FRAME_MS = 20
NEXT: dict[str, tuple[str, str]] = {}          # call sid -> what to do when the stream ends


def twiml(body: str) -> Response:
    return Response(content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>',
                    media_type="application/xml")


async def signed_form(request: Request) -> dict | None:
    """The webhook's fields, or None if Twilio did not sign it.

    Twilio signs the full URL plus every form field, sorted and concatenated, with the account's
    auth token. The URL has to match what Twilio called, which is why PUBLIC_HOST is explicit:
    behind a proxy the request's own host header is whatever the proxy chose to send.
    """
    pairs = parse_qsl((await request.body()).decode(), keep_blank_values=True)
    if VERIFY:
        host = PUBLIC_HOST or request.headers.get("host", "")
        data = f"https://{host}{request.url.path}" + "".join(k + v for k, v in sorted(pairs))
        want = base64.b64encode(hmac.new(AUTH_TOKEN.encode(), data.encode(), hashlib.sha1).digest()).decode()
        if not hmac.compare_digest(want, request.headers.get("x-twilio-signature", "")):
            log.warning("unsigned request to %s (does PUBLIC_HOST match the webhook URL?)", request.url.path)
            return None
    return dict(pairs)


@app.get("/")
async def health():
    return {"ok": True, "model": agent.MODEL, "verifying_signatures": VERIFY}


@app.post("/voice")
async def voice(request: Request):
    """The call has come in. Hand the audio to our own WebSocket and say where to go afterwards."""
    form = await signed_form(request)
    if form is None:
        return Response(status_code=403)
    host = PUBLIC_HOST or request.headers.get("host", "")
    caller = form.get("From", "")
    log.info("call %s from %s", form.get("CallSid"), caller)
    return twiml(f'<Connect><Stream url="wss://{host}/stream">'
                 f'<Parameter name="caller" value="{caller}"/>'
                 f'</Stream></Connect>'
                 f'<Redirect method="POST">https://{host}/next</Redirect>')


@app.post("/next")
async def next_step(request: Request):
    """The stream has ended. Hang up, or put the caller through to a person."""
    form = await signed_form(request)
    if form is None:
        return Response(status_code=403)
    what, where = NEXT.pop(form.get("CallSid", ""), ("hangup", ""))
    if what == "transfer" and where:
        return twiml(f"<Dial>{where}</Dial>")
    return twiml("<Hangup/>")


@app.websocket("/stream")
async def stream(ws: WebSocket):
    """Both directions of one call's audio.

    Twilio's frames arrive about every 20 ms whether or not anyone is speaking, so this loop is
    also the call's clock. The model streams its reply faster than real time; Twilio buffers it
    and plays it out at the proper rate, which is why an interruption has to clear that buffer
    explicitly rather than just stopping the sender.
    """
    await ws.accept()
    to_model: asyncio.Queue[bytes] = asyncio.Queue()
    state = {"sid": "", "call_sid": "", "caller": ""}

    async def to_caller(pcm24: bytes):
        """The model's 24 kHz, as the 8 kHz mu-law Twilio wants."""
        pcm8, _ = audioop.ratecv(pcm24, 2, 1, 24000, 8000, None)
        await ws.send_text(json.dumps({"event": "media", "streamSid": state["sid"],
                                       "media": {"payload": base64.b64encode(audioop.lin2ulaw(pcm8, 2)).decode()}}))

    async def interrupted():
        """The caller has started talking: drop whatever Twilio has not played yet."""
        await ws.send_text(json.dumps({"event": "clear", "streamSid": state["sid"]}))

    def finish(what: str, where: str = ""):
        NEXT[state["call_sid"]] = (what, where)

    session = asyncio.create_task(agent.run(to_model, to_caller, interrupted, finish, state))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            event = msg.get("event")
            if event == "start":
                state["sid"] = msg["streamSid"]
                state["call_sid"] = msg["start"]["callSid"]
                state["caller"] = msg["start"].get("customParameters", {}).get("caller", "")
            elif event == "media":
                ulaw = base64.b64decode(msg["media"]["payload"])
                pcm16, _ = audioop.ratecv(audioop.ulaw2lin(ulaw, 2), 2, 1, 8000, 16000, None)
                to_model.put_nowait(pcm16)
            elif event == "stop":
                break
    except WebSocketDisconnect:
        pass
    finally:
        session.cancel()
        await asyncio.gather(session, return_exceptions=True)
