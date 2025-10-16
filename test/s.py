#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import threading
from queue import Queue, Empty
from typing import Iterable, Optional, Tuple

import requests

# ================== 환경 / 상수 ==================

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

STAFF    = f"{BASE_URL}/api/staff"
ACCOUNTS = f"{BASE_URL}/api/auth"
CATALOG  = f"{BASE_URL}/api/catalog"
ORDERS   = f"{BASE_URL}/api/orders"

STAFF_USER = "boss"
STAFF_PASS = "1234"

TIMEOUT = int(os.environ.get("TIMEOUT", "10"))

# SSE 파라미터 (필요 시 환경변수로 조정)
SSE_PARAMS = {
    "status": os.environ.get("SSE_STATUS", "pending"),
    "limit": int(os.environ.get("SSE_LIMIT", "20")),
}

# 타임아웃 설정
SSE_CONNECT_WAIT = int(os.environ.get("SSE_CONNECT_WAIT", "8"))     # 첫 프레임 대기
SSE_DIAG_WAIT    = int(os.environ.get("SSE_DIAG_WAIT", "15"))       # diagnostic 대기
SSE_EVENT_WAIT   = int(os.environ.get("SSE_EVENT_WAIT", "20"))      # 주문 후 이벤트 대기

# ================== 유틸 ==================

def fail(msg: str):
    print(f"\n[FAIL] {msg}")
    sys.exit(2)

def call(sess: requests.Session, method: str, url: str,
         expect: Iterable[int] | int = (200,), **kw) -> requests.Response:
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

# ================== SSE 리더 ==================

def sse_reader(sess: requests.Session, url: str, params: dict,
               out_q: Queue, stop_evt: threading.Event):
    """
    SSE 프레임을 읽어서 (event, data) 튜플로 out_q에 넣는다.
    - Accept 헤더를 명시하고
    - iter_lines(chunk_size=1)로 즉시 라인 분리
    """
    try:
        # 세션 기본 Accept는 application/json일 수 있으므로, 이 요청만 덮어씀
        headers = {"Accept": "text/event-stream"}
        with sess.get(url, params=params, headers=headers,
                      stream=True, timeout=max(TIMEOUT, 60)) as resp:
            if resp.status_code != 200:
                out_q.put(("error", {"status": resp.status_code, "text": resp.text}))
                return
            buf = ""
            for raw in resp.iter_lines(decode_unicode=True, chunk_size=1):
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

# ================== 인증/주문 ==================

def staff_login() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    call(s, "POST", f"{STAFF}/login", json={"username": "boss", "password": "1234"})
    if "access" not in s.cookies:
        fail("스태프 로그인 후 'access' 쿠키가 없음")
    print("[OK] Staff 로그인 성공")
    return s

def customer_register_and_login() -> Tuple[requests.Session, int]:
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    suffix = int(time.time() * 1000) % 1_000_000
    username = f"tester_{suffix}"
    password = f"Aa1!ok_{suffix}"

    # 회원가입(이미 있으면 409)
    call(s, "POST", f"{ACCOUNTS}/register", expect=(200, 201, 409),
         json={"username": username, "password": password, "profile_consent": False})
    r = call(s, "POST", f"{ACCOUNTS}/login", json={"username": username, "password": password})

    # 토큰이 바디로 오면 Authorization 헤더 세팅, 아니면 쿠키 사용
    try:
        tok = r.json().get("access") or r.json().get("token")
    except Exception:
        tok = None
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    else:
        if "access" not in s.cookies:
            fail("고객 로그인 토큰/쿠키를 확인할 수 없음")

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

def create_order_after_sse(sess_cust: requests.Session, customer_id: int,
                           dinner_code: str = os.environ.get("DINNER_CODE", "valentine"),
                           dinner_style: str = os.environ.get("DINNER_STYLE", "simple")) -> int:
    """
    1) 카탈로그 조회(옵션 id 추출은 가능하면)
    2) price/preview: 단수 'dinner' 스키마
    3) 주문 생성: 'dinner'(단수) + 평평한 수신자/결제 필드 (receiver_*, payment_*)
    """
    # 1) 카탈로그(옵션 id는 없어도 됨)
    try:
        r_cat = call(sess_cust, "GET", f"{CATALOG}/dinners/{dinner_code}")
        cat = r_cat.json()
        opt_ids = []
        for key in ("options", "dinner_options", "option_groups"):
            if isinstance(cat.get(key), list):
                for el in cat[key]:
                    _id = el.get("id")
                    if isinstance(_id, int):
                        opt_ids.append(_id)
        opt_ids = list(dict.fromkeys(opt_ids))
    except Exception:
        opt_ids = []

    # 2) price/preview (단수 'dinner')
    preview_payload = {
        "order_source": "GUI",
        "customer_id": customer_id,
        "dinner": {
            "code": dinner_code,
            "style": dinner_style,
            "quantity": 1,
            "dinner_options": opt_ids,
        },
        "items": [],
        "coupons": [],
    }
    print("[DBG] preview_payload =", json.dumps(preview_payload, ensure_ascii=False))
    call(sess_cust, "POST", f"{ORDERS}/price/preview", expect=200, json=preview_payload)
    print("[OK] 가격 프리뷰 성공")

    # 3) 주문 생성 (receiver/payment 중첩 금지, 필드 평평하게)
    order_payload = {
        "order_source": "GUI",
        "customer_id": customer_id,

        # ⬇️ DB 컬럼과 동일한 평평한 수신자/주소 필드
        "receiver_name": "홍길동",
        "receiver_phone": "010-0000-0000",
        "delivery_address": "서울시 테스트구 테스트로 123",
        "place_label": "기본",
        # geo_lat/geo_lng, address_meta는 선택

        # ⬇️ 결제도 평평하게
        "payment_token": "tok_test",
        "card_last4": "4242",

        # ⬇️ 단수 'dinner' 그대로
        "dinner": preview_payload["dinner"],

        "items": [],
        "coupons": [],
    }
    print("[DBG] order_payload =", json.dumps(order_payload, ensure_ascii=False))
    r = sess_cust.post(f"{ORDERS}/", json=order_payload, timeout=TIMEOUT)

    if r.status_code != 201:
        print("\n=== ERROR RESP: POST /api/orders/ ===")
        print("HTTP", r.status_code)
        try:
            print(r.json())
        except Exception:
            print((r.text or "")[:1000])
        fail("주문 생성 실패")

    try:
        body = r.json()
        oid = int(body.get("id") or body.get("order_id") or (body.get("order") or {}).get("id"))
    except Exception:
        print("응답 바디:", (r.text or "")[:500])
        fail("주문 생성 응답에서 id를 찾을 수 없음")

    print(f"[OK] 주문 생성 완료 (order_id={oid})")
    return oid


# ================== 메인 시나리오 ==================

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
        daemon=True,
    )
    t.start()
    print(f"[i] SSE 연결 시도 → {STAFF}/sse/orders params={SSE_PARAMS}")

    # 2-1) ready/bootstrap을 지나 'diagnostic'을 받을 때까지 대기
    first_ev = None
    diag_seen = False
    deadline = time.time() + max(SSE_CONNECT_WAIT, SSE_DIAG_WAIT)
    while time.time() < deadline:
        try:
            ev, data = q.get(timeout=1.0)
            if first_ev is None:
                first_ev = (ev, data)
                print(f"=== SSE 첫 이벤트 ===\n{{'event': '{ev}', 'data': {data}}}")
            # 핸드셰이크 이벤트들 그대로 보여주기
            if ev in ("bootstrap", "diagnostic", "error"):
                print(f"[SSE] {ev}: {data}")
            if ev == "diagnostic":
                diag_seen = True
                break
        except Empty:
            pass

    if not diag_seen:
        # 서버가 아직 diagnostic을 안 주는 상황이면 레이스 가능 → 경고만 남기고 진행
        print("[!] diagnostic 미수신: LISTEN 시작 타이밍이 늦을 수 있음(이벤트 누락 위험).")

    # 3) 주문 생성 (LISTEN 이후로 미루어 레이스 제거)
    new_oid = create_order_after_sse(S_CUST, customer_id)

    # 4) 새 주문 이벤트 대기
    got = None
    deadline = time.time() + SSE_EVENT_WAIT
    while time.time() < deadline:
        try:
            ev, data = q.get(timeout=1.0)
            print(f"[SSE] {ev}: {data}")
            payload = data or {}
            order_id = (
                payload.get("order_id")
                or payload.get("id")
                or (payload.get("order") or {}).get("id")
            )
            # event 명은 order_created/order_updated/혹은 채널명일 수 있으니 id만으로 매칭
            if order_id and int(order_id) == new_oid:
                got = (ev, payload)
                break
        except Empty:
            pass

    # 5) 종료/결과
    stop_evt.set()
    t.join(timeout=2)
    if not got:
        fail(f"SSE에서 신규 주문 이벤트를 못 받음 (order_id={new_oid}, {SSE_EVENT_WAIT}s 대기)")
    print("\n성공: SSE가 신규 주문 이벤트를 수신했습니다.")
    print(f"   event={got[0]} payload={got[1]}")

if __name__ == "__main__":
    main()
