from __future__ import annotations
from typing import Any, Dict, List

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet
from rest_framework.decorators import action

from .auth import createAccessToken
from .models import Customer
from .serializers import (
    sha256_hex,
    RegisterSerializer, LoginSerializer, MeSerializer,
    ProfileUpdateSerializer, PasswordChangeSerializer,
    AddressSerializer, UsernameUpdateSerializer,
)

COOKIE_NAME = "access"
MAX_ADDRESSES = 3

def ensure_default_unique(addrs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """기본 주소가 정확히 하나(또는 0개)만 유지되도록 보정."""
    seen = False
    for a in addrs:
        if a.get("is_default"):
            if not seen:
                seen = True
            else:
                a["is_default"] = False
    if not seen and addrs:
        addrs[0]["is_default"] = True
    return addrs


# ===== drf-spectacular =====
from drf_spectacular.utils import (
    extend_schema, extend_schema_view,
    OpenApiResponse, OpenApiParameter, OpenApiExample,
    inline_serializer,
)
from rest_framework import serializers


# ----------------- Auth -----------------

@extend_schema(
    tags=['Accounts/Auth'],
    summary='회원가입',
    description=(
        '신규 고객을 생성합니다.\n'
        '- `profile_consent=False`(기본)면 개인정보(real_name/phone/address)는 저장하지 않고 파기합니다.\n'
        '- `profile_consent=True`인 경우에만 전화번호/주소 검증 후 저장합니다.'
    ),
    request=RegisterSerializer,
    responses={
        201: inline_serializer(
            name='RegisterResp',
            fields={
                'message': serializers.CharField(),
                'customer_id': serializers.IntegerField(),
            }
        ),
        400: OpenApiResponse(description='유효성 오류'),
    },
    examples=[
        OpenApiExample(
            name='요청(동의 Off: 개인정보 파기됨)',
            request_only=True,
            value={"username": "alice", "password": "VeryStrong!Pass#2025"}
        ),
        OpenApiExample(
            name='요청(동의 On: 전화/주소 저장)',
            request_only=True,
            value={
                "username": "bob",
                "password": "Another#Pass2025",
                "profile_consent": True,
                "real_name": "홍길동",
                "phone": "010-1234-5678",
                "address": {"label":"집","line":"서울 OO구 OO로 12","lat":37.57,"lng":126.98}
            }
        ),
        OpenApiExample(
            name='응답(성공)',
            response_only=True,
            value={"message": "ok", "customer_id": 42}
        ),
        OpenApiExample(
            name='응답(전화번호 형식 오류)',
            response_only=True,
            value={"detail": "전화번호 형식은 010-0000-0000 입니다."}
        ),
    ]
)
class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = s.save()
        return Response({"message": "ok", "customer_id": user.customer_id}, status=201)


@extend_schema(
    tags=['Accounts/Auth'],
    summary='로그인',
    description=(
        "성공 시 응답 JSON에 `access` 토큰이 포함되며, 동시에 "
        f"`Set-Cookie: {COOKIE_NAME}`(HTTPOnly, SameSite=Lax, 7일)이 설정됩니다.\n"
    ),
    request=LoginSerializer,
    responses={
        200: inline_serializer(
            name='LoginResp',
            fields={'access': serializers.CharField()}
        ),
        400: OpenApiResponse(description='자격 증명 오류(아이디/비밀번호)'),
    },
    examples=[
        OpenApiExample(
            name='요청',
            request_only=True,
            value={"username": "alice", "password": "VeryStrong!Pass#2025"}
        ),
        OpenApiExample(
            name='응답(성공)',
            response_only=True,
            value={"access": "JWT 토큰"}
        ),
        OpenApiExample(
            name='응답(존재하지 않는 아이디)',
            response_only=True,
            value={"detail": "존재하지 않는 아이디예요."}
        ),
        OpenApiExample(
            name='응답(비밀번호 불일치)',
            response_only=True,
            value={"detail": "비밀번호가 틀려요."}
        ),
    ]
)
class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        user: Customer = s.validated_data["user"]
        access = createAccessToken(user)

        resp = Response({"access": access}, status=200)
        resp.set_cookie(
            key=COOKIE_NAME,
            value=access,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure(),
            path="/",
            max_age=60 * 60 * 24 * 7,  # 7 days
        )
        return resp


@extend_schema(
    tags=['Accounts/Auth'],
    summary='로그아웃',
    description=f'인증 쿠키 `{COOKIE_NAME}`를 제거합니다.',
    responses={204: OpenApiResponse(description='No Content')},
    examples=[OpenApiExample(name='성공', response_only=True, value=None)]
)
class LogoutView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        resp = Response(status=204)
        resp.delete_cookie(COOKIE_NAME, path="/")
        return resp


# ----------------- Me -----------------

@extend_schema_view(
    retrieve=extend_schema(
        tags=['Accounts/Me'],
        summary='내 정보 조회',
        responses=MeSerializer,
        examples=[OpenApiExample(
            name='응답 예시',
            response_only=True,
            value={
                "customer_id": 6,
                "username": "alice",
                "real_name": "홍길동",
                "phone": "010-1234-5678",
                "addresses": [
                    {"label":"집","line":"서울 OO구 OO로 12","lat":37.57,"lng":126.98,"is_default":True}
                ],
                "loyalty_tier": "SILVER",
                "profile_consent": True,
                "profile_consent_at": "2025-10-28T09:00:00+09:00"
            }
        )]
    ),
    partial_update=extend_schema(
        tags=['Accounts/Me'],
        summary='프로필 일부 수정',
        description=(
            "- `profile_consent=False`로 바꾸면 개인정보(real_name, phone, addresses) 즉시 파기\n"
            "- 동의 Off 상태에서 real_name/phone 변경 시도 → 403"
        ),
        request=ProfileUpdateSerializer,
        responses={
            200: MeSerializer,
            403: OpenApiResponse(description='프로필 동의가 필요합니다.')
        },
        examples=[
            OpenApiExample(
                name='요청(동의 켬 + 실명/전화 수정)',
                request_only=True,
                value={"profile_consent": True, "real_name": "홍길동", "phone": "010-1111-2222"}
            ),
            OpenApiExample(
                name='요청(동의 끔)',
                request_only=True,
                value={"profile_consent": False}
            ),
        ]
    )
)
class MeViewSet(ViewSet):
    """
    /accounts/me/* 하위로 내 정보 및 프로필/주소/비번/username 변경
    """
    permission_classes = [IsAuthenticated]

    # GET /accounts/me/
    def retrieve(self, request):
        return Response(MeSerializer(request.user).data, status=200)

    # PATCH /accounts/me/
    def partial_update(self, request):
        """
        real_name / phone / profile_consent 동시 관리
        - profile_consent=False로 변경 시 개인정보 즉시 파기(real_name, phone, addresses)
        - consent Off 상태에서 real_name/phone만 변경 시도 → 403
        """
        user: Customer = request.user
        s = ProfileUpdateSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        data = s.validated_data

        changed: List[str] = []

        # 1) 동의 토글 선처리
        if "profile_consent" in data:
            want = bool(data["profile_consent"])
            if want and not user.profile_consent:
                user.profile_consent = True
                user.profile_consent_at = timezone.now()
                changed += ["profile_consent", "profile_consent_at"]
            elif (not want) and user.profile_consent:
                user.profile_consent = False
                user.profile_consent_at = None
                user.real_name = None
                user.phone = None
                user.addresses = []
                changed += ["profile_consent", "profile_consent_at", "real_name", "phone", "addresses"]

        # 2) 개인정보 반영 — 동의 On 인 경우에만
        if user.profile_consent:
            if "real_name" in data:
                user.real_name = data["real_name"]
                changed.append("real_name")
            if "phone" in data:
                user.phone = data["phone"]
                changed.append("phone")
        else:
            if ("real_name" in data) or ("phone" in data):
                return Response({"detail": "프로필 동의가 필요합니다."}, status=403)

        if changed:
            user.save(update_fields=list(set(changed)))

        return Response(MeSerializer(user).data, status=200)

    # POST /accounts/me/password/
    @extend_schema(
        tags=['Accounts/Me'],
        summary='비밀번호 변경',
        request=PasswordChangeSerializer,
        responses={
            200: inline_serializer(
                name='PasswordChangeResp',
                fields={'detail': serializers.CharField()}
            ),
            400: OpenApiResponse(description='기존 비밀번호 불일치 또는 유효성 오류')
        },
        examples=[
            OpenApiExample(
                name='요청',
                request_only=True,
                value={"old_password": "OldPass#2024", "new_password": "NewPass#2025!!"}
            ),
            OpenApiExample(
                name='응답(성공)',
                response_only=True,
                value={"detail": "비밀번호가 변경되었습니다."}
            ),
            OpenApiExample(
                name='응답(기존 비밀번호 불일치)',
                response_only=True,
                value={"detail": "기존 비밀번호가 올바르지 않습니다."}
            ),
            OpenApiExample(
                name='응답(새 비밀번호 강도 부족)',
                response_only=True,
                value={"detail": "비밀번호는 대/소문자·숫자·특수문자 중 3종류 이상을 포함해 주세요."}
            ),
            OpenApiExample(
                name='응답(새 비밀번호가 기존과 동일)',
                response_only=True,
                value={"detail": "새 비밀번호가 기존과 같습니다."}
            ),
        ]
    )
    @action(detail=False, methods=["post"], url_path="password")
    def change_password(self, request):
        user: Customer = request.user
        s = PasswordChangeSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        old_pw = s.validated_data["old_password"]
        new_pw = s.validated_data["new_password"]

        if user.password != sha256_hex(old_pw):
            return Response({"detail": "기존 비밀번호가 올바르지 않습니다."}, status=400)

        user.password = sha256_hex(new_pw)
        user.save(update_fields=["password"])
        return Response({"detail": "비밀번호가 변경되었습니다."}, status=200)

    # GET|POST /accounts/me/addresses/
    @extend_schema(
        methods=['GET'],
        tags=['Accounts/Me'],
        summary='주소 목록 조회',
        responses=inline_serializer(
            name='AddressesListResp',
            fields={'addresses': AddressSerializer(many=True)}
        ),
        examples=[OpenApiExample(
            name='응답 예시',
            response_only=True,
            value={
                "addresses": [
                    {"label":"집","line":"서울 OO구 OO로 12","lat":37.57,"lng":126.98,"is_default":True},
                    {"label":"회사","line":"서울 OO구 OO로 34","lat":37.51,"lng":127.02,"is_default":False},
                ]
            },
        )]
    )
    @extend_schema(
        methods=['POST'],
        tags=['Accounts/Me'],
        summary='주소 추가',
        description='`profile_consent=True`일 때만 추가할 수 있습니다.',
        request=AddressSerializer,
        responses={
            201: inline_serializer(
                name='AddressCreateResp',
                fields={'addresses': AddressSerializer(many=True)}
            ),
            403: OpenApiResponse(description='프로필 동의 필요'),
            400: OpenApiResponse(description='유효하지 않은 입력/최대 개수 초과'),
        },
        examples=[OpenApiExample(
            name='요청',
            request_only=True,
            value={"label":"우리집","line":"서울 OO구 OO로 12","lat":37.57,"lng":126.98,"is_default":True}
        )]
    )
    @action(detail=False, methods=["get", "post"], url_path="addresses")
    def addresses(self, request):
        user: Customer = request.user

        if request.method == "GET":
            return Response({"addresses": user.addresses or []}, status=200)

        # POST (추가)
        if not user.profile_consent:
            return Response({"detail": "프로필 동의가 필요합니다."}, status=403)

        if "line" not in request.data:
            return Response({"detail": "주소(line)는 필수입니다."}, status=400)

        s = AddressSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        addrs: List[Dict[str, Any]] = list(user.addresses or [])
        if len(addrs) >= MAX_ADDRESSES:
            return Response({"detail": f"주소는 최대 {MAX_ADDRESSES}개까지 저장할 수 있습니다."}, status=400)

        new_addr = dict(s.validated_data)
        if not new_addr.get("label"):
            new_addr["label"] = "새 장소"
        if new_addr.get("is_default"):
            for a in addrs:
                a["is_default"] = False

        addrs.append(new_addr)
        user.addresses = ensure_default_unique(addrs)
        user.save(update_fields=["addresses"])
        return Response({"addresses": user.addresses}, status=201)

    # PATCH|DELETE /accounts/me/addresses/{idx}/
    @extend_schema(
        methods=['PATCH'],
        tags=['Accounts/Me'],
        summary='주소 수정',
        parameters=[OpenApiParameter(name='idx', required=True, type=int, location=OpenApiParameter.PATH, description='수정할 주소 인덱스(0-base)')],
        request=AddressSerializer,
        responses={
            200: inline_serializer(
                name='AddressModifyResp',
                fields={'addresses': AddressSerializer(many=True)}
            ),
            403: OpenApiResponse(description='프로필 동의 필요'),
            400: OpenApiResponse(description='idx 범위 오류/유효하지 않은 입력'),
        },
    )
    @extend_schema(
        methods=['DELETE'],
        tags=['Accounts/Me'],
        summary='주소 삭제',
        parameters=[OpenApiParameter(name='idx', required=True, type=int, location=OpenApiParameter.PATH, description='삭제할 주소 인덱스(0-base)')],
        responses={
            200: inline_serializer(
                name='AddressDeleteResp',
                fields={'addresses': AddressSerializer(many=True)}
            ),
            403: OpenApiResponse(description='프로필 동의 필요'),
            400: OpenApiResponse(description='idx 범위 오류'),
        },
    )
    @action(detail=False, methods=["patch", "delete"], url_path=r"addresses/(?P<idx>\d+)")
    def modify_address(self, request, idx: str):
        user: Customer = request.user

        if not user.profile_consent:
            return Response({"detail": "프로필 동의가 필요합니다."}, status=403)

        addrs: List[Dict[str, Any]] = list(user.addresses or [])
        i = int(idx)
        if not (0 <= i < len(addrs)):
            return Response({"detail": "idx 범위를 벗어났습니다."}, status=400)

        if request.method == "DELETE":
            del addrs[i]
            user.addresses = ensure_default_unique(addrs)
            user.save(update_fields=["addresses"])
            return Response({"addresses": user.addresses}, status=200)

        # PATCH
        s = AddressSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)

        target = addrs[i]
        for f in ("label", "line", "lat", "lng"):
            if f in s.validated_data:
                target[f] = s.validated_data[f]

        # 기본 주소 전환
        if "is_default" in s.validated_data and s.validated_data["is_default"]:
            for k, a in enumerate(addrs):
                a["is_default"] = (k == i)

        user.addresses = ensure_default_unique(addrs)
        user.save(update_fields=["addresses"])
        return Response({"addresses": user.addresses}, status=200)

    # PATCH /accounts/me/addresses/{idx}/default
    @extend_schema(
        tags=['Accounts/Me'],
        summary='기본 주소 설정',
        parameters=[OpenApiParameter(name='idx', required=True, type=int, location=OpenApiParameter.PATH, description='기본으로 지정할 주소 인덱스(0-base)')],
        responses={
            200: inline_serializer(
                name='AddressSetDefaultResp',
                fields={'addresses': AddressSerializer(many=True)}
            ),
            403: OpenApiResponse(description='프로필 동의 필요'),
            400: OpenApiResponse(description='idx 범위 오류/저장된 주소 없음'),
        },
    )
    @action(detail=False, methods=["patch"], url_path=r"addresses/(?P<idx>\d+)/default")
    def set_default_address(self, request, idx: str):
        user: Customer = request.user

        if not user.profile_consent:
            return Response({"detail": "프로필 동의가 필요합니다."}, status=403)

        addrs: List[Dict[str, Any]] = list(user.addresses or [])
        i = int(idx)
        if not addrs:
            return Response({"detail": "저장된 주소가 없습니다."}, status=400)
        if not (0 <= i < len(addrs)):
            return Response({"detail": "idx 범위를 벗어났습니다."}, status=400)

        for k, a in enumerate(addrs):
            a["is_default"] = (k == i)

        user.addresses = addrs
        user.save(update_fields=["addresses"])
        return Response({"addresses": user.addresses}, status=200)

    # POST /accounts/me/username
    @extend_schema(
        tags=['Accounts/Me'],
        summary='사용자명 변경',
        description=(
            "중복/형식 검증 후 사용자명을 변경합니다. 성공 시 토큰을 재발급하고 "
            f"`Set-Cookie: {COOKIE_NAME}`를 갱신합니다."
        ),
        request=UsernameUpdateSerializer,  # fields: new_username, password
        responses=inline_serializer(
            name='UsernameChangeResp',
            fields={
                'access': serializers.CharField(),
                'username': serializers.CharField(),
            }
        ),
        examples=[
            OpenApiExample(
                name='요청',
                request_only=True,
                value={"new_username": "new_id", "password": "CurrentPass#2024"}
            ),
            OpenApiExample(
                name='응답(성공)',
                response_only=True,
                value={"access": "eyJ...", "username": "new_id"}
            ),
            OpenApiExample(
                name='응답(현재 아이디와 동일)',
                response_only=True,
                value={"detail": "현재 아이디와 동일해요."}
            ),
            OpenApiExample(
                name='응답(중복 닉네임)',
                response_only=True,
                value={"detail": "이미 사용 중인 닉네임이에요."}
            ),
            OpenApiExample(
                name='응답(잘못된 비밀번호)',
                response_only=True,
                value={"detail": "잘못된 비밀번호예요."}
            ),
        ]
    )
    @action(detail=False, methods=["post"], url_path="username")
    def change_username(self, request):
        """
        UsernameUpdateSerializer로 검증/저장 → 토큰 재발급 & 쿠키 업데이트.
        """
        user: Customer = request.user
        s = UsernameUpdateSerializer(data=request.data, context={"user": user})
        s.is_valid(raise_exception=True)

        user = s.save()  # save() 내부에서 IntegrityError → ValidationError 변환

        access = createAccessToken(user)
        resp = Response({"access": access, "username": user.username}, status=200)
        resp.set_cookie(
            key=COOKIE_NAME,
            value=access,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure(),
            path="/",
            max_age=60 * 60 * 24 * 7,
        )
        return resp
