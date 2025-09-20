
# 데이터 모델 설명서

이 문서는 `accounts / catalog / orders / staff` 네 개 앱의 **Django 모델 구조**와
의도(도메인 규칙), 제약, 사용 패턴을 상세히 설명합니다.  
DB는 PostgreSQL을 기준으로 하며, 시간대 정책은 **애플리케이션은 Asia/Seoul, DB 저장은 UTC**를 가정합니다.

---

## 공통 표기
- `PK`: Primary Key
- `FK → X`: X 테이블로의 외래키
- `…?`: NULL 허용
- `JSON`: Django `JSONField`(PostgreSQL `jsonb`)
- `_cents`: 통화 금액을 **정수(원 단위)** 로 저장(예: 69,600원 → 69600)

---

# 1) accounts 앱

## 1.1 `Customer`
고객 프로필(동의 시 이름/연락처/주소 저장)과 로그인 정보.

| 필드 | 타입 | 설명 |
|---|---|---|
| `customer_id` | BigAutoField (PK) | 고객 PK |
| `username` | Text (UNIQUE) | 로그인 ID/닉네임 |
| `password` | Char(64) | **SHA-256 hex** |
| `real_name` | Text? | 동의 시 저장 |
| `phone` | Text? | `^010-\d{4}-\d{4}$` 검증(하이픈 포함) |
| `addresses` | JSON (default=[]) | **최대 3개** 주소(라벨/좌표 등). DB `CHECK`로 길이 ≤ 3 보장 |
| `loyalty_tier` | Text (choices) | `none/silver/gold` |
| `profile_consent` | Bool | 프로필 저장 동의 여부 |
| `profile_consent_at` | DateTime? | 동의 시간 |
| `created_at` | DateTime | 생성 시각 |

### 제약
- `CHECK ck_customer_addresses_json`: `addresses`는 배열이고 길이 ≤ 3  
  (RawSQL을 `ExpressionWrapper(Boolean)`로 감싸 구현)
- `CHECK ck_customer_loyalty_tier`: `none/silver/gold` 화이트리스트

### 주소 JSON 예시
```json
[
  {"label":"home","line":"서울시 중랑구 ...","lat":37.6,"lng":127.08,"is_default":true},
  {"label":"work","line":"서울시 성동구 ...","lat":37.55,"lng":127.04}
]
```

### 운영 메모
- 주문 시에는 `orders` 쪽에 **배송 스냅샷**을 별도로 저장.
- 주소가 3개 이상/이력 관리가 필요해지면 별도 `customer_address` 테이블로 분리 고려. --> 근데 필요 없을듯 합니다.

---

# 2) catalog 앱 (메뉴/옵션/디너/스타일)

## 2.1 `MenuCategory`
트리형 카테고리.

| 필드 | 타입 | 설명 |
|---|---|---|
| `category_id` | BigAutoField (PK) |  |
| `parent` | FK → self ? | 상위 카테고리 |
| `name` | Text | 표시명 |
| `slug` | Slug? (UNIQUE) | 영문 코드 |
| `rank` | Int | 정렬 우선순위(낮을수록 위) |
| `active` | Bool | 비활성화 시 숨김 |

## 2.2 `ItemTag`, `ItemTagMap`
아이템 성격 태그와 다대다 매핑.

## 2.3 `MenuItem`
판매 가능한 원자 아이템(요리/와인/디저트/커피 등).

| 필드 | 타입 | 설명 |
|---|---|---|
| `item_id` | BigAutoField (PK) |  |
| `code` | Char(120) UNIQUE | 코드 |
| `name` | Text | 아이템명 |
| `description` | Text? | 설명 |
| `category` | FK → MenuCategory ? | 분류 |
| `unit` | Char(50)? | 인분/병/잔 등 |
| `base_price_cents` | UInt | 기본가(옵션 전) |
| `active` | Bool | 판매 가능 여부 |
| `attrs` | JSON (default={}) | 알러지/원산지/영양 등 자유 키 |

인덱스: `idx_menu_item_name`(이름 검색)

## 2.4 옵션 시스템
### `ItemOptionGroup`
옵션 그룹(사이즈/굽기/토핑/샷 등).

| 필드 | 타입 | 설명 |
|---|---|---|
| `group_id` | BigAutoField (PK) |  |
| `item` | FK → MenuItem | 어느 아이템의 옵션인지 |
| `name` | Text | 그룹 라벨 |
| `select_mode` | Enum | `single` or `multi` |
| `min_select` | UInt (default=0) | 최소 선택 수 |
| `max_select` | Int? | 최대 선택 수(NULL=무제한) |
| `is_required` | Bool | 필수 여부 |
| `is_variant` | Bool | 사이즈/용량 |
| `price_mode` | Enum | `addon`(가산) or `multiplier`(배수) |
| `rank` | Int | 정렬 순서 |

제약: `max_select >= 0` 또는 `NULL`

### `ItemOption`
옵션 선택지.

| 필드 | 타입 | 설명 |
|---|---|---|
| `option_id` | BigAutoField (PK) |  |
| `group` | FK → ItemOptionGroup |  |
| `name` | Text | 선택지명 |
| `price_delta_cents` | UInt (default=0) | 가산금액(price_mode=addon) |
| `multiplier` | Decimal(7,3)? | 배수(price_mode=multiplier) |
| `is_default` | Bool | 기본 선택(주로 single) |
| `rank` | Int | 정렬 |

**아이템 가격 공식**  
`(base_price + Σ addon) × Π multiplier`

## 2.5 `ServingStyle`
simple / grand / deluxe 등 **코스 전체**에 적용되는 스타일.

| 필드 | 타입 | 설명 |
|---|---|---|
| `style_id` | BigAutoField (PK) |  |
| `code` | Char(60) UNIQUE | 스타일 코드 |
| `name` | Text |  |
| `price_mode` | Enum | `multiplier` 혹은 `addon` |
| `price_value` | Decimal(7,2) | 배수 또는 가산금액 |
| `notes` | Text? | 비고 |

## 2.6 `DinnerType`
코스/세트(발렌타인/프렌치/샴페인 등).

| 필드 | 타입 | 설명 |
|---|---|---|
| `dinner_type_id` | BigAutoField (PK) |  |
| `code` | Char(120) UNIQUE | 디너 코드 |
| `name` | Text |  |
| `description` | Text? |  |
| `base_price_cents` | UInt | 기준가 |
| `active` | Bool | 판매 가능 여부 |

## 2.7 `DinnerTypeDefaultItem`
디너에 **기본 포함**되는 아이템/수량 목록(정의서).

| 필드 | 타입 | 설명 |
|---|---|---|
| `dinner_type` | FK → DinnerType |  |
| `item` | FK → MenuItem |  |
| `default_qty` | Decimal(10,2) | 기본 수량 |
| `included_in_base` | Bool | 기준가에 포함 여부 |
| `notes` | Text? |  |

UNIQUE: `(dinner_type, item)`

## 2.8 `DinnerStyleAllowed`
디너별 **허용 스타일** 조합(업무 규칙을 DB에서 강제).

UNIQUE: `(dinner_type, style)`

## 2.9 (선택) `DinnerOptionGroup` / `DinnerOption`
디너 레벨 옵션(코스 선택/교체/업그레이드).

- `DinnerOptionGroup`: `select_mode/min/max/price_mode/rank` 등은 아이템 옵션 그룹과 동일 개념.
- `DinnerOption`: `item`(선택형) 또는 `name`(추가금만 있는 가상 옵션) 중 하나는 반드시 존재.  
  `CHECK ck_dinner_opt_has_name_or_item` 로 보장.

## 2.10 (선택) `ItemAvailability`
아이템의 요일/시간/기간 제한(시즌/런치 전용 등).  
`dow ∈ [0,6]`, UNIQUE `(item, dow, start_time)`

---

# 3) orders 앱 (주문 스냅샷)

## 3.1 `Order`
주문 헤더. **배송/결제 스냅샷**과 합계 보관.

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | BigAutoField (PK) |  |
| `customer` | FK → accounts.Customer (RESTRICT) | 고객 |
| `ordered_at` | DateTime | 주문 시각 |
| `status` | Enum | `pending/preparing/out_for_delivery/delivered/canceled` |
| `order_source` | Enum | `GUI` or `VOICE` |
| `receiver_name/phone` | Text? | 수령인 스냅샷 |
| `delivery_address` | Text? | 주소 스냅샷 |
| `geo_lat/lng` | Decimal(9,6)? | 좌표 |
| `place_label` | Text? | 집/회사 라벨 등 |
| `address_meta` | JSON? | 도로명/지번/건물/POI 등 |
| `payment_token` | Text? | 결제 토큰(민감정보 미저장) |
| `card_last4` | Char(4)? | 카드 뒷4자리 |
| `subtotal_cents/discount_cents/total_cents` | UInt | 합계 |
| `meta` | JSON? | 기타 메타 |

인덱스: `(customer, -ordered_at)`

## 3.2 `OrderDinner`
주문 안의 디너 인스턴스

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | BigAutoField (PK) |  |
| `order` | FK → Order (CASCADE) |  |
| `dinner_type` | FK → catalog.DinnerType (RESTRICT) |  |
| `style` | FK → catalog.ServingStyle (RESTRICT) |  |
| `person_label` | Text? | 수령인 라벨 |
| `quantity` | Decimal(10,2) | 수량(기본 1) |
| `base_price_cents` | UInt | 디너 기준가 스냅샷 |
| `style_adjust_cents` | UInt | 스타일 가산가(또는 multiplier 계산 결과 반영) |
| `notes` | Text? |  |

**중요 제약**: `(dinner_type, style)` 조합은 `catalog.DinnerStyleAllowed` 에 존재해야 함.  
→ `orders/migrations/0002`에서 **복합 FK**를 `RunSQL`로 추가.

## 3.3 `OrderDinnerItem`
디너 내 최종 아이템 구성(기본 + 증감/추가 결과).

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | BigAutoField (PK) |  |
| `order_dinner` | FK → OrderDinner (CASCADE) |  |
| `item` | FK → MenuItem (RESTRICT) |  |
| `final_qty` | Decimal(10,2) | 최종 수량 |
| `unit_price_cents` | UInt | 단가 스냅샷 |
| `is_default` | Bool | 기본 구성 여부 |
| `change_type` | Enum | `unchanged/added/removed/increased/decreased` |

UNIQUE: `(order_dinner, item)`

## 3.4 `OrderItemOption` / `OrderDinnerOption`
- `OrderItemOption`: 아이템 옵션 선택 스냅샷(옵션 그룹/옵션 이름, 가격 영향).  
- `OrderDinnerOption`: 디너 레벨 옵션 선택 스냅샷.

> **가격 계산 권장 플로우**:  
> 1) 디너 기준가 + 스타일(addon/multiplier) 반영  
> 2) 디너 옵션(addon/multiplier) 반영  
> 3) 아이템 옵션(addon/multiplier) 반영  
> 4) 기본 포함 아이템 증감/삭제에 따른 차액 반영  
> 5) 주문 헤더에 `subtotal/discount/total` 고정 저장

---

# 4) staff 앱 (직원/출퇴근/일일집계)

## 4.1 `Staff`
직원 프로필(정적).

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | BigAutoField (PK) |  |
| `name` | Text |  |
| `role` | Enum | `delivery` 또는 `kitchen` |
| `active` | Bool | 배달직원의 경우 가용한지 |

## 4.2 `StaffShift`
출근/퇴근 기록(이력).

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | BigAutoField (PK) |  |
| `staff` | FK → Staff (CASCADE) |  |
| `started_at` | DateTime | 출근시각 |
| `ended_at` | DateTime? | 퇴근시각(NULL=근무중) |
| `work_minutes` | Int? | **퇴근 시** 자동 계산(트리거) |

제약
- `CHECK ended_at > started_at` (또는 NULL)
- **부분 유니크**: `(staff, ended_at IS NULL)` → **미종료 근무 1개만 허용**

트리거
- `BEFORE UPDATE OF ended_at`: `work_minutes` 계산
- `AFTER UPDATE OF ended_at`: `StaffDailyHours`에 **Asia/Seoul 기준 날짜로 분할 upsert**

## 4.3 `StaffDailyHours`
직원×날짜별 누적 근무 시간(분).

| 필드 | 타입 | 설명 |
|---|---|---|
| `staff` | FK → Staff (CASCADE) |  |
| `work_date` | Date | 로컬(Asia/Seoul) 일자 |
| `minutes` | UInt | 누적 분 |

UNIQUE: `(staff, work_date)`

### 자주 쓰는 쿼리
- 오늘 근무 현황
```sql
SELECT s.id, s.name, s.role, COALESCE(d.minutes,0) AS minutes_today
FROM staff s
LEFT JOIN staff_daily_hours d
  ON d.staff_id = s.id
 AND d.work_date = (now() AT TIME ZONE 'Asia/Seoul')::date
ORDER BY s.role, s.name;
```

---

# 5) 마이그레이션/의존성 적용 순서

1. `INSTALLED_APPS` 순서: `accounts`, `catalog`, `orders`, `staff` 등록
2. `makemigrations` & `migrate` (초기 스키마 생성)
3. `staff`의 트리거 마이그레이션(0002) 적용
4. `orders`의 복합 FK 마이그레이션(0002) 적용

```bash
python manage.py makemigrations accounts catalog orders staff
python manage.py migrate
python manage.py migrate staff 0002
python manage.py migrate orders 0002
```

---
