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

# ----------------- Auth -----------------

class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = s.save()
        return Response({"message": "ok", "customer_id": user.customer_id}, status=201)


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


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        resp = Response(status=204)
        resp.delete_cookie(COOKIE_NAME, path="/")
        return resp


# ----------------- Me -----------------

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
