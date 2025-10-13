# apps/accounts/serializers.py
from __future__ import annotations
import re, hashlib
from typing import Any, Dict, Optional
from django.utils import timezone
from rest_framework import serializers
from django.db import IntegrityError
from .models import Customer

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,29}$")
PHONE_RE = re.compile(r"^010-\d{4}-\d{4}$")

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def is_password_strong(pw: str) -> bool:
    classes = 0
    classes += bool(re.search(r"[a-z]", pw))
    classes += bool(re.search(r"[A-Z]", pw))
    classes += bool(re.search(r"\d", pw))
    classes += bool(re.search(r"[^A-Za-z0-9]", pw))
    return classes >= 3

# 회원가입 Serializer
class RegisterSerializer(serializers.ModelSerializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=12)
    profile_consent = serializers.BooleanField(required=False, default=False)

    # 미 동의 시 파기할 목록 --> 동의 시에만 저장
    real_name = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    phone = serializers.CharField(required=False, allow_null=True)
    address = serializers.JSONField(required=False)

    class Meta:
        model = Customer
        fields = ("username", "password", "profile_consent", "real_name", "phone", "address")

    # username 검증
    def validate_username(self, v: str) -> str:
        u = (v or "").strip().lower()
        if not u or not USERNAME_RE.match(u):
            raise serializers.ValidationError("영문소문자/숫자/._- 조합 3~30자로 입력해 주세요.")
        if Customer.objects.filter(username=u).exists():
            raise serializers.ValidationError("이미 사용 중인 닉네임이에요.")
        return u

    # 비밀번호 강도 검증
    def validate_password(self, pw: str) -> str:
        if not is_password_strong(pw):
            raise serializers.ValidationError("비밀번호는 대/소문자·숫자·특수문자 중 3종류 이상을 포함해 주세요.")
        return pw

    # 나머지 항목 검증
    def validate(self, attrs):
        consent = bool(attrs.get("profile_consent", False))
        # 미동의시 파기 #
        if not consent:
            attrs["real_name"] = None
            attrs["phone"] = None
            attrs["address"] = None
            return attrs

        # 전화번호 형식 검증 #
        phone = attrs.get("phone")
        if phone is not None and not PHONE_RE.match(phone):
            raise serializers.ValidationError({"detail": "전화번호 형식은 010-0000-0000 입니다."})

        # 주소 형식 및 값 검증 #
        addr = attrs.get("address")
        if addr is not None:
            if not isinstance(addr, dict):
                raise serializers.ValidationError({"detail": "주소는 객체여야 합니다."})
            line = (addr.get("line") or "").strip()
            if not line:
                raise serializers.ValidationError({"detail": "주소(line)는 필수입니다."})
            addr["line"] = line
            if "lat" in addr and addr["lat"] is not None:
                lat = float(addr["lat"])
                if not (-90.0 <= lat <= 90.0):
                    raise serializers.ValidationError({"detail": "lat 범위가 올바르지 않습니다."})
            if "lng" in addr and addr["lng"] is not None:
                lng = float(addr["lng"])
                if not (-180.0 <= lng <= 180.0):
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
                addresses=addresses,
            )
        except IntegrityError:
            raise serializers.ValidationError({"detail": "이미 사용중인 닉네임이에요."})

# 로그인 Serializer
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

# 프로필 정보 가져오기 Serializer
class MeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ("customer_id","username","real_name","phone","addresses",
                  "loyalty_tier","profile_consent","profile_consent_at")
        read_only_fields = fields

# username 제외 프로필 업데이트 Serializer
class ProfileUpdateSerializer(serializers.Serializer):
    # username 변경은 의도적으로 뺌. username이 unique key이고, JWT에 username이 claim으로 들어가 있어서
    # 변경 즉시 재발급 해야됨 --> 볼륨 커짐, 따로 뺌.
    real_name = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    phone = serializers.CharField(required=False, allow_null=True)
    profile_consent = serializers.BooleanField(required=False)

    def validate_phone(self, v):
        if v is None:
            return None
        if not PHONE_RE.match(v):
            raise serializers.ValidationError("전화번호 형식은 010-0000-0000 입니다.")
        return v
    
# username 업데이트 Serializer
class UsernameUpdateSerializer(serializers.Serializer):
    new_username = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_new_username(self, v: str) -> str:
        u = (v or "").strip().lower()

        if not USERNAME_RE.match(u):
            raise serializers.ValidationError("영문소문자/숫자/._- 조합 3~30자로 입력해 주세요.")
        
        user = (self.context.get("user") or (self.context.get("request").user if self.context.get("request") else None))
        if user and u == user.username:
            raise serializers.ValidationError("현재 아이디와 동일해요.")
        
        qs = Customer.objects.filter(username=u)
        if user:
            qs = qs.exclude(pk=user.pk)
        if qs.exists():
            raise serializers.ValidationError("이미 사용 중인 닉네임이에요.")
        
        return u
    
    def validate(self, attrs):
        user = (self.context.get("user") or (self.context.get("request").user if self.context.get("request") else None))
        if not user:
            raise serializers.ValidationError({"detail": "사용자 컨텍스트가 필요해요."})
        
        pw = attrs.get("password")
        if not pw:
            raise serializers.ValidationError({"detail": "password를 입력해 주세요."})
        
        ok = (user.password == sha256_hex(pw))
        if not ok:
            raise serializers.ValidationError({"detail": "잘못된 비밀번호예요."})
        
        return attrs
    
    def save(self, **kwargs):
        user = (self.context.get("user") or (self.context.get("request").user if self.context.get("request") else None))
        user.username = self.validated_data["new_username"]
        try:
            user.save(update_fields=["username"])
        # race 방지 #
        except IntegrityError:
            raise serializers.ValidationError({"detail": "이미 사용 중인 닉네임이에요."})
        
        return user

# 주소 Serializer 
class AddressSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=32, required=False, allow_blank=True) # 집, 일터 등 라벨
    line = serializers.CharField(max_length=255, required=False) # 실제 주소, create 시에는 강제 체크
    lat = serializers.FloatField(required=False, allow_null=True) 
    lng = serializers.FloatField(required=False, allow_null=True)
    is_default = serializers.BooleanField(required=False, default=False) # 기본 주소인지?

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
        return attrs

# 비밀번호 변경 serializer
class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=12)

    def validate(self, attrs):
        if attrs["old_password"] == attrs["new_password"]:
            raise serializers.ValidationError({"detail": "새 비밀번호가 기존과 같습니다."})
        if not is_password_strong(attrs["new_password"]):
            raise serializers.ValidationError({"detail": "비밀번호는 대/소문자·숫자·특수문자 중 3종류 이상을 포함해 주세요."})
        return attrs
