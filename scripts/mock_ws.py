"""LLM'siz sahte backend (port 8001) — K1/K2 için uçtan uca test sunucusu.

Protokol ORTAK BAĞLAM §6 ile birebir aynıdır; amaç panelin ve extension'ın
gerçek backend'i beklemeden akışı görebilmesidir:

    user_msg  ->  (fixture trace/tool_call/need_confirm satırları)
                  ->  gerçek bir browser.snapshot tool_call
                  ->  tek bir final

LLM anahtarı, ağ erişimi ve veritabanı kullanılmaz.  Durum bağlantı başına
tutulur (global mutable state yok).

Çalıştırma:  python scripts/mock_ws.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)

HOST = "0.0.0.0"
PORT = 8001

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "shared" / "fixtures" / "session_example.jsonl"

#: Fixture satırları arasında panelin nefes alması için küçük bekleme.
FIXTURE_DELAY_S = float(os.environ.get("MOCK_FIXTURE_DELAY_S", "0.15"))
#: Extension tool_result döndürmezse akışı yine de bitiren süre.
SNAPSHOT_WAIT_S = float(os.environ.get("MOCK_SNAPSHOT_WAIT_S", "20.0"))

SendFn = Callable[[dict[str, Any]], Awaitable[None]]


def _new_call_id() -> str:
    """Same shape as backend.bridge.new_call_id()."""

    return f"c_{secrets.token_hex(3)}"


def _new_request_id() -> str:
    return f"r_{secrets.token_hex(3)}"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Fixture readers
# ---------------------------------------------------------------------------
def load_fixture_events() -> list[dict[str, Any]]:
    """Read the shared fixture; a missing/broken file is not fatal."""

    try:
        raw = FIXTURE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.info("Fixture yok (%s), kendi minimal trace'lerim üretilecek.", FIXTURE_PATH)
        return []
    except OSError as exc:
        logger.warning("Fixture okunamadı (%s): %s", FIXTURE_PATH, exc)
        return []

    events: list[dict[str, Any]] = []
    for number, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.warning("Fixture %s satır %d bozuk, atlandı: %s", FIXTURE_PATH.name, number, exc)
            continue
        if isinstance(payload, dict) and payload.get("type"):
            events.append(payload)
        else:
            logger.warning("Fixture satır %d bir olay değil, atlandı.", number)
    logger.info("Fixture'dan %d olay yüklendi (%s).", len(events), FIXTURE_PATH.name)
    return events


def _trace_step(
    agent: str,
    decision: str,
    why: str,
    *,
    evidence: list[str] | None = None,
    confidence: float | None = None,
    tool: dict[str, Any] | None = None,
    verdict: str | None = None,
) -> dict[str, Any]:
    """Minimal §4 şemasına uyan trace adımı (fixture yoksa kullanılır)."""

    return {
        "agent": agent,
        "decision": decision,
        "why": why,
        "evidence": list(evidence or []),
        "confidence": confidence,
        "tool": tool,
        "scores": None,
        "verdict": verdict,
        "latency_ms": 0,
        "tokens": None,
        "ts": _now_iso(),
    }


def fallback_events(user_text: str) -> list[dict[str, Any]]:
    """Fixture bulunamadığında üretilen sabit, açıklanabilir akış."""

    return [
        {
            "type": "trace",
            "step": _trace_step(
                "router",
                "browser_task rotası seçildi",
                "Kullanıcı sayfa üzerinde bir işlem istedi.",
                confidence=0.82,
            ),
        },
        {
            "type": "trace",
            "step": _trace_step(
                "planner",
                "2 adımlı plan",
                "Önce sayfanın durumu okunacak, sonra sonuç özetlenecek.",
                evidence=[f"kullanıcı: {user_text[:80]}"],
            ),
        },
        {
            "type": "trace",
            "step": _trace_step(
                "executor",
                "sayfa anlık görüntüsü alınıyor",
                "Tıklama hedeflerini görebilmek için snapshot gerekiyor.",
                tool={"name": "browser.snapshot", "args": {}},
            ),
        },
        {
            "type": "trace",
            "step": _trace_step(
                "judge",
                "onay istendi",
                "Riskli etiketli bir öğeye tıklanacak, kullanıcı onayı şart.",
                verdict="ask_user",
            ),
        },
        {
            "type": "need_confirm",
            "call_id": _new_call_id(),
            "summary": "“Sepete Ekle” öğesine tıklanmasını onaylıyor musun?",
            "tool": "browser.click",
            "args": {"ref": 12},
        },
    ]


def _with_request_id(event: dict[str, Any], request_id: str) -> dict[str, Any]:
    """Replace the fixture's request id with the incoming one (frame is copied)."""

    frame = dict(event)
    frame["request_id"] = request_id
    if frame.get("type") == "need_confirm" and not frame.get("call_id"):
        frame["call_id"] = _new_call_id()
    return frame


# ---------------------------------------------------------------------------
# Per-connection state
# ---------------------------------------------------------------------------
@dataclass
class ConnectionState:
    """Everything one socket knows; never shared between connections."""

    send: SendFn
    task: asyncio.Task[None] | None = None
    request_id: str | None = None
    source: str = "text"
    pending_calls: dict[str, str] = field(default_factory=dict)  # call_id -> tool name
    confirm_calls: dict[str, bool] = field(default_factory=dict)  # call_id -> approved
    snapshot_call_id: str | None = None
    done_event: asyncio.Event | None = None

    def begin_request(self, request_id: str, source: str) -> None:
        self.request_id = request_id
        self.source = source
        self.pending_calls.clear()
        self.confirm_calls.clear()
        self.snapshot_call_id = None
        self.done_event = asyncio.Event()

    def finish_request(self) -> None:
        self.request_id = None
        self.snapshot_call_id = None
        self.done_event = None

    async def cancel_task(self) -> None:
        task, self.task = self.task, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("İstek görevi kapanırken hata: %s", exc)


# ---------------------------------------------------------------------------
# Frame handlers
# ---------------------------------------------------------------------------
async def _send_frame(state: ConnectionState, frame: dict[str, Any]) -> None:
    try:
        await state.send(frame)
    except (WebSocketDisconnect, RuntimeError) as exc:
        # The reader loop owns disconnect handling; a dead socket is expected.
        logger.info("Frame gönderilemedi (%s): %s", frame.get("type"), exc)
        raise


async def _run_request(state: ConnectionState, request_id: str, user_text: str) -> None:
    """Fixture satırları -> browser.snapshot tool_call -> final.

    Fixture'daki `final` olayları **atlanır**: fixture iki ayrı senaryonun tam
    kaydını taşıdığı için (senaryo 3 ve 4) olduğu gibi oynatılırsa tek bir
    `user_msg` isteğine üç `final` frame'i düşer ve panel ilk `final`'da akışı
    bitirir.  Canlı akışın tek `final`'ını bu fonksiyon üretir.
    """

    try:
        events = load_fixture_events() or fallback_events(user_text)
        for event in events:
            if event.get("type") == "final":
                continue
            await _send_frame(state, _with_request_id(event, request_id))
            if FIXTURE_DELAY_S > 0:
                await asyncio.sleep(FIXTURE_DELAY_S)

        call_id = _new_call_id()
        state.snapshot_call_id = call_id
        state.pending_calls[call_id] = "browser.snapshot"
        logger.info("GTÖ: browser.snapshot gönderiliyor (call_id=%s)", call_id)
        await _send_frame(
            state,
            {
                "type": "tool_call",
                "call_id": call_id,
                "request_id": request_id,
                "name": "browser.snapshot",
                # Args must satisfy the registered `browser.snapshot` schema
                # (object, additionalProperties=false, no `max_elements`).
                "args": {},
            },
        )

        approved = await _wait_for_result(state)
        await _send_frame(state, _final_frame(state, request_id, approved))
    except asyncio.CancelledError:
        logger.info("İstek iptal edildi: request_id=%s", request_id)
        raise
    except (WebSocketDisconnect, RuntimeError) as exc:
        logger.info("Bağlantı koptu, akış bırakıldı: %s", exc)
    except Exception as exc:  # never take the socket down
        logger.exception("Akış hatası: %s", exc)
        try:
            await state.send(
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": f"Sahte sunucu hatası: {exc}",
                }
            )
        except Exception:
            logger.info("Hata frame'i de gönderilemedi.")


async def _wait_for_result(state: ConnectionState) -> bool | None:
    """Wait for the snapshot tool_result or a confirm; None means timeout."""

    event = state.done_event
    if event is None:
        return None
    try:
        await asyncio.wait_for(event.wait(), timeout=SNAPSHOT_WAIT_S)
    except asyncio.TimeoutError:
        logger.info("tool_result/confirm gelmedi (%.0f sn), akış 'final' ile kapatılıyor.", SNAPSHOT_WAIT_S)
        return None
    if any(state.confirm_calls.values()):
        return True
    if state.confirm_calls:
        return False
    return None


def _final_frame(state: ConnectionState, request_id: str, approved: bool | None) -> dict[str, Any]:
    if approved is True:
        spoken = "İşlem onaylandı ve tamamlandı."
        note = "İşlem kullanıcı tarafından onaylandı ve tamamlandı."
    elif approved is False:
        spoken = "İşlemi iptal ettiniz, hiçbir şeye tıklamadım."
        note = "Kullanıcı onayı reddedildiği için tıklama yapılmadı."
    else:
        spoken = "Sayfanın durumunu okudum, özet hazır."
        note = "Sayfa okundu; bekleyen işlem yok."
    return {
        "type": "final",
        "request_id": request_id,
        "answer": (
            f"**Sahte backend yanıtı**\n\n{note}\n\n"
            f"- Kaynak: panel/extension uçtan uca testi (LLM yok)\n"
            f"- Mod: {state.source}"
        ),
        "spoken": spoken,
        "citations": [
            {"title": "Sahte test akışı", "url": "https://example.com/mock", "ref": 1},
        ],
    }


async def _handle_hello(state: ConnectionState, frame: dict[str, Any]) -> None:
    client = frame.get("client") or frame.get("version") or "bilinmiyor"
    logger.info("hello alındı: client=%s", client)


async def _handle_user_msg(state: ConnectionState, frame: dict[str, Any]) -> None:
    request_id = str(frame.get("id") or _new_request_id())
    user_text = str(frame.get("text") or "")
    source = str(frame.get("source") or "text")
    logger.info("user_msg alındı: request_id=%s source=%s metin=%r", request_id, source, user_text[:120])

    await state.cancel_task()  # bağlantı başına tek istek kuralı
    state.begin_request(request_id, source)
    state.task = asyncio.create_task(_run_request(state, request_id, user_text))


async def _handle_tool_result(state: ConnectionState, frame: dict[str, Any]) -> None:
    call_id = str(frame.get("call_id") or "")
    ok = bool(frame.get("ok"))
    data = frame.get("data")
    error = frame.get("error")
    logger.info("tool_result alındı: call_id=%s ok=%s", call_id, ok)

    if ok:
        summary = _summarise_data(data)
        logger.info("  veri özeti: %s", summary)
    else:
        logger.info("  hata: %s", error or "(açıklama yok)")

    state.pending_calls.pop(call_id, None)
    if call_id == state.snapshot_call_id and state.done_event is not None:
        state.done_event.set()


def _summarise_data(data: Any) -> str:
    if not isinstance(data, dict):
        return repr(data)[:160]
    elements = data.get("elements")
    parts = [
        f"ref={data.get('ref')}" if data.get("ref") is not None else "",
        f"öğe_sayısı={len(elements)}" if isinstance(elements, list) else "",
        f"url={str(data.get('url'))[:80]}" if data.get("url") else "",
        f"başlık={str(data.get('title'))[:60]}" if data.get("title") else "",
        f"anahtarlar={','.join(sorted(data.keys()))}",
    ]
    return " ".join(p for p in parts if p)


async def _handle_confirm(state: ConnectionState, frame: dict[str, Any]) -> None:
    call_id = str(frame.get("call_id") or "")
    approved = bool(frame.get("approved"))
    logger.info("confirm alındı: call_id=%s approved=%s", call_id, approved)

    if call_id in state.confirm_calls:
        state.confirm_calls[call_id] = approved
        logger.info("  onay zaten bekleniyordu, güncellendi.")
    else:
        state.confirm_calls[call_id] = approved

    if state.done_event is not None:
        state.done_event.set()
        return
    # Onay, akış bittikten sonra geldiyse kısa bir kapanış frame'i gönder.
    message = "İşlem onaylandı." if approved else "İşlem onaylanmadı."
    await state.send({"type": "notify", "text": message, "spoken": message})


async def _handle_cancel(state: ConnectionState, frame: dict[str, Any]) -> None:
    request_id = state.request_id or frame.get("request_id")
    logger.info("cancel alındı: request_id=%s", request_id)
    await state.cancel_task()
    state.finish_request()
    await state.send(
        {
            "type": "error",
            "request_id": request_id,
            "message": "İstek iptal edildi.",
        }
    )


async def handle_frame(state: ConnectionState, frame: dict[str, Any]) -> None:
    """Route one client frame; unknown types become an error frame."""

    kind = str(frame.get("type") or "")
    if kind == "hello":
        await _handle_hello(state, frame)
        return
    if kind == "user_msg":
        await _handle_user_msg(state, frame)
        return
    if kind == "tool_result":
        await _handle_tool_result(state, frame)
        return
    if kind == "confirm":
        await _handle_confirm(state, frame)
        return
    if kind == "cancel":
        await _handle_cancel(state, frame)
        return

    logger.info("Bilinmeyen frame tipi: %r", kind)
    await state.send(
        {
            "type": "error",
            "request_id": state.request_id,
            "message": f"Bilinmeyen frame tipi: {kind or '(boş)'}",
        }
    )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Evren Mock Backend", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    async def send(frame: dict[str, Any]) -> None:
        if websocket.client_state is WebSocketState.CONNECTED:
            await websocket.send_json(frame)

    state = ConnectionState(send=send)
    logger.info("Yeni bağlantı: %s", websocket.client)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                logger.info("Bozuk JSON alındı, error frame dönülüyor.")
                await send(
                    {
                        "type": "error",
                        "request_id": state.request_id,
                        "message": "Geçersiz JSON.",
                    }
                )
                continue
            if not isinstance(frame, dict):
                await send(
                    {
                        "type": "error",
                        "request_id": state.request_id,
                        "message": "Frame bir JSON nesnesi olmalı.",
                    }
                )
                continue
            try:
                await handle_frame(state, frame)
            except (WebSocketDisconnect, RuntimeError) as exc:
                logger.info("Frame işlenirken bağlantı kapandı: %s", exc)
                break
            except Exception as exc:  # a bad frame must not kill the socket
                logger.exception("Frame işlenemedi: %s", exc)
                await send(
                    {
                        "type": "error",
                        "request_id": state.request_id,
                        "message": f"Frame işlenemedi: {exc}",
                    }
                )
    except WebSocketDisconnect:
        logger.info("Bağlantı kapandı: %s", websocket.client)
    except Exception as exc:  # pragma: no cover - transport level
        logger.warning("WebSocket hatası: %s", exc)
    finally:
        await state.cancel_task()


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logger.info("Sahte backend başlıyor: http://%s:%d (WS /ws, health /health)", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
