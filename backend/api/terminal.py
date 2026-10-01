"""Authenticated PTY bridge. The shell is opened only after the session is valid."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket

from backend.auth.sessions import COOKIE_NAME, user_from_token
from backend.services.terminal import ShellSession, parse_client_message
from backend.utils.security import origin_allowed
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/terminal")
async def terminal_socket(websocket: WebSocket) -> None:
    if not origin_allowed(websocket.headers):
        await websocket.close(code=4403)
        return
    user = user_from_token(websocket.cookies.get(COOKIE_NAME), websocket.app.state.session_secret)
    if user is None:
        await websocket.close(code=4401)
        return
    slots = websocket.app.state.terminal_slots
    if not slots.acquire(user.id):
        await websocket.close(code=4429)
        return

    shell: ShellSession | None = None
    try:
        await websocket.accept()
        factory = getattr(websocket.app.state, "shell_factory", None)
        shell = factory() if factory is not None else _default_shell()
        await _bridge(websocket, shell)
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("Terminal session ended with an error")
        try:
            await websocket.close(code=1011)
        except Exception:
            return
    finally:
        if shell is not None:
            await asyncio.to_thread(shell.close)
        slots.release(user.id)


def _default_shell() -> ShellSession:
    from backend.services.terminal import open_shell

    return open_shell()


async def _bridge(websocket: WebSocket, shell: ShellSession) -> None:
    async def pump_output() -> None:
        while True:
            chunk = await asyncio.to_thread(shell.read, 0.25)
            if chunk is None:
                try:
                    await websocket.send_text(json.dumps({"type": "exit"}))
                except Exception:
                    return
                return
            if chunk:
                await websocket.send_bytes(chunk)

    async def pump_input() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            raw = message.get("bytes")
            try:
                if isinstance(text, str):
                    payload = parse_client_message(text)
                    if payload["type"] == "input":
                        data = str(payload["data"]).encode("utf-8")
                        await asyncio.to_thread(shell.write, data)
                    else:
                        await asyncio.to_thread(
                            shell.resize,
                            int(payload["rows"]),
                            int(payload["cols"]),
                        )
                elif isinstance(raw, (bytes, bytearray)):
                    if len(raw) > 65536:
                        continue
                    await asyncio.to_thread(shell.write, bytes(raw))
            except ValueError:
                await websocket.send_text(
                    json.dumps({"type": "error", "message": "Invalid terminal message"})
                )

    output_task = asyncio.create_task(pump_output())
    input_task = asyncio.create_task(pump_input())
    done, pending = await asyncio.wait(
        {output_task, input_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        if task.cancelled():
            continue
        error = task.exception()
        if error is not None and not isinstance(error, WebSocketDisconnect):
            raise error
