# apps/staff/eventbus.py
from __future__ import annotations

import json
import logging
import re
import select
import time
import os
from typing import Any, Dict, Iterator, List, Optional

import psycopg
from psycopg import sql
from django.conf import settings

log = logging.getLogger(__name__)

# LISTEN 채널: settings에 없으면 기본값
CHANNELS: List[str] = list(getattr(settings, "ORDERS_NOTIFY_CHANNELS", ["orders_events"]))
# select() 타임아웃(초)
LISTEN_TIMEOUT: float = float(getattr(settings, "ORDERS_LISTEN_TIMEOUT", 15.0))


# ---------------- helpers ----------------

def _dsn() -> str:
    db = settings.DATABASES["default"]
    parts: List[str] = []
    if db.get("NAME"):
        parts.append(f"dbname={db['NAME']}")
    if db.get("USER"):
        parts.append(f"user={db['USER']}")
    if db.get("PASSWORD"):
        parts.append(f"password={db['PASSWORD']}")
    if db.get("HOST"):
        parts.append(f"host={db['HOST']}")
    if db.get("PORT"):
        parts.append(f"port={db['PORT']}")
    return " ".join(parts)


_CH_RE = re.compile(r"^[a-z0-9_]+$", re.I)

def _validate_channel(ch: str) -> str:
    if not _CH_RE.match(ch):
        raise ValueError(f"invalid LISTEN channel: {ch!r}")
    return ch


def _b2s(x: Any) -> Any:
    """bytes/bytearray → utf-8 문자열로 변환."""
    if isinstance(x, (bytes, bytearray)):
        return x.decode("utf-8", "replace")
    return x


def _jsonable(x: Any) -> Any:
    """json.dumps 가능한 형태로 딥 정규화(bytes → str)."""
    if isinstance(x, (bytes, bytearray)):
        return x.decode("utf-8", "replace")
    if isinstance(x, dict):
        return { _jsonable(k): _jsonable(v) for k, v in x.items() }
    if isinstance(x, (list, tuple, set)):
        return [ _jsonable(v) for v in x ]
    return x


def _drain_notifies(conn: psycopg.Connection) -> List[Any]:
    """
    알림 큐를 전부 비워서 반환.
    - psycopg3 고수준 큐(conn.notifies) 비움
    - libpq 저수준 큐(conn.pgconn.notifies())도 끝까지 비움
    - (구호환) conn.notifications 있으면 같이 비움
    """
    out: List[Any] = []

    # 1) 고수준 큐
    q = getattr(conn, "notifies", None)
    if q is not None:
        while True:
            try:
                out.append(q.get_nowait())
            except Exception:
                break
        # 여기서 return 하지 말 것!

    # 2) libpq 저수준 큐
    pg = getattr(conn, "pgconn", None)
    if pg is not None:
        while True:
            try:
                note = pg.notifies()  # 더 없으면 falsy
            except Exception:
                break
            if not note:
                break
            ch = _b2s(getattr(note, "channel", None) or getattr(note, "relname", None))
            payload = _b2s(getattr(note, "payload", None) or getattr(note, "extra", ""))
            be_pid = getattr(note, "be_pid", None)
            out.append(type("Notify", (), {"channel": ch, "payload": payload, "be_pid": be_pid})())

    # 3) 구호환
    notes = getattr(conn, "notifications", None)
    if notes:
        out.extend(list(notes))
        try:
            notes.clear()
        except Exception:
            pass

    return out


# ---------------- public API ----------------

def iter_order_notifications() -> Iterator[Dict[str, Any]]:
    dsn = _dsn()
    chans = [_validate_channel(c) for c in CHANNELS]
    backoff = 0.5

    while True:
        conn: Optional[psycopg.Connection] = None
        cur: Optional[psycopg.Cursor] = None
        try:
            conn = psycopg.connect(dsn, autocommit=True)
            cur = conn.cursor()

            for ch in chans:
                cur.execute(sql.SQL("LISTEN {}").format(sql.Identifier(ch)))

            host = getattr(conn.pgconn, "host", None) or "?"
            port = getattr(conn.pgconn, "port", None) or getattr(conn.pgconn, "socket", None)
            log.info(
                "SSE psycopg3 listening=%s on %s:%s db=%s user=%s",
                chans, _b2s(host), port,
                settings.DATABASES["default"].get("NAME"),
                settings.DATABASES["default"].get("USER"),
            )

            # diagnostic 1회 (bytes → str 정규화)
            diag = {
                "event": "diagnostic",
                "listening": [_b2s(c) for c in chans],
                "host": _b2s(host),
                "port": port,
                "db": settings.DATABASES["default"].get("NAME"),
                "user": settings.DATABASES["default"].get("USER"),
                "pid": os.getpid(),
                "notify_impl": "queue+libpq",
            }
            yield _jsonable(diag)

            # 소켓 대기 루프
            sock_fd = conn.pgconn.socket  # int fd
            while True:
                r, _, _ = select.select([sock_fd], [], [], LISTEN_TIMEOUT)
                if not r:
                    continue  # keep-alive

                # 입력 소비 → notify 큐 적재
                conn.pgconn.consume_input()

                # 큐 비우기
                for note in _drain_notifies(conn):
                    ch = _b2s(getattr(note, "channel", None)) or "message"
                    payload = _b2s(getattr(note, "payload", ""))

                    # JSON 파싱
                    try:
                        obj = json.loads(payload) if payload else {}
                    except Exception:
                        obj = {"raw": payload}

                    if isinstance(obj, dict):
                        # 1) 이미 event가 있으면 우선 (bytes → str)
                        if "event" in obj:
                            obj["event"] = _b2s(obj["event"])
                        else:
                            # 2) 구트리거 호환: op → event 매핑
                            op = obj.get("op")
                            mapping = {"INSERT": "order_created",
                                       "UPDATE": "order_updated",
                                       "DELETE": "order_deleted"}
                            if isinstance(op, str) and op in mapping:
                                obj["event"] = mapping[op]
                            else:
                                # 3) 그래도 없으면 채널명 사용
                                obj["event"] = ch

                        safe_obj = _jsonable(obj)  # 딥 정규화(bytes → str)
                    else:
                        safe_obj = {"event": ch, "raw": _jsonable(obj)}

                    log.info("SSE RECV %s: %s", safe_obj.get("event"), safe_obj)
                    yield safe_obj

        except Exception as e:
            log.warning("SSE loop error: %s (reconnecting...)", e)
        finally:
            if cur:
                try:
                    cur.close()
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        time.sleep(backoff)
        backoff = min(backoff * 2.0, 10.0)
