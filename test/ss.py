# watch_orders_sse.py
import os, sys, time, json, requests

BASE = os.environ.get("MRDINNER_BASE", "http://localhost:8000")
STAFF_USER = os.environ.get("STAFF_USER", "owner")
STAFF_PASS = os.environ.get("STAFF_PASS", "1234")
TARGET_ID = os.environ.get("TARGET_ID")  # 특정 주문 id만 기다릴 때 사용 (없으면 아무 order_created면 통과)
WAIT_SEC = int(os.environ.get("WAIT_SEC", "25"))

SSE_PATHS = ["/api/staff/sse/orders", "/api/staff/sse/orders/"]

def fail(msg, code=1):
    print(f"[FAIL] {msg}")
    sys.exit(code)

def ok(msg):
    print(f"[OK] {msg}")
    sys.exit(0)

def staff_login(s: requests.Session):
    url = f"{BASE}/api/staff/login"
    r = s.post(url, json={"username": STAFF_USER, "password": STAFF_PASS}, timeout=5)
    if r.status_code != 200:
        fail(f"스태프 로그인 실패 {r.status_code}: {r.text[:200]}")
    print("[1] Staff 로그인 성공")

def open_sse(s: requests.Session):
    headers = {
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    last_err = None
    for path in SSE_PATHS:
        url = f"{BASE}{path}"
        try:
            r = s.get(url, headers=headers, stream=True, timeout=(5, 60))
            ctype = r.headers.get("Content-Type", "")
            print(f"[2] SSE 연결 ct={ctype} status={r.status_code} url={url}")
            if r.status_code == 200 and "text/event-stream" in ctype:
                r.raw.decode_content = True
                return r
            last_err = f"{r.status_code} {ctype}"
        except Exception as e:
            last_err = str(e)
    fail(f"SSE 연결 실패: {last_err}")

def iter_sse_blocks(resp, deadline):
    """SSE 블록(event+data)을 yield. 타임아웃(deadline)까지 대기."""
    raw = resp.raw
    buf = ""
    while time.time() < deadline:
        try:
            chunk = raw.read(1)  # 1바이트씩 읽어도 충분함
        except Exception:
            chunk = b""
        if chunk:
            buf += chunk.decode("utf-8", "replace")
            # \r\n\r\n, \n\n 모두 허용
            while "\n\n" in buf or "\r\n\r\n" in buf:
                if "\r\n\r\n" in buf:
                    block, buf = buf.split("\r\n\r\n", 1)
                else:
                    block, buf = buf.split("\n\n", 1)
                if block.startswith(":"):  # 주석 프레임
                    continue
                ev = "message"
                data_lines = []
                for line in block.splitlines():
                    if line.startswith("event:"):
                        ev = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[len("data:"):].lstrip())
                yield ev, "\n".join(data_lines)
        else:
            time.sleep(0.02)

def main():
    target_id = int(TARGET_ID) if TARGET_ID and TARGET_ID.isdigit() else None
    s = requests.Session()
    staff_login(s)
    resp = open_sse(s)

    deadline = time.time() + WAIT_SEC
    print(f"[3] SSE 수신 대기… ({WAIT_SEC}s, target_id={target_id})")

    seen_bootstrap = False
    last_ev = None

    for ev, data_str in iter_sse_blocks(resp, deadline):
        last_ev = ev
        if ev == "bootstrap":
            seen_bootstrap = True
            try:
                arr = json.loads(data_str) if data_str else []
                print(f"[i] bootstrap {len(arr)} rows")
            except Exception:
                print(f"[i] bootstrap (파싱 실패) raw={data_str[:120]}")
            continue

        # 기타 이벤트 처리
        payload = None
        try:
            payload = json.loads(data_str) if data_str else {}
        except Exception:
            print(f"[i] {ev} (raw) {data_str[:120]}")
            payload = {}

        oid = payload.get("order_id") or payload.get("id")
        print(f"[i] recv event={ev} order_id={oid} keys={list(payload.keys())[:5]}")

        if ev == "order_created":
            if target_id is None or (isinstance(oid, int) and oid == target_id):
                ok(f"order_created 수신 성공 (order_id={oid})")

        # 업데이트 이벤트도 통과로 보고 싶으면 주석 해제
        if ev == "order_updated":
            if target_id is None or (isinstance(oid, int) and oid == target_id):
                ok(f"order_updated 수신 성공 (order_id={oid})")

    # 타임아웃
    hint = " (bootstrap은 받음)" if seen_bootstrap else ""
    fail(f"타임아웃: order_created 미수신{hint}. last_event={last_ev}")

if __name__ == "__main__":
    main()