#!/usr/bin/env python3
import os, time, json, pprint, random
import requests

BASE_URL   = os.getenv("BASE_URL", "http://localhost:8000")
API_PREFIX = os.getenv("API_PREFIX", "/api")

ACCOUNTS = f"{BASE_URL}{API_PREFIX}/auth"
CATALOG  = f"{BASE_URL}{API_PREFIX}/catalog"
ORDERS   = f"{BASE_URL}{API_PREFIX}/orders"

pp = pprint.PrettyPrinter(indent=2, width=120, compact=False)
S = requests.Session()  # 쿠키 유지

def show(title, data):
    print(f"\n=== {title} ===")
    if isinstance(data, requests.Response):
        print(f"HTTP {data.status_code}")
        try:
            pp.pprint(data.json())
        except Exception:
            print(data.text[:400])
    else:
        pp.pprint(data)

def call(method, url, expect=(200,), **kwargs):
    r = S.request(method, url, **kwargs)
    if isinstance(expect, int):
        expect = (expect,)
    if r.status_code not in expect:
        show("ERROR RESP", r)
        raise SystemExit(f"[FAIL] {method} {url} -> {r.status_code} (expect {expect})")
    return r

def get_json(r):
    try:
        return r.json()
    except Exception:
        return {}

def assert_cookie(name="access"):
    if name not in S.cookies:
        raise SystemExit(f"[FAIL] cookie '{name}' not found")
    print(f"[OK] cookie '{name}' present")

def main():
    # ---------- 0) 테스트 사용자 준비 ----------
    suffix    = int(time.time()) % 1000000
    username  = f"tester_{suffix}"
    password  = f"Aa1!verystrong_{suffix}"
    print(f"Using username={username}")

    # ---------- 1) 회원가입 (동의 미체크) ----------
    r = call("POST", f"{ACCOUNTS}/register", expect=201, json={
        "username": username,
        "password": password,
        "profile_consent": False
    })
    show("REGISTER", r)

    # ---------- 2) 로그인 -> 쿠키 확인 ----------
    r = call("POST", f"{ACCOUNTS}/login", json={"username": username, "password": password})
    show("LOGIN", r)
    assert_cookie("access")

    # ---------- 3) /me ----------
    r = call("GET", f"{ACCOUNTS}/me")
    me = get_json(r)
    show("ME", r)

    # ---------- 4) 동의 없이 연락처 수정 시도 -> 403 기대 ----------
    r = call("PATCH", f"{ACCOUNTS}/contact", expect=(403,200), json={"real_name": "홍길동"})
    if r.status_code == 200:
        print("[WARN] contact allowed without consent? expected 403")
    show("CONTACT (expect 403 if no consent)", r)

    # ---------- 5) 프로필 동의 켜기 ----------
    r = call("PATCH", f"{ACCOUNTS}/profile-consent", json={"profile_consent": True})
    show("CONSENT ON", r)

    # ---------- 6) 연락처/전화 ----------
    r = call("PATCH", f"{ACCOUNTS}/contact", json={"real_name": "홍길동", "phone": "010-1234-5678"})
    show("CONTACT UPDATE", r)

    # ---------- 7) 주소 목록 ----------
    r = call("GET", f"{ACCOUNTS}/addresses")
    show("ADDR LIST (initial)", r)

    # ---------- 8) 주소 추가 2개 ----------
    r = call("POST", f"{ACCOUNTS}/addresses", expect=201, json={
        "label": "집",
        "line":  "서울시 어딘가 123",
        "lat": 37.5665, "lng": 126.9780,
        "is_default": True
    })
    show("ADDR CREATE (home, default)", r)

    r = call("POST", f"{ACCOUNTS}/addresses", expect=201, json={
        "label": "회사",
        "line":  "서울시 센터 456",
        "lat": 37.5665, "lng": 126.9900,
        "is_default": False
    })
    show("ADDR CREATE (office)", r)

    # ---------- 9) 주소 0번 일부 수정 ----------
    r = call("PATCH", f"{ACCOUNTS}/addresses/0", json={"label": "집(리모델링)"})
    show("ADDR PATCH idx=0", r)

    # ---------- 10) 기본 주소를 idx=1로 전환 ----------
    r = call("PATCH", f"{ACCOUNTS}/addresses/default", json={"idx": 1})
    show("ADDR SET DEFAULT -> 1", r)

    # ---------- 11) 주소 0번 삭제 ----------
    r = call("DELETE", f"{ACCOUNTS}/addresses/0", expect=200)
    show("ADDR DELETE idx=0", r)

    # ---------- 12) 카탈로그: 디너/아이템 확인 ----------
    r = call("GET", f"{CATALOG}/dinners")
    dinners = get_json(r)
    show("CATALOG /dinners", dinners)

    # 디너 'valentine' 찾기
    def find_dinner_id(dinners_json, code="valentine"):
        # 예상 형식: {"results":[{...,"code":"valentine","dinner_type_id":1}, ...]} or list
        arr = dinners_json.get("results") if isinstance(dinners_json, dict) else dinners_json
        if not isinstance(arr, list):
            return None
        for d in arr:
            if (d.get("code") == code) or (str(d.get("name","")).lower().startswith("valentine")):
                return d.get("dinner_type_id") or d.get("id") or d.get("pk")
        return None

    val_id = find_dinner_id(dinners)
    if not val_id:
        print("[WARN] couldn't find 'valentine' dinner id; continuing with 1")
        val_id = 1

    # 스타일 목록
    r = call("GET", f"{CATALOG}/dinners/valentine/styles")
    styles = get_json(r)
    show("CATALOG /dinners/valentine/styles", styles)

    style_id = None
    # 예상 형식: {"results":[{"style_id":1,"code":"simple"},...]} or list
    arr = styles.get("results") if isinstance(styles, dict) else styles
    if isinstance(arr, list) and arr:
        style_id = arr[0].get("style_id") or arr[0].get("id") or arr[0].get("pk")
    if not style_id:
        style_id = 1
        print("[WARN] style_id fallback -> 1")

    # 아이템 옵션 확인(steak)
    r = call("GET", f"{CATALOG}/items/steak")
    show("CATALOG /items/steak", r)

    # ---------- 13) 주문 생성(간단) ----------
    # 기본 주소 0번이 남아있다면 사용, 없다면 line 문자열로 채움
    addr_r = call("GET", f"{ACCOUNTS}/addresses")
    addr_json = get_json(addr_r)
    addresses = addr_json.get("addresses") or []
    if addresses:
        default_addr = next((a for a in addresses if a.get("is_default")), addresses[0])
        ship_line = default_addr.get("line")
        receiver_name = "홍길동"
        receiver_phone = "010-1234-5678"
    else:
        default_addr = None
        ship_line = "서울시 임시로 789"
        receiver_name = "홍길동"
        receiver_phone = "010-1234-5678"

    # 주문 페이로드 (dinner 1개, 수량 1)
    order_payload = {
        "receiver_name": receiver_name,
        "receiver_phone": receiver_phone,
        "delivery_address": ship_line,
        "place_label": (default_addr or {}).get("label", "기본"),
        "meta": {"source": "smoke_test"},
        "dinners": [
            {
                "dinner_type_id": val_id,
                "style_id": style_id,
                "person_label": "테이블1",
                "quantity": "1",
                # 필요 시 아이템 추가/옵션 변경은 여기 items/options 필드로 확장
            }
        ]
    }

    # POST /orders (주문 생성)
    try:
        r = call("POST", f"{ORDERS}/", expect=(201, 200), json=order_payload)
        created = get_json(r)
        show("ORDER CREATE", created)
        order_id = created.get("id") or created.get("order_id") or created.get("pk")
        if order_id:
            # GET /orders/{id}
            r = call("GET", f"{ORDERS}/{order_id}")
            show("ORDER DETAIL", r)
    except SystemExit as e:
        print("[WARN] 주문 생성 스텝 실패(아직 주문 API 미구현/스키마 불일치일 수 있음). 다음 단계로 계속합니다.")
        print(e)

    # ---------- 14) 비밀번호 변경 & 재로그인 체크 ----------
    r = call("PATCH", f"{ACCOUNTS}/password", json={
        "old_password": password,
        "new_password": password + "_X"
    })
    show("PASSWORD CHANGE", r)

    # 기존 세션 쿠키로 /me 접근 (여전히 유효)
    r = call("GET", f"{ACCOUNTS}/me")
    show("ME after password change (same session)", r)

    # 로그아웃
    r = call("POST", f"{ACCOUNTS}/logout", expect=(204,200))
    print("\n=== LOGOUT ===")
    print(f"HTTP {r.status_code}")
    # 쿠키 제거 확인
    if "access" in S.cookies:
        print("[WARN] 'access' cookie still present in client jar; server deleted-cookie header was sent though.")
    else:
        print("[OK] client cookie cleared (jar)")

    print("\nAll steps attempted. ✅")

if __name__ == "__main__":
    main()
