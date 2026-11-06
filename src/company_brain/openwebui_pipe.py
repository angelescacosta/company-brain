"""
title: Company Brain
description: Answers and summaries from the consultancy's documents, one client per chat.
version: 0.1.0
"""

# This file is installed into Open WebUI as a Pipe Function (T29) and runs inside
# Open WebUI's own Python, so it uses the standard library and pydantic only.
# It never streams: every reply has already passed the assistant's output gate.

from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel

MODES = {
    "answer": "Company Brain",
    "summarise": "Company Brain: summarise a document",
    "draft": "Company Brain: draft a proposal",
}
WHICH_CLIENT = "Which client? Reply with the number or the name:"
NO_CHAT = "Please start a new chat from the sidebar, then ask again."
TIMEOUT = "The assistant took too long to answer. Please try again, or ask a shorter question."
NOT_RUNNING = "The assistant is not running. Double-click Start AI, wait a minute, and ask again."
WRONG_CLIENT = (
    "This chat belongs to another client, so it cannot be used for this request. "
    "Start a new chat to work on a different client."
)
GENERIC = "Something went wrong and nothing was shown. Please try again; Check health shows why."


class Pipe:
    class Valves(BaseModel):
        backend_url: str = "http://127.0.0.1:8765"
        token: str = ""
        timeout_seconds: int = 300

    def __init__(self) -> None:
        self.type = "manifold"
        self.valves = self.Valves()

    def pipes(self) -> list[dict[str, str]]:
        return [{"id": mode, "name": name} for mode, name in MODES.items()]

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310 - fixed local address from the valves
            self.valves.backend_url.rstrip("/") + path,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Company-Brain-Token": self.valves.token,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(  # noqa: S310
                request, timeout=self.valves.timeout_seconds
            ) as response:
                result: dict[str, Any] = json.loads(response.read())
                return result
        except urllib.error.HTTPError as error:
            if error.code == 403:
                raise PermissionError("binding") from error
            raise RuntimeError(f"status {error.code}") from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError from error
            raise ConnectionRefusedError from error

    async def _call(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._post, path, payload)

    @staticmethod
    def _choose(text: str, clients: list[dict[str, str]]) -> str | None:
        wanted = text.strip().rstrip(".").casefold()
        if wanted.isdigit() and 1 <= int(wanted) <= len(clients):
            return clients[int(wanted) - 1]["scope"]
        for client in clients:
            if wanted in {
                client["name"].casefold(),
                client["scope"].casefold(),
                client["name"].replace(" (fictional)", "").casefold(),
            }:
                return client["scope"]
        return None

    async def pipe(
        self,
        body: dict[str, Any],
        __metadata__: dict[str, Any] | None = None,
        __user__: dict[str, Any] | None = None,
        __event_emitter__: Any = None,
    ) -> str:
        chat_id = str((__metadata__ or {}).get("chat_id") or "")
        if not chat_id:
            return NO_CHAT
        mode = str(body.get("model", "")).rsplit(".", 1)[-1]
        messages = [m for m in body.get("messages", []) if m.get("role") in ("user", "assistant")]
        message = str(messages[-1].get("content", "")) if messages else ""
        notice = ""
        try:
            state = await self._call("/chat/state", {"chat_id": chat_id})
            client = state.get("client")
            if not client:
                clients = state.get("clients", [])
                chosen = self._choose(message, clients)
                if chosen is None:
                    listing = "\n".join(f"{n}. {c['name']}" for n, c in enumerate(clients, 1))
                    return f"{WHICH_CLIENT}\n{listing}"
                await self._call("/chats", {"chat_id": chat_id, "client": chosen})
                name = next(c["name"] for c in clients if c["scope"] == chosen)
                # The chat's first message, if it was a question rather than a client, is
                # answered now, so it does not have to be typed again.
                first = [m for m in messages if m["role"] == "user"][0]
                question = str(first.get("content", ""))
                if first is messages[-1] or self._choose(question, clients) is not None:
                    return f"This chat is now about {name}. Ask your question."
                client, message, messages = chosen, question, [first]
                notice = f"This chat is now about {name}. Your question was:\n> {question}\n\n"
            history = [
                {"role": m["role"], "content": str(m.get("content", ""))} for m in messages[:-1]
            ][-6:]
            reply = await self._call(
                "/ask",
                {
                    "chat_id": chat_id,
                    "client": client,
                    "mode": mode,
                    "message": message,
                    "history": history,
                },
            )
            if __event_emitter__ is not None:
                for source in reply.get("sources", []):
                    where = f", {source['location']}" if source.get("location") else ""
                    await __event_emitter__(
                        {
                            "type": "source",
                            "data": {
                                "source": {"name": f"[{source['n']}] {source['name']}{where}"},
                                "document": [str(source.get("excerpt", ""))],
                                "metadata": [{"source": f"{source['name']}{where}"}],
                            },
                        }
                    )
            text = notice + str(reply.get("text", GENERIC))
            links = reply.get("downloads") or []
            if links:
                text += "\n\nDownload: " + " · ".join(f"[{d['label']}]({d['url']})" for d in links)
            return text
        except PermissionError:
            return WRONG_CLIENT
        except TimeoutError:
            return TIMEOUT
        except ConnectionRefusedError:
            return NOT_RUNNING
        except Exception:  # noqa: BLE001 - the chat never shows a stack trace
            return GENERIC
