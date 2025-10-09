from __future__ import annotations
import re, hashlib
from typing import Any, Dict, List, Optional
from django.utils import timezone
from rest_framework import serializers
from django.db import IntegrityError
from .models import Customer

# ---------- 공통 유틸 ----------

# username: 소문자/숫자/._- 3~30자
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,29}$")
# 전화번호: 010-0000-0000
PHONE_RE = re.compile(r"^010-\d{4}-\d{4}$")

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def password_is_strong(pw: str) -> bool:
    # 길이는 view/serializer에서 최소 12자 보장
    classes = 0
    classes += bool(re.search(r"[a-z]", pw))
    classes += bool(re.search(r"[A-Z]", pw))
    classes += bool(re.search(r"\d", pw))
    classes += bool(re.search(r"[^A-Za-z0-9]", pw))
    return classes >= 3  # 4종류 중 최소 3종류

# ---------- Auth / Profile ----------

class RegisterSerializer(serializers.ModelSerializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=12)
    profile_consent = serializers.BooleanField(required=False, default=False)

    # 동의 시에만 사용
    real_name = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    phone = serializers.CharField(required=False, allow_null=True)
    address = serializers.JSONField(required=False)

    class Meta:
        model = Customer
        fields = ("username", "password", "profile_consent", "real_name", "phone", "address")

    def validate_username(self, v: str) -> str:
        u = (v or "").strip().lower()
        if not u:
            raise serializers.ValidationError("사용자명을 입력해 주세요.")
        if not USERNAME_RE.match(u):
            raise serializers.ValidationError("영문소문자/숫자/._- 조합 3~30자로 입력해 주세요.")
        if Customer.objects.filter(username=u).exists():
            raise serializers.ValidationError("이미 사용 중인 닉네임이에요.")
        return u

    def validate_password(self, pw: str) -> str:
        if not password_is_strong(pw):
            raise serializers.ValidationError("비밀번호는 대/소문자·숫자·특수문자 중 3종류 이상을 포함해 주세요.")
        return pw

    def validate(self, attrs):
        consent = bool(attrs.get("profile_consent", False))

        # 미동의: 관련 필드 파기
        if not consent:
            attrs["real_name"] = None
            attrs["phone"] = None
            attrs["address"] = None
            return attrs

        # 전화번호 형식
        phone = attrs.get("phone")
        if phone is not None and not PHONE_RE.match(phone):
            raise serializers.ValidationError({"detail": "전화번호 형식은 010-0000-0000 입니다."})

        # 주소 스키마(최초 기본 주소)
        addr = attrs.get("address")
        if addr is not None:
            if not isinstance(addr, dict):
                raise serializers.ValidationError({"detail": "주소는 객체여야 합니다."})
            line = (addr.get("line") or "").strip()
            if not line:
                raise serializers.ValidationError({"detail": "주소(line)는 필수입니다."})
            addr["line"] = line
            # 좌표 검증(있다면 범위 체크)
            if "lat" in addr and addr["lat"] is not None:
                try:
                    lat = float(addr["lat"])
                    if not (-90.0 <= lat <= 90.0):
                        raise ValueError()
                except Exception:
                    raise serializers.ValidationError({"detail": "lat 범위가 올바르지 않습니다."})
            if "lng" in addr and addr["lng"] is not None:
                try:
                    lng = float(addr["lng"])
                    if not (-180.0 <= lng <= 180.0):
                        raise ValueError()
                except Exception:
                    raise serializers.ValidationError({"detail": "lng 범위가 올바르지 않습니다."})
            if not addr.get("label"):
                addr["label"] = "집"
            addr["is_default"] = True
            attrs["address"] = addr

        return attrs

    def create(self, validated):
        consent = bool(validated.pop("profile_consent", False))
        raw_pw = validated.pop("password")
        username = validated["username"]
        addr = validated.pop("address", None)
        addresses = [addr] if (consent and addr) else []

        try:
            return Customer.objects.create(
                username=username,
                password=sha256_hex(raw_pw),
                profile_consent=consent,
                profile_consent_at=timezone.now() if consent else None,
                real_name=validated.get("real_name"),
                phone=validated.get("phone"),
                addresses=addresses,  # 모델 제약: JSON 배열 & 최대 3개 체크 있음
            )
        except IntegrityError:
            # 경합으로 인한 중복
            raise serializers.ValidationError({"detail": "이미 사용중인 닉네임이에요."})

class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        u, p = attrs["username"], attrs["password"]
        try:
            user = Customer.objects.get(username=u.strip().lower())
        except Customer.DoesNotExist:
            raise serializers.ValidationError({"detail": "존재하지 않는 아이디예요."})
        if user.password != sha256_hex(p):
            raise serializers.ValidationError({"detail": "비밀번호가 틀려요."})
        attrs["user"] = user
        return attrs

class MeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = (
            "customer_id", "username", "real_name", "phone",
            "addresses", "loyalty_tier", "profile_consent", "profile_consent_at"
        )
        read_only_fields = fields

class ContactUpdateSerializer(serializers.Serializer):
    real_name = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    phone = serializers.CharField(required=False, allow_null=True)

    def validate_phone(self, v):
        if v is None:
            return None
        if not PHONE_RE.match(v):
            raise serializers.ValidationError("전화번호 형식은 010-0000-0000 입니다.")
        return v

# ---------- Address ----------

class AddressSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=32, required=False, allow_blank=True)
    line = serializers.CharField(max_length=255)
    lat = serializers.FloatField(required=False, allow_null=True)
    lng = serializers.FloatField(required=False, allow_null=True)
    is_default = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        # 좌표 범위 체크(있다면)
        if attrs.get("lat") is not None and not (-90.0 <= float(attrs["lat"]) <= 90.0):
            raise serializers.ValidationError({"detail": "lat 범위가 올바르지 않습니다."})
        if attrs.get("lng") is not None and not (-180.0 <= float(attrs["lng"]) <= 180.0):
            raise serializers.ValidationError({"detail": "lng 범위가 올바르지 않습니다."})
        attrs["line"] = (attrs.get("line") or "").strip()
        if not attrs["line"]:
            raise serializers.ValidationError({"detail": "주소(line)는 필수입니다."})
        if "label" in attrs:
            attrs["label"] = (attrs["label"] or "").strip()
        return attrs

class AddressCreateSerializer(AddressSerializer):
    pass

class AddressUpdateSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=32, required=False, allow_blank=True)
    line = serializers.CharField(max_length=255, required=False)
    lat = serializers.FloatField(required=False, allow_null=True)
    lng = serializers.FloatField(required=False, allow_null=True)
    is_default = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if "lat" in attrs and attrs["lat"] is not None and not (-90.0 <= float(attrs["lat"]) <= 90.0):
            raise serializers.ValidationError({"detail": "lat 범위가 올바르지 않습니다."})
        if "lng" in attrs and attrs["lng"] is not None and not (-180.0 <= float(attrs["lng"]) <= 180.0):
            raise serializers.ValidationError({"detail": "lng 범위가 올바르지 않습니다."})
        if "line" in attrs:
            line = (attrs["line"] or "").strip()
            if not line:
                raise serializers.ValidationError({"detail": "주소(line)는 비워둘 수 없습니다."})
            attrs["line"] = line
        if "label" in attrs:
            attrs["label"] = (attrs["label"] or "").strip()
        return attrs

class SetDefaultAddressSerializer(serializers.Serializer):
    idx = serializers.IntegerField()

# ---------- Consent / Password ----------

class ConsentUpdateSerializer(serializers.Serializer):
    profile_consent = serializers.BooleanField()

class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=12)

    def validate(self, attrs):
        if attrs["old_password"] == attrs["new_password"]:
            raise serializers.ValidationError({"detail": "새 비밀번호가 기존과 같습니다."})
        if not password_is_strong(attrs["new_password"]):
            raise serializers.ValidationError({"detail": "비밀번호는 대/소문자·숫자·특수문자 중 3종류 이상을 포함해 주세요."})
        return attrs
