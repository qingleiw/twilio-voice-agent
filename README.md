# twilio-voice-agent

A phone number that a model answers. Twilio carries the call, Gemini Live holds the conversation,
and a handful of tools do the only things that touch anything real — look up opening hours, offer
times that are actually free, take a booking, put the caller through to a person.

It is small on purpose: five files, no framework beyond FastAPI, and a stand-in business you
replace with whatever system the bookings really live in.

```
caller ──phone──▶ Twilio ──WebSocket──▶ app.py ──▶ agent.py ──▶ Gemini Live
                   mu-law 8 kHz          resample    tools.py ──▶ shop.py
```

## Why speech in and speech out are the same model

The usual build is three services: speech-to-text, then an LLM, then text-to-speech. Each adds its
own delay, and the middle one only ever sees a transcript, so tone, hesitation and interruption
are gone before it reads a word. Here the model takes the caller's audio directly and answers in
audio, which removes two network hops from every turn and keeps what the transcript would have
thrown away.

What that leaves you to get right is the plumbing: Twilio speaks 8 kHz mu-law, the model wants
16 kHz PCM in and gives 24 kHz PCM back, and when the caller interrupts, the audio already queued
at Twilio has to be dropped or the assistant talks over them for another second and a half.

## Running it

```bash
python -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env            # GEMINI_API_KEY, TWILIO_AUTH_TOKEN, PUBLIC_HOST
uvicorn app:app --port 8000
```

Twilio needs to reach it over HTTPS. For a number you already own, point its voice webhook at
`https://<your host>/voice`, method POST. For local work, a tunnel is enough:

```bash
cloudflared tunnel --url http://localhost:8000
```

`PUBLIC_HOST` has to match the hostname Twilio calls, exactly — the signature is computed over the
full URL, so behind a proxy the request's own `Host` header is not good enough.

## The rules live in code, not in the prompt

A prompt is a request. These are not:

- **A time that was never offered cannot be booked.** The model occasionally confirms a slot it
  invented; `tools.book` checks the diary again and makes it ask properly instead.
- **A phone number the caller never said is dropped.** Asked for a number, a model will sometimes
  produce a plausible one. The digits have to appear in the transcript of what the caller actually
  said, or the caller ID is used instead.
- **No transfer to a number the business did not configure.** Without `RECEPTION_NUMBER` the
  assistant says there is no one free rather than inventing somewhere to send them.
- **Opening hours and availability come from `shop.py`**, never from the model's own idea of them.

`tests/test_tools.py` covers each of those. They run without a network or an API key:

```bash
python -m pytest tests -q
```

## Replacing the stand-in business

`shop.py` keeps a diary in memory and is the only file that knows anything about the business.
Four functions — `hours`, `free_times`, `book`, `bookings` — are the whole interface. Point them
at a real booking system, a practice management database or a CRM and nothing above has to change.

## Configuration

| | |
|---|---|
| `GEMINI_API_KEY` | from Google AI Studio |
| `GEMINI_MODEL` | defaults to `gemini-2.0-flash-live-001` |
| `GEMINI_VOICE` | `Kore`, `Puck`, `Charon`, `Fenrir`, `Aoede` |
| `TWILIO_AUTH_TOKEN` | from the Twilio console; webhook signatures are checked against it |
| `TWILIO_VERIFY` | `0` turns signature checking off — for local testing only |
| `PUBLIC_HOST` | the hostname Twilio calls, e.g. `voice.example.com` |
| `RECEPTION_NUMBER` | where `transfer_call` sends people; unset means transfers are refused |
| `BUSINESS_NAME`, `GREETING` | what the caller hears |

## Licence

MIT.
