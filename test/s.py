#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import requests, time, json, threading, sys
from queue import Queue, Empty
from typing import Any, Dict, Iterable, Optional, Tuple

BASE_URL   = "http://localhost:8000".rstrip("/")
API_PREFIX = "/api"
ACCOUNTS   = f"{BASE_URL}{API_PREFIX}/auth"
CATALOG    = f"{BASE_URL}{API_PREFIX}/catalog"
ORDERS     = f"{BASE_URL}{API_PREFIX}/orders"
STAFF      = f"{BASE_URL}{API_PREFIX}/staff"
REQ_TIMEOUT = 30

S_STAFF = requests.Session(); S_STAFF.headers.update({"Accept": "application/json"})
S_CUST  = requests.Session();  S_CUST.headers.update({"Accept": "application/json"})

STAFF_CREDENTIALS = {"username": "owner", "password": "1234"}
DINNER_CODE, DINNER_STYLE = "valentine", "simple"

def fail(msg: str) -> None:
    print(f"\n[FAIL] {msg}"); sys.exit(2)

def call(sess: requests.Session, method: str, url: str,
         expect: Iterable[int] | int = (200,), add_slash_fallback: bool = True, **kw) -> requests.Response:
    exp = (expect,) if isinstance(expect, int) else tuple(expect)
    kw.setdefault("timeout", REQ_TIMEOUT)
    urls = [url] if not add_slash_fallback else [url, (url if url.endswith("/") else url + "/")]
    last = None
    for u in urls:
        try:
            r = sess.request(method, u, **kw); last = r
            if r.status_code in exp: return r
        except requests.RequestException as e:
            last = e
    if isinstance(last, requests.Response):
        try: body = last.json()
        except Exception: body = (last.text or "")[:800]
        fail(f"{method} {url} -> {last.status_code} (expect {exp}) body={body}")
    fail(f"{method} {url} request error: {last}")

def as_json(resp: requests.Response) -> Dict[str, Any]:
    try: return resp.json()
    except Exception: fail("JSON parse failed")

# ------------------ SSE ------------------
def _parse_sse_frame(frame: str) -> Tuple[Optional[str], Optional[Any]]:
    event, data_lines = None, []
    for ln in frame.splitlines():
        if ln.startswith("event:"): event = ln[6:].strip()
        elif ln.startswith("data:"): data_lines.append(ln[5:].strip())
    if not data_lines: return event, None
    data_str = "\n".join(data_lines)
    try: obj = json.loads(data_str)
    except Exception: obj = {"raw": data_str}
    return event, obj

def _sse_loop(sess: requests.Session, url: str, headers: Dict[str, str], params: Dict[str, Any],
              out_q: Queue, stop_evt: threading.Event):
    try:
        with sess.get(url, headers=headers or None, params=params or None, stream=True, timeout=max(REQ_TIMEOUT, 60)) as resp:
            if resp.status_code != 200:
                out_q.put(("error", {"status": resp.status_code, "text": (resp.text or "")[:300]})); return
            buf = ""
            for raw in resp.iter_lines(decode_unicode=True):
                if stop_evt.is_set(): break
                if raw is None: continue
                line = raw.rstrip("\r")
                if not line:
                    ev, data = _parse_sse_frame(buf)
                    if data is not None: out_q.put((ev or "message", data))
                    buf = ""
                else:
                    buf += line + "\n"
    except requests.RequestException as e:
        out_q.put(("error", {"exception": str(e)}))

def start_sse_with_retries() -> tuple[Queue, threading.Event, threading.Thread]:
    """
    - 세션 후보: S_STAFF(기존 헤더 유지) → 빈 헤더 세션(쿠키만 복사)
    - URL 후보: /orders, /orders/
    - params 후보: {}, format=api/json/event-stream
    - Accept 후보: (그대로), application/json, (제거), text/event-stream
    조합을 12초 동안 재시도해서 200이 뜨는 첫 조합으로 스트리밍 시작.
    """
    out_q, stop_evt = Queue(), threading.Event()
    url_candidates = [f"{STAFF}/sse/orders", f"{STAFF}/sse/orders/"]
    params_candidates = [{}, {"format":"api"}, {"format":"json"}, {"format":"event-stream"}]

    # 세션 1: S_STAFF(그대로), 세션 2: 쿠키만 복사한 깨끗한 세션
    sess2 = requests.Session(); sess2.cookies.update(S_STAFF.cookies.get_dict())
    sessions = [("S_STAFF", S_STAFF), ("SSE_CLEAN", sess2)]

    # Accept 후보 생성: 세션 기본값 유지 → 강제 application/json → 제거 → event-stream
    def header_variants(sess: requests.Session):
        base_accept = sess.headers.get("Accept")
        yield {"__desc__": f"default({base_accept})"}  # 변경 없음
        yield {"Accept": "application/json", "__desc__": "Accept: application/json"}
        yield {"__remove_accept__": True, "__desc__": "Accept: <removed>"}
        yield {"Accept": "text/event-stream", "__desc__": "Accept: text/event-stream"}

    deadline = time.time() + 12.0
    last_err = None

    while time.time() < deadline:
        for sess_name, sess in sessions:
            for u in url_candidates:
                for p in params_candidates:
                    for h in header_variants(sess):
                        # 실제 요청용 헤더 구성
                        hdr = {}
                        if "__remove_accept__" in h:
                            # 제거: 세션 헤더를 복사한 뒤 Accept 키만 빼고 전달하지 않음
                            pass
                        elif "Accept" in h:
                            hdr["Accept"] = h["Accept"]
                        try:
                            prob = sess.get(u, params=p or None, headers=(hdr or None), stream=True, timeout=6)
                            ct = prob.headers.get("Content-Type", "")
                            if prob.status_code == 200:
                                print(f"[i] SSE 연결: {u} params={p} via={sess_name} hdr={h.get('__desc__')} ct={ct}")
                                t = threading.Thread(target=_sse_loop, args=(sess, u, hdr, p, out_q, stop_evt), daemon=True)
                                t.start()
                                return out_q, stop_evt, t
                            last_err = f"{prob.status_code} {u} {p} via={sess_name} hdr={h.get('__desc__')}"
                        except Exception as e:
                            last_err = f"EXC {e} {u} {p} via={sess_name} hdr={h.get('__desc__')}"
        time.sleep(0.4)

    fail(f"SSE 연결 실패. last={last_err}")

def sse_stop(stop_evt: threading.Event, t: threading.Thread) -> None:
    stop_evt.set(); t.join(timeout=2)

def drain(q: Queue) -> None:
    try:
        while True: q.get_nowait()
    except Empty:
        pass

def payload_has_order_id(payload: Any, order_id: int) -> bool:
    def _scan(x: Any) -> bool:
        if isinstance(x, dict):
            if x.get("order_id") == order_id or x.get("id") == order_id: return True
            return any(_scan(v) for v in x.values())
        if isinstance(x, list):
            return any(_scan(v) for v in x)
        return False
    return _scan(payload)

# ------------------ flows ------------------
def ensure_staff_login() -> None:
    r = call(S_STAFF, "POST", f"{STAFF}/login", json=STAFF_CREDENTIALS, expect=(200,201,204))
    if "access" not in S_STAFF.cookies:
        try:
            if not r.json().get("access"): raise ValueError
        except Exception:
            fail("직원 로그인 실패(세션/토큰 없음)")

def register_login_customer() -> None:
    sfx = int(time.time()) % 1_000_000
    u, p = f"tester_{sfx}", f"Aa1!verystrong_{sfx}"
    call(S_CUST, "POST", f"{ACCOUNTS}/register", json={"username": u, "password": p, "profile_consent": False},
         expect=(201,200,409))
    r = call(S_CUST, "POST", f"{ACCOUNTS}/login", json={"username": u, "password": p}, expect=(200,201,204))
    body = as_json(r); tok = body.get("access") or body.get("token")
    if tok: S_CUST.headers.update({"Authorization": f"Bearer {tok}"})
    elif "access" not in S_CUST.cookies: fail("고객 로그인 세션/토큰 없음")

def get_customer_id() -> int:
    me = as_json(call(S_CUST, "GET", f"{ACCOUNTS}/me", add_slash_fallback=True))
    cid = me.get("customer_id") or (me.get("customer") or {}).get("id")
    if not cid: fail("customer_id 획득 실패")
    return int(cid)

def select_dinner_option_ids(code: str) -> list[int]:
    d = as_json(call(S_CUST, "GET", f"{CATALOG}/dinners/{code}", add_slash_fallback=True))
    sel: list[int] = []
    for g in d.get("option_groups") or []:
        opts = g.get("options") or []
        if not opts: continue
        chosen = next((o for o in opts if o.get("is_default") or o.get("default") is True), opts[0])
        oid = chosen.get("option_id") or chosen.get("id")
        if isinstance(oid, str) and oid.isdigit(): oid = int(oid)
        if isinstance(oid, int): sel.append(oid)
    return sel

def create_order_and_get_id(cid: int) -> int:
    option_ids = select_dinner_option_ids(DINNER_CODE)
    preview_payload = {
        "order_source": "GUI",
        "customer_id": cid,
        "dinner": {"code": DINNER_CODE, "style": DINNER_STYLE, "quantity": 1, "dinner_options": option_ids},
        "items": [], "coupons": []
    }
    call(S_CUST, "POST", f"{ORDERS}/price/preview", json=preview_payload, expect=200)
    create_payload = {**preview_payload,
        "receiver_name":"홍길동","receiver_phone":"010-1234-5678",
        "delivery_address":"서울시 임시로 789","place_label":"기본"}
    r = call(S_CUST, "POST", f"{ORDERS}/", json=create_payload, expect=(201,200), add_slash_fallback=True)
    body = as_json(r); oid = body.get("id") or body.get("order_id") or body.get("pk")
    if not oid: fail("주문 생성 응답 id 없음")
    return int(oid)

# ------------------ main ------------------
def main() -> None:
    print("[1] Staff 로그인…"); ensure_staff_login()

    print("[2] SSE 연결 시작…(재시도·폴백)")
    q, stop_evt, t = start_sse_with_retries()

    # 부트스트랩/기존 이벤트 비우기
    time.sleep(0.3); drain(q)

    print("[3] 고객 등록/로그인 및 새 주문 생성…")
    register_login_customer()
    cid = get_customer_id()
    new_oid = create_order_and_get_id(cid)
    print(f"[i] 새 주문 id={new_oid}")

    print("[4] SSE에서 새 주문 반영 대기…")
    deadline = time.time() + 25
    last_ev, last_data = None, None
    got = False
    try:
        while time.time() < deadline:
            try:
                ev, data = q.get(timeout=2.0)
            except Empty:
                continue
            last_ev, last_data = ev, data
            # if ev == "bootstrap":  # 초기 목록은 스킵
            #     continue
            if payload_has_order_id(data, new_oid):
                print(f"\n[PASS] SSE 반영 확인: event={ev}, order_id={new_oid}")
                got = True
                break
    finally:
        sse_stop(stop_evt, t)

    if not got:
        snippet = None
        try: snippet = json.dumps(last_data, ensure_ascii=False)[:800]
        except Exception: snippet = str(last_data)[:800]
        print(f"\n[FAIL] 제한시간 내 SSE 미반영. last_event={last_ev}, data={snippet}")

if __name__ == "__main__":
    main()
