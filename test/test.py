#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UOS-SE-DELIVERY 전체 API 스모크/스키마 검증 스크립트 (Fail-Fast, no env)

- 고객용 세션 S_CUST  ⟶ /api/auth, /api/catalog, /api/orders
- 직원용 세션 S_STAFF ⟶ /api/staff (쿠키 'access'가 서로 덮어쓰지 않도록 분리)

staff API 가정:
  /api/staff/login           -> HttpOnly cookie 'access' (응답 바디 토큰 없음)
  /api/staff/me              -> 200(인증), 403(비인증)
  /api/staff/coupons         -> GET/POST
  /api/staff/coupons/{code}  -> GET/PATCH/DELETE
  /api/staff/orders/{id}     -> GET
  /api/staff/sse/orders      -> GET (SSE)
"""
from __future__ import annotations
import time, json, pprint, threading, sys
from typing import Iterable, Dict, Any, Optional, Tuple
from queue import Queue, Empty
import requests

# ---------------------------------------------------------------------
# 고정 설정 (환경변수 사용 금지)
# ---------------------------------------------------------------------
BASE_URL         = "http://localhost:8000".rstrip("/")
API_PREFIX       = "/api"
REQ_TIMEOUT      = 30

ACCOUNTS         = f"{BASE_URL}{API_PREFIX}/auth"
CATALOG          = f"{BASE_URL}{API_PREFIX}/catalog"
ORDERS           = f"{BASE_URL}{API_PREFIX}/orders"
STAFF            = f"{BASE_URL}{API_PREFIX}/staff"

# Staff 로그인 정보(없으면 None)
STAFF_CREDENTIALS = {"username": "boss", "password": "1234"}

# 주문 목록 필터(SSE 등 파라미터로만 사용)
STAFF_STATUS     = "pending"   # or None
STAFF_READY      = None        # "0" | "1" | None

pp = pprint.PrettyPrinter(indent=2, width=120, compact=False)

# 전역 세션 두 개(고객/직원 완전 분리)
S_CUST  = requests.Session();  S_CUST.headers.update({"Accept": "application/json"})
S_STAFF = requests.Session(); S_STAFF.headers.update({"Accept": "application/json"})

# ---------------------------------------------------------------------
# 공통 유틸 (Fail-Fast)
# ---------------------------------------------------------------------
def exit_fail(msg: str) -> None:
    print(f"\n[FAIL] {msg}")
    sys.exit(2)

def show(title: str, data: Any) -> None:
    print(f"\n=== {title} ===")
    if isinstance(data, requests.Response):
        print(f"HTTP {data.status_code}")
        try:
            pp.pprint(data.json())
        except Exception:
            print((data.text or "")[:1200])
    else:
        pp.pprint(data)

def _try_request(sess: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", REQ_TIMEOUT)
    return sess.request(method, url, **kwargs)

def call(sess: requests.Session, method: str, url: str, expect: Iterable[int] | int = (200,),
         add_slash_fallback: bool = True, label: str = "", **kwargs) -> requests.Response:
    exp = (expect,) if isinstance(expect, int) else tuple(expect)
    urls = [url] if not add_slash_fallback else [url, (url if url.endswith("/") else url + "/")]

    last: requests.Response | Exception | None = None
    for u in urls:
        try:
            last = _try_request(sess, method, u, **kwargs)
            if isinstance(last, requests.Response) and last.status_code in exp:
                return last
        except requests.RequestException as e:
            last = e

    if isinstance(last, requests.Response):
        show(f"ERROR RESP ({label or method+' '+url})", last)
        exp_str = ",".join(map(str, exp))
        exit_fail(f"{method} {url} -> {last.status_code} (expect {exp_str})")
    else:
        exit_fail(f"{method} {url} request error: {last}")

def get_json(resp: requests.Response) -> Dict[str, Any]:
    try:
        return resp.json()
    except Exception:
        show("NON-JSON RESPONSE", resp)
        exit_fail("JSON 파싱 실패")

def must_keys(d: Dict[str, Any], keys: Iterable[str], where: str = "") -> None:
    miss = [k for k in keys if k not in d]
    if miss:
        pp.pprint(d)
        exit_fail(f"필수 키 누락{(' @ '+where) if where else ''}: {miss}")

def assert_cookie(sess: requests.Session, name="access") -> None:
    if name not in sess.cookies:
        exit_fail(f"쿠키 '{name}' 없음(로그인 실패 또는 서버 설정 확인)")

# ---------------------------------------------------------------------
# SSE 유틸 (세션 주입형)
# ---------------------------------------------------------------------
def _parse_sse_frame(frame: str) -> Tuple[Optional[str], Optional[dict]]:
    event, data_lines = None, []
    for ln in frame.splitlines():
        if ln.startswith("event:"): event = ln[len("event:"):].strip()
        elif ln.startswith("data:"): data_lines.append(ln[len("data:"):].strip())
    if not data_lines:
        return event, None
    data_str = "\n".join(data_lines)
    try:
        obj = json.loads(data_str)
    except Exception:
        obj = {"raw": data_str}
    return event, obj

def _sse_reader(sess: requests.Session, url: str, params: dict, out_q: Queue, stop_evt: threading.Event):
    try:
        with sess.get(url, params=params, stream=True, timeout=max(REQ_TIMEOUT, 60)) as resp:
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

# ---------------------------------------------------------------------
# 카탈로그 유틸(옵션 자동 선택)
# ---------------------------------------------------------------------
def _select_dinner_option_ids(dinner_code: str) -> list:
    r = call(S_CUST, "GET", f"{CATALOG}/dinners/{dinner_code}", label=f"catalog/dinner({dinner_code})")
    detail = get_json(r)
    groups = detail.get("option_groups") or detail.get("dinner_option_groups") or []
    selected_ids: list = []
    for g in groups:
        options = g.get("options") or []
        if not options:
            continue
        chosen = next((o for o in options if o.get("default") is True), options[0])
        opt_id = chosen.get("option_id") or chosen.get("code") or chosen.get("id")
        if opt_id is None:
            continue
        if isinstance(opt_id, str) and opt_id.isdigit():
            opt_id = int(opt_id)
        selected_ids.append(opt_id)
    return selected_ids

# ---------------------------------------------------------------------
# Staff 세션 만들기(직원 세션에만 쿠키 세팅)
# ---------------------------------------------------------------------
def make_staff_session() -> None:
    if not STAFF_CREDENTIALS:
        print("[i] STAFF_CREDENTIALS 미설정 → 직원 세션 건너뜀")
        return
    user = STAFF_CREDENTIALS.get("username")
    pw   = STAFF_CREDENTIALS.get("password")
    if not user or not pw:
        exit_fail("STAFF_CREDENTIALS 설정 오류(username/password)")
    print(f"[i] Staff 로그인 시도: {user}")
    try:
        r = S_STAFF.post(f"{STAFF}/login", json={"username": user, "password": pw}, timeout=REQ_TIMEOUT)
    except Exception as e:
        exit_fail(f"Staff 로그인 요청 실패: {e}")
    if r.status_code not in (200, 201, 204):
        show("STAFF LOGIN FAIL", r)
        exit_fail("Staff 로그인 실패")
    assert_cookie(S_STAFF, "access")
    print("[OK] Staff 로그인 성공")

# ---------------------------------------------------------------------
# Staff 테스트 (단건 주문 상세 + SSE + 쿠폰 CRUD 스키마 확인)
# ---------------------------------------------------------------------
def staff_tests(order_id: Optional[int]) -> dict:
    print("\n================ STAFF APP TESTS ================")

    def req(method: str, url: str, expect=(200,), add_slash=True, label="STAFF", **kw):
        return call(S_STAFF, method, url, expect=expect, add_slash_fallback=add_slash, label=label, **kw)

    # /me (권한 확인: 200 또는 403 허용)
    r = req("GET", f"{STAFF}/me", expect=(200, 403), label="STAFF /me")
    show("STAFF GET /me", r)

    # 단건 주문 상세 (order_id가 있으면 검사)
    if order_id is not None:
        r = req("GET", f"{STAFF}/orders/{order_id}", label="orders(detail)")
        detail = get_json(r)
        show(f"STAFF GET /orders/{order_id}", detail)
        must_keys(detail, ["id", "status", "subtotal_cents", "total_cents", "dinners"], where="staff.order.detail")

    # SSE(짧게 연결)
    params = {}
    if STAFF_STATUS:
        params["status"] = STAFF_STATUS
    if STAFF_READY in {"0", "1"}:
        params["ready"] = STAFF_READY

    sse_q: Queue = Queue()
    stop_evt = threading.Event()
    t = threading.Thread(target=_sse_reader, args=(S_STAFF, f"{STAFF}/sse/orders", params, sse_q, stop_evt), daemon=True)
    t.start()
    print(f"SSE 연결: {STAFF}/sse/orders params={params}")
    try:
        ev, data = sse_q.get(timeout=8)
        show("SSE 첫 이벤트", {"event": ev, "data": data})
    except Empty:
        print("! SSE 타임아웃 (초기 이벤트 없음) — 계속 진행")
    finally:
        stop_evt.set(); t.join(timeout=2); print("SSE 종료")

    # 쿠폰 관리 (리스트 → 생성 → 패치)
    # 목록
    r = req("GET", f"{STAFF}/coupons", label="coupons(list)")
    coupons = get_json(r)
    show("STAFF GET /coupons", coupons)
    if not (isinstance(coupons, list) or (isinstance(coupons, dict) and "results" in coupons)):
        exit_fail("쿠폰 목록 스키마가 리스트/페이징(results) 형태가 아님")

    # 생성
    ts = int(time.time())
    new_code = f"AUTOTEST_{ts}"
    create_payload = {
        "code": new_code,
        "name": "자동화 테스트 쿠폰",
        "label": "AUTOTEST",
        "active": True,
        "kind": "percent",
        "value": 10.0,
        "channel": "ANY",
        "min_subtotal_cents": 0,
        "max_discount_cents": 0,
        "max_redemptions_global": 0,
        "max_redemptions_per_user": 0,
        "stackable_with_membership": False,
        "stackable_with_coupons": False,
        "notes": "created by test.py",
    }
    r = req("POST", f"{STAFF}/coupons", expect=(201,), label="coupons(create)", json=create_payload)
    created = get_json(r); show("STAFF POST /coupons (create)", created)
    must_keys(created, ["code", "active", "kind", "value", "channel"], where="coupon.create")

    # 패치(코드로 단건)
    r = req("PATCH", f"{STAFF}/coupons/{new_code}", expect=(200,), label="coupons(patch#1)",
            json={"active": False, "label": "AUTO-OFF"})
    patched = get_json(r); show(f"STAFF PATCH /coupons/{new_code} (deactivate)", patched)
    if patched.get("active") is not False:
        exit_fail("쿠폰 비활성화 실패(active != False)")

    r = req("PATCH", f"{STAFF}/coupons/{new_code}", expect=(200,), label="coupons(patch#2)",
            json={"active": True, "label": "AUTO-ON"})
    patched = get_json(r); show(f"STAFF PATCH /coupons/{new_code} (activate)", patched)
    if patched.get("active") is not True:
        exit_fail("쿠폰 활성화 실패(active != True)")

    return {"coupon_code": new_code}

# ---------------------------------------------------------------------
# 메인 시나리오 (Fail-Fast)
# ---------------------------------------------------------------------
def main() -> None:
    # 0) 회원가입용 계정 생성(충돌 가능성 낮은 suffix)
    suffix   = int(time.time()) % 1_000_000
    username = f"tester_{suffix}"
    password = f"Aa1!verystrong_{suffix}"
    print(f"Using username={username}")

    # 1) 회원가입 (고객 세션)
    r = call(S_CUST, "POST", f"{ACCOUNTS}/register", expect=(201,200,409),
             json={"username": username, "password": password, "profile_consent": False},
             label="auth/register")
    show("REGISTER", r)

    # 2) 로그인 -> 토큰/쿠키 설정 (고객 세션)
    r = call(S_CUST, "POST", f"{ACCOUNTS}/login", expect=(200,201,204),
             json={"username": username, "password": password}, label="auth/login")
    show("LOGIN", r)
    body = get_json(r)
    token = body.get("access") or body.get("token")
    if token:
        S_CUST.headers.update({"Authorization": f"Bearer {token}"})
    else:
        assert_cookie(S_CUST, "access")

    # 3) /me 및 동의/연락처 업데이트 (고객 세션)
    r = call(S_CUST, "GET", f"{ACCOUNTS}/me/", label="auth/me")
    me = get_json(r); show("ME", me)

    r = call(S_CUST, "PATCH", f"{ACCOUNTS}/me/", json={"profile_consent": True}, label="me(consent on)")
    show("CONSENT ON (PATCH /me/)", r)

    r = call(S_CUST, "PATCH", f"{ACCOUNTS}/me/", json={"real_name": "홍길동", "phone": "010-1234-5678"},
             label="me(contact update)")
    show("CONTACT UPDATE (PATCH /me/)", r)

    # 4) 주소 (CRUD)
    r = call(S_CUST, "GET", f"{ACCOUNTS}/me/addresses/", label="addr(list)")
    show("ADDR LIST (initial)", r)

    r = call(S_CUST, "POST", f"{ACCOUNTS}/me/addresses/", expect=(201,200), label="addr(create#home)", json={
        "label": "집", "line": "서울시 어딘가 123", "lat": 37.5665, "lng": 126.9780, "is_default": True
    })
    show("ADDR CREATE (home, default)", r)

    r = call(S_CUST, "POST", f"{ACCOUNTS}/me/addresses/", expect=(201,200), label="addr(create#office)", json={
        "label": "회사", "line": "서울시 센터 456", "lat": 37.5665, "lng": 126.9900, "is_default": False
    })
    show("ADDR CREATE (office)", r)

    r = call(S_CUST, "GET", f"{ACCOUNTS}/me/addresses/", label="addr(list#2)")
    show("ADDR LIST #2", r)

    r = call(S_CUST, "PATCH", f"{ACCOUNTS}/me/addresses/0/", json={"label": "집(리모델링)"},
             label="addr(patch idx=0)")
    show("ADDR PATCH idx=0", r)

    r = call(S_CUST, "PATCH", f"{ACCOUNTS}/me/addresses/1/default/", json={}, label="addr(set default idx=1)")
    show("ADDR SET DEFAULT -> 1", r)

    r = call(S_CUST, "DELETE", f"{ACCOUNTS}/me/addresses/0/", expect=(200,204), label="addr(delete idx=0)")
    show("ADDR DELETE idx=0", r)

    # 5) 카탈로그
    r = call(S_CUST, "GET", f"{CATALOG}/bootstrap", label="catalog/bootstrap")
    show("CATALOG /bootstrap", r)

    r = call(S_CUST, "GET", f"{CATALOG}/dinners/valentine", label="catalog/dinner(valentine)")
    show("CATALOG /dinners/valentine", r)

    r = call(S_CUST, "GET", f"{CATALOG}/items/steak", label="catalog/item(steak)")
    show("CATALOG /items/steak", r)

    # 6) Staff 로그인(직원 세션에만 쿠키 세팅)
    make_staff_session()

    # 7) 주문: 프리뷰 → 생성 → 상세 (고객 세션으로 진행)
    me2 = get_json(call(S_CUST, "GET", f"{ACCOUNTS}/me", label="me(fetch for customer_id)")) or {}
    customer_id = (
        me2.get("customer_id")
        or (me2.get("customer") or {}).get("id")
        or (me2.get("data") or {}).get("customer_id")
    )
    if not customer_id:
        exit_fail("customer_id를 /auth/me에서 찾을 수 없음")

    DINNER_CODE  = "valentine"
    DINNER_STYLE = "simple"

    selected_option_ids = _select_dinner_option_ids(DINNER_CODE)

    preview_payload = {
        "order_source": "GUI",
        "customer_id": customer_id,
        "dinner": {
            "code": DINNER_CODE,
            "style": DINNER_STYLE,
            "quantity": 1,
            "dinner_options": selected_option_ids
        },
        "items": [],
        "coupons": []
    }

    r = call(S_CUST, "POST", f"{ORDERS}/price/preview", expect=(200,), add_slash_fallback=False,
             label="orders/price/preview", json=preview_payload)
    preview = get_json(r); show("ORDERS price/preview", preview)

    create_payload = {
        **preview_payload,
        "receiver_name":  "홍길동",
        "receiver_phone": "010-1234-5678",
        "delivery_address": "서울시 임시로 789",
        "place_label": "기본"
    }
    r = call(S_CUST, "POST", f"{ORDERS}/", expect=(201, 200), label="orders(create)", json=create_payload)
    created = get_json(r); show("ORDER CREATE", created)

    oid = created.get("id") or created.get("order_id") or created.get("pk")
    if not oid:
        exit_fail("주문 생성 응답에 ID 없음")

    # 고객용 상세
    r = call(S_CUST, "GET", f"{ORDERS}/{oid}", label="orders(detail)")
    show("ORDER DETAIL (customer API)", r)

    # 8) Staff 시나리오(직원 세션): 단건 상세 + SSE + 쿠폰 CRUD
    staff_out = staff_tests(order_id=oid)
    _ = staff_out.get("coupon_code")

    # 9) 비밀번호 변경 + 로그아웃 (고객 세션)
    r = call(S_CUST, "POST", f"{ACCOUNTS}/me/password/", label="me/password change",
             json={"old_password": password, "new_password": password + '_X'})
    show("PASSWORD CHANGE (POST /me/password/)", r)

    r = call(S_CUST, "POST", f"{ACCOUNTS}/logout", expect=(200,204), label="auth/logout")
    print("\n=== LOGOUT ==="); print(f"HTTP {r.status_code}")

    print("\nAll steps completed.")

# ---------------------------------------------------------------------
if __name__ == "__main__":
    main()
