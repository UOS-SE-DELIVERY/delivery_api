#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sse_order_flow_test.py

목표:
  1) 직원 세션(S_STAFF)으로 /api/staff/sse/orders 에 먼저 연결
  2) 별도 고객 세션(S_CUST)으로 주문 1건 생성
  3) SSE에서 새 주문 이벤트가 실제로 들어오는지 확인

가정:
  - APPEND_SLASH=False
  - /api/staff/login 은 HttpOnly 'access' 쿠키 세팅
  - /api/staff/sse/orders 는 접속 시 'ready', 'bootstrap' 이벤트를 먼저 보냄
  - 주문 생성 시 status=pending 이 기본값
"""

import time, json, threading, sys
from queue import Queue, Empty
from typing import Optional, Tuple, Dict, Any, Iterable
import requests

# =============== 설정 ===============
BASE_URL   = "http://localhost:8000".rstrip("/")
API_PREFIX = "/api"
TIMEOUT    = 30

ACCOUNTS = f"{BASE_URL}{API_PREFIX}/auth"
CATALOG  = f"{BASE_URL}{API_PREFIX}/catalog"
ORDERS   = f"{BASE_URL}{API_PREFIX}/orders"
STAFF    = f"{BASE_URL}{API_PREFIX}/staff"

STAFF_USER = "boss"
STAFF_PASS = "1234"

DINNER_CODE  = "valentine"
DINNER_STYLE = "simple"

SSE_PARAMS = {
    "status": "pending",   # 연결 이후 pending 주문만 구독
    # "since": "...",      # 필요시 ISO8601
    # "limit": "20",
}

SSE_CONNECT_WAIT = 8      # 첫 이벤트(ready/bootstrap) 기다리는 최대 초
SSE_EVENT_WAIT   = 20     # 주문 생성 후 새 이벤트 대기 초

# ====================================

def fail(msg: str):
    print(f"\n[FAIL] {msg}")
    sys.exit(2)

def call(sess: requests.Session, method: str, url: str, expect: Iterable[int] | int = (200,), **kw) -> requests.Response:
    exp = (expect,) if isinstance(expect, int) else tuple(expect)
    kw.setdefault("timeout", TIMEOUT)
    r = sess.request(method, url, **kw)
    if r.status_code not in exp:
        print(f"\n=== ERROR RESP: {method} {url} ===")
        print("HTTP", r.status_code)
        try:
            print(r.json())
        except Exception:
            print((r.text or "")[:1000])
        fail(f"{method} {url} -> {r.status_code} (expect {exp})")
    return r

def _parse_sse_frame(frame: str) -> Tuple[Optional[str], Optional[dict]]:
    event, data_lines = None, []
    for ln in frame.splitlines():
        if ln.startswith("event:"):
            event = ln[len("event:"):].strip()
        elif ln.startswith("data:"):
            data_lines.append(ln[len("data:"):].strip())
    if not data_lines:
        return event, None
    data_str = "\n".join(data_lines)
    try:
        obj = json.loads(data_str)
    except Exception:
        obj = {"raw": data_str}
    return event, obj

def sse_reader(sess: requests.Session, url: str, params: dict, out_q: Queue, stop_evt: threading.Event):
    try:
        with sess.get(url, params=params, stream=True, timeout=max(TIMEOUT, 60)) as resp:
            if resp.status_code != 200:
                out_q.put(("error", {"status": resp.status_code, "text": resp.text}))
                return
            buf = ""
            for raw in resp.iter_lines(decode_unicode=True):
                if stop_evt.is_set():
                    break
                if raw is None:
                    continue
                line = raw.rstrip("\r")
                if not line:
                    ev, data = _parse_sse_frame(buf)
                    if data is not None:
                        out_q.put((ev or "message", data))
                    buf = ""
                else:
                    buf += line + "\n"
    except requests.RequestException as e:
        out_q.put(("error", {"exception": str(e)}))

def staff_login() -> requests.Session:
    s = requests.Session(); s.headers.update({"Accept": "application/json"})
    r = call(s, "POST", f"{STAFF}/login", json={"username": STAFF_USER, "password": STAFF_PASS})
    if "access" not in s.cookies:
        fail("스태프 로그인 후 'access' 쿠키가 없음")
    print("[OK] Staff 로그인 성공")
    return s

def customer_register_and_login() -> Tuple[requests.Session, int]:
    s = requests.Session(); s.headers.update({"Accept":"application/json"})
    suffix = int(time.time() * 1000) % 1_000_000
    username = f"tester_{suffix}"
    password = f"Aa1!ok_{suffix}"

    call(s, "POST", f"{ACCOUNTS}/register", expect=(200,201,409),
         json={"username": username, "password": password, "profile_consent": False})
    r = call(s, "POST", f"{ACCOUNTS}/login", json={"username": username, "password": password})
    # 토큰이 헤더로 온다면 Authorization 세팅, 아니라면 쿠키 사용
    try:
        tok = r.json().get("access") or r.json().get("token")
    except Exception:
        tok = None
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    else:
        if "access" not in s.cookies:
            fail("고객 로그인 토큰/쿠키를 확인할 수 없음")

    # customer_id 가져오기 (이 엔드포인트는 슬래시가 필요함)
    me = call(s, "GET", f"{ACCOUNTS}/me/").json()
    customer_id = (
        me.get("customer_id")
        or (me.get("customer") or {}).get("id")
        or (me.get("data") or {}).get("customer_id")
    )
    if not customer_id:
        fail("customer_id를 /api/auth/me/에서 찾을 수 없음")
    print(f"[OK] Customer 로그인 성공 (customer_id={customer_id})")
    return s, int(customer_id)

def select_dinner_option_ids(sess: requests.Session, code: str) -> list:
    r = call(sess, "GET", f"{CATALOG}/dinners/{code}")
    detail = r.json()
    groups = detail.get("option_groups") or detail.get("dinner_option_groups") or []
    out = []
    for g in groups:
        options = g.get("options") or []
        if not options:
            continue
        chosen = next((o for o in options if o.get("default") is True), options[0])
        opt_id = chosen.get("option_id") or chosen.get("code") or chosen.get("id")
        if isinstance(opt_id, str) and opt_id.isdigit():
            opt_id = int(opt_id)
        out.append(opt_id)
    return out

def create_order_after_sse(sess_cust: requests.Session, customer_id: int) -> int:
    # 프리뷰
    opt_ids = select_dinner_option_ids(sess_cust, DINNER_CODE)
    preview_payload = {
        "order_source": "GUI",
        "customer_id": customer_id,
        "dinner": {
            "code": DINNER_CODE,
            "style": DINNER_STYLE,
            "quantity": 1,
            "dinner_options": opt_ids
        },
        "items": [],
        "coupons": []
    }
    call(sess_cust, "POST", f"{ORDERS}/price/preview", expect=(200,), json=preview_payload)

    # 생성(주소 스냅샷 포함)
    create_payload = {
        **preview_payload,
        "receiver_name": "홍길동",
        "receiver_phone": "010-1234-5678",
        "delivery_address": "서울시 임시로 123",
        "place_label": "기본",
    }
    r = call(sess_cust, "POST", f"{ORDERS}/", expect=(201,200), json=create_payload)
    body = r.json()
    oid = body.get("id") or body.get("order_id") or body.get("pk")
    if not oid:
        fail("주문 생성 응답에 id 없음")
    print(f"[OK] 주문 생성 완료 (order_id={oid})")
    return int(oid)

def main():
    # 1) 세션 분리
    S_STAFF = staff_login()
    S_CUST, customer_id = customer_register_and_login()

    # 2) SSE 먼저 연결
    q: Queue = Queue()
    stop_evt = threading.Event()
    t = threading.Thread(
        target=sse_reader,
        args=(S_STAFF, f"{STAFF}/sse/orders", dict(SSE_PARAMS), q, stop_evt),
        daemon=True
    )
    t.start()
    print(f"[i] SSE 연결 시도 → {STAFF}/sse/orders params={SSE_PARAMS}")

    # 2-1) 핸드셰이크 이벤트 대기
    try:
        ev, data = q.get(timeout=SSE_CONNECT_WAIT)
        print(f"=== SSE 첫 이벤트 ===\n{ {'event': ev, 'data': data} }")
    except Empty:
        stop_evt.set(); t.join(timeout=2)
        fail("SSE 첫 이벤트 타임아웃(ready/bootstrap 없음)")

    # 3) SSE가 열린 상태에서 주문 하나 생성
    new_oid = create_order_after_sse(S_CUST, customer_id)

    # 4) 새 주문 이벤트 대기
    got = None
    deadline = time.time() + SSE_EVENT_WAIT
    while time.time() < deadline:
        try:
            ev, data = q.get(timeout=1.0)
            print(f"[SSE] {ev}: {data}")
            # 이벤트 페이로드 형태에 따라 매칭(아래는 넓게 커버)
            payload = data or {}
            order_id = (
                payload.get("order_id")
                or payload.get("id")
                or (payload.get("order") or {}).get("id")
            )
            if order_id and int(order_id) == new_oid:
                got = (ev, payload)
                break
        except Empty:
            pass

    # 5) 종료/결과
    stop_evt.set(); t.join(timeout=2)
    if not got:
        fail(f"SSE에서 신규 주문 이벤트를 못 받음 (order_id={new_oid}, {SSE_EVENT_WAIT}s 대기)")
    print("\n 성공: SSE가 신규 주문 이벤트를 수신했습니다.")
    print(f"   event={got[0]} payload={got[1]}")

if __name__ == "__main__":
    main()
