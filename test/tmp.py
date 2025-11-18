# sse_order_e2e.py
import os, sys, time, json, random, string, threading, queue, requests

BASE = os.environ.get("MRDINNER_BASE", "http://localhost:8000")
STAFF_USER = os.environ.get("STAFF_USER", "owner")
STAFF_PASS = os.environ.get("STAFF_PASS", "1234")
WAIT_SEC = int(os.environ.get("WAIT_SEC", "30"))

SSE_URLS = [f"{BASE}/api/staff/sse/orders", f"{BASE}/api/staff/sse/orders/"]

def _fail(msg, code=1):
    print(f"[FAIL] {msg}")
    sys.exit(code)

def _ok(msg):
    print(f"[OK] {msg}")
    sys.exit(0)

def staff_login() -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE}/api/staff/login",
               json={"username": STAFF_USER, "password": STAFF_PASS},
               timeout=8)
    if r.status_code != 200:
        _fail(f"Staff 로그인 실패: {r.status_code} {r.text[:200]}")
    print("[1] Staff 로그인 성공")
    return s

def open_sse(staff_sess: requests.Session):
    hdrs = {
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    last = None
    for url in SSE_URLS:
        try:
            r = staff_sess.get(url, headers=hdrs, stream=True, timeout=(6, 60))
            ct = r.headers.get("Content-Type", "")
            print(f"[2] SSE 연결 시도 → {url} status={r.status_code} ct={ct}")
            if r.status_code == 200 and "text/event-stream" in ct:
                r.raw.decode_content = True
                return r
            last = f"{r.status_code} {ct}"
        except Exception as e:
            last = str(e)
    _fail(f"SSE 연결 실패: {last}")

def sse_reader(resp, out_q: queue.Queue):
    """
    SSE 프레임(event, data)을 out_q로 전달.
    """
    buf = ""
    raw = resp.raw
    while True:
        try:
            ch = raw.read(1)
        except Exception:
            ch = b""
        if not ch:
            time.sleep(0.02)
            continue
        buf += ch.decode("utf-8", "replace")
        # 프레임 경계(\n\n 또는 \r\n\r\n) 기준으로 분리
        while "\n\n" in buf or "\r\n\r\n" in buf:
            if "\r\n\r\n" in buf:
                block, buf = buf.split("\r\n\r\n", 1)
            else:
                block, buf = buf.split("\n\n", 1)
            if block.startswith(":"):
                continue
            ev = "message"
            data_lines = []
            for line in block.splitlines():
                if line.startswith("event:"):
                    ev = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:"):].lstrip())
            out_q.put((ev, "\n".join(data_lines)))

def customer_register_and_login() -> requests.Session:
    # 랜덤 고객
    uname = "u_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    pw = "pw1234!"
    s = requests.Session()

    r = s.post(f"{BASE}/api/auth/register", json={"username": uname, "password": pw}, timeout=8)
    if r.status_code not in (200, 201):
        _fail(f"고객 등록 실패: {r.status_code} {r.text[:200]}")

    r = s.post(f"{BASE}/api/auth/login", json={"username": uname, "password": pw}, timeout=8)
    if r.status_code != 200:
        _fail(f"고객 로그인 실패: {r.status_code} {r.text[:200]}")

    # /api/auth/me/ 로 세션 확인 (주의: 끝에 슬래시 필요)
    r = s.get(f"{BASE}/api/auth/me/", timeout=8)
    if r.status_code != 200:
        _fail(f"고객 세션 확인 실패(/api/auth/me/): {r.status_code} {r.text[:120]}")
    return s

def get_valentine_dinner_id(cust_sess: requests.Session) -> int:
    r = cust_sess.get(f"{BASE}/api/catalog/dinners/valentine", timeout=8)
    if r.status_code != 200:
        _fail(f"밸런타인 디너 조회 실패: {r.status_code}")
    data = r.json()
    did = data.get("id") or data.get("dinner", {}).get("id")
    if not isinstance(did, int):
        _fail("밸런타인 디너 id 파싱 실패")
    return did

def create_order(cust_sess: requests.Session, dinner_id: int) -> int:
    # 가격 미리보기
    preview = {
        "dinners": [
            {"dinner_id": dinner_id, "qty": 1, "options": []}
        ],
        "address": {"receiver_name": "홍길동", "receiver_phone": "010-1111-2222",
                    "delivery_address": "서울 중구 을지로 00", "place_label": "집"},
        "pay": {"card_token": "4242", "channel": "GUI"}
    }
    r = cust_sess.post(f"{BASE}/api/orders/price/preview", json=preview, timeout=8)
    if r.status_code != 200:
        _fail(f"price/preview 실패: {r.status_code} {r.text[:200]}")

    # 실제 주문 생성
    r = cust_sess.post(f"{BASE}/api/orders/", json=preview, timeout=12)
    if r.status_code != 201:
        _fail(f"주문 생성 실패: {r.status_code} {r.text[:200]}")
    body = r.json()
    oid = body.get("id")
    if not isinstance(oid, int):
        _fail("주문 id 파싱 실패")
    return oid

def main():
    # 1) 스태프 로그인 + SSE 연결
    staff = staff_login()
    resp = open_sse(staff)
    qev = queue.Queue()
    th = threading.Thread(target=sse_reader, args=(resp, qev), daemon=True)
    th.start()

    # 2) 부트스트랩 프레임 수신될 때까지 잠깐 대기(스트림 준비 신호)
    ready_deadline = time.time() + 10
    got_bootstrap = False
    while time.time() < ready_deadline:
        try:
            ev, data = qev.get(timeout=0.5)
        except queue.Empty:
            continue
        if ev == "bootstrap":
            try:
                arr = json.loads(data) if data else []
                print(f"[i] bootstrap 수신 ({len(arr)} rows)")
            except Exception:
                print(f"[i] bootstrap(raw) {data[:120]}")
            got_bootstrap = True
            break
        else:
            # 진단 프레임 등은 흘려보냄
            print(f"[i] 예열 단계 이벤트: {ev}")
    if not got_bootstrap:
        print("[w] bootstrap 미수신이지만 진행함(스트림은 열림)")

    # 3) 고객 등록/로그인 + 주문 생성
    print("[3] 고객 등록/로그인 및 주문 생성…")
    cust = customer_register_and_login()
    did = get_valentine_dinner_id(cust)
    oid = create_order(cust, did)
    print(f"[i] 새 주문 id={oid}")

    # 4) 해당 주문의 order_created를 대기
    deadline = time.time() + WAIT_SEC
    last_ev = None
    while time.time() < deadline:
        try:
            ev, data = qev.get(timeout=0.5)
        except queue.Empty:
            continue
        last_ev = ev
        payload = {}
        try:
            payload = json.loads(data) if data else {}
        except Exception:
            pass
        got_id = payload.get("order_id") or payload.get("id")

        print(f"[i] recv ev={ev} oid={got_id}")
        if ev == "order_created" and isinstance(got_id, int) and got_id == oid:
            _ok(f"order_created 수신 성공 (order_id={oid})")

    _fail(f"타임아웃: order_created 미수신. last_event={last_ev}")

if __name__ == "__main__":
    main()
