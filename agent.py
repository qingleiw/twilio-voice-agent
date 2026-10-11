"""The conversation: one Gemini Live session, with tools it can call.

Speech in and speech out are the same model, so there is no transcription step between the caller
speaking and the model understanding, and no text-to-speech step after. That is most of where the
latency goes in a pipeline built from three separate services.

The caller's audio is pushed in as it arrives. What comes back is audio, transcripts of both
sides, and tool calls; the tool call is the only part that touches anything real.
"""
import asyncio
import logging
import os

from google import genai
from google.genai import types

import tools

log = logging.getLogger("agent")

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-live-001")
VOICE = os.getenv("GEMINI_VOICE", "Kore")
GREETING = os.getenv("GREETING", "Thanks for calling. How can I help?")

SYSTEM = f"""You answer the telephone for a small business, by voice.

- Speak the way a person on the phone speaks: short sentences, one question at a time, then wait.
- Open with exactly: "{GREETING}"
- Use the tools for anything factual. Never invent an opening time, a price or an availability.
- If you cannot help, say so plainly and offer to put them through to someone who can.
- When the caller has what they need, say goodbye and call end_call. Say nothing after that, and
  never describe what is happening to the call itself.
"""


def live_config():
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM,
        speech_config={"voice_config": {"prebuilt_voice_config": {"voice_name": VOICE}}},
        tools=tools.SCHEMA,
        input_audio_transcription={},
        output_audio_transcription={},
    )


async def run(audio_in: asyncio.Queue, send_audio, on_interrupt, finish, state: dict):
    """One call, from the greeting to the hang-up.

    `audio_in` is fed by the transport, `send_audio` plays a chunk to the caller, `on_interrupt`
    clears whatever is still queued, and `finish` records how the call should end.
    """
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    async with client.aio.live.connect(model=MODEL, config=live_config()) as session:

        async def upstream():
            while True:
                await session.send_realtime_input(
                    audio=types.Blob(data=await audio_in.get(), mime_type="audio/pcm;rate=16000"))

        pump = asyncio.create_task(upstream())
        try:
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=f'Say exactly: "{GREETING}"')]),
                turn_complete=True)

            async for msg in session.receive():
                sc = msg.server_content
                if sc and sc.interrupted:
                    await on_interrupt()
                if msg.data:
                    await send_audio(msg.data)
                if sc and sc.input_transcription and sc.input_transcription.text:
                    log.info("caller: %s", sc.input_transcription.text)
                if sc and sc.output_transcription and sc.output_transcription.text:
                    log.info("agent:  %s", sc.output_transcription.text)
                if msg.tool_call:
                    replies = []
                    for call in msg.tool_call.function_calls or []:
                        args = dict(call.args or {})
                        result = tools.run(call.name, args, state, finish)
                        log.info("tool %s(%s) -> %s", call.name, args, result)
                        replies.append(types.FunctionResponse(id=call.id, name=call.name, response=result))
                    await session.send_tool_response(function_responses=replies)
                    if state.get("closing"):
                        return
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
