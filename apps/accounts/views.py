from __future__ import annotations
import hashlib
from typing import Any, Dict, List

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .auth import createAccessToken  # 토큰 생성 유틸 (기존 코드 사용)
from .models import Customer
from .serializers import (
    RegisterSerializer, LoginSerializer, MeSerializer,
    ContactUpdateSerializer,
    AddressCreateSerializer, AddressUpdateSerializer, SetDefaultAddressSerializer, AddressSerializer,
    ConsentUpdateSerializer, PasswordChangeSerializer,
)

MAX_ADDRESSES = 3
COOKIE_NAME = "access"


# ----------------- helpers -----------------

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def ensure_default_unique(addrs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """addresses 배열에서 is_default=True가 정확히 하나가 되도록 정리"""
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


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data, status=200)


# ----------------- Profile -----------------

class ContactUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        user: Customer = request.user
        if not user.profile_consent:
            return Response({"detail": "프로필 동의가 필요합니다."}, status=403)

        s = ContactUpdateSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)

        changed: List[str] = []
        if "real_name" in s.validated_data:
            user.real_name = s.validated_data["real_name"]
            changed.append("real_name")
        if "phone" in s.validated_data:
            user.phone = s.validated_data["phone"]
            changed.append("phone")

        if changed:
            user.save(update_fields=changed)

        return Response(MeSerializer(user).data, status=200)


# ----------------- Addresses -----------------

class AddressListCreateView(APIView):
    """GET /addresses : 주소 목록 조회
       POST /addresses: 주소 추가(최대 3개)"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user: Customer = request.user
        return Response({"addresses": user.addresses or []}, status=200)

    def post(self, request):
        user: Customer = request.user
        if not user.profile_consent:
            return Response({"detail": "프로필 동의가 필요합니다."}, status=403)

        s = AddressCreateSerializer(data=request.data)
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



class AddressDetailView(APIView):
    """PATCH/DELETE /addresses/{idx} : 단건 수정/삭제"""
    permission_classes = [IsAuthenticated]

    def patch(self, request, idx: int):
        user: Customer = request.user
        if not user.profile_consent:
            return Response({"detail": "프로필 동의가 필요합니다."}, status=403)

        addrs: List[Dict[str, Any]] = list(user.addresses or [])
        if not (0 <= idx < len(addrs)):
            return Response({"detail": "idx 범위를 벗어났습니다."}, status=400)

        s = AddressUpdateSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)

        target = addrs[idx]
        for f in ("label", "line", "lat", "lng"):
            if f in s.validated_data:
                target[f] = s.validated_data[f]

        if "is_default" in s.validated_data and s.validated_data["is_default"]:
            for i, a in enumerate(addrs):
                a["is_default"] = (i == idx)

        user.addresses = ensure_default_unique(addrs)
        user.save(update_fields=["addresses"])

        return Response({"addresses": user.addresses}, status=200)

    def delete(self, request, idx: int):
        user: Customer = request.user
        if not user.profile_consent:
            return Response({"detail": "프로필 동의가 필요합니다."}, status=403)

        addrs: List[Dict[str, Any]] = list(user.addresses or [])
        if not (0 <= idx < len(addrs)):
            return Response({"detail": "idx 범위를 벗어났습니다."}, status=400)

        del addrs[idx]
        user.addresses = ensure_default_unique(addrs)
        user.save(update_fields=["addresses"])

        return Response({"addresses": user.addresses}, status=200)


class AddressSetDefaultView(APIView):
    """PATCH /addresses/default : 기본 주소 전환(idx)"""
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        user: Customer = request.user
        if not user.profile_consent:
            return Response({"detail": "프로필 동의가 필요합니다."}, status=403)

        s = SetDefaultAddressSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        addrs: List[Dict[str, Any]] = list(user.addresses or [])
        if not addrs:
            return Response({"detail": "저장된 주소가 없습니다."}, status=400)

        i = s.validated_data["idx"]
        if not (0 <= i < len(addrs)):
            return Response({"detail": "idx 범위를 벗어났습니다."}, status=400)

        for k, a in enumerate(addrs):
            a["is_default"] = (k == i)

        user.addresses = addrs
        user.save(update_fields=["addresses"])

        return Response({"addresses": user.addresses}, status=200)


# ----------------- Consent / Password -----------------

class ConsentUpdateView(APIView):
    """PATCH /profile-consent : 동의 토글. 철회 시 개인정보 즉시 파기(주문 스냅샷은 별개)"""
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        user: Customer = request.user
        s = ConsentUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        want = s.validated_data["profile_consent"]
        if want and not user.profile_consent:
            user.profile_consent = True
            user.profile_consent_at = timezone.now()
            user.save(update_fields=["profile_consent", "profile_consent_at"])
        elif not want and user.profile_consent:
            user.profile_consent = False
            user.profile_consent_at = None
            user.real_name = None
            user.phone = None
            user.addresses = []
            user.save(update_fields=[
                "profile_consent", "profile_consent_at", "real_name", "phone", "addresses"
            ])

        return Response(MeSerializer(user).data, status=200)


class PasswordChangeView(APIView):
    """PATCH /password : 비밀번호 변경"""
    permission_classes = [IsAuthenticated]

    def patch(self, request):
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
