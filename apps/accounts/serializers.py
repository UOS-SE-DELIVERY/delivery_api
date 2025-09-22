from __future__ import annotations
import re, hashlib
from typing import Any, Dict, List, Optional
from django.utils import timezone
from rest_framework import serializers
from .models import Customer
from django.db import IntegrityError

PHONE_RE = re.compile(r"^010-\d{4}-\d{4}")

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

class RegisterSerializer(serializers.ModelSerializer):
    ### 필수 입력 필드 ###
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=12)
    profile_consent = serializers.BooleanField(required=False, default=False)

    ### 동의 시 입력 필드 ###
    real_name = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    phone = serializers.CharField(required=False, allow_null=True)
    address = serializers.JSONField(required=False)

    class Meta:
        model = Customer
        fields = ("username", "password", "profile_consent", "real_name", "phone", "address")

    def validate(self, attrs):
        username = attrs.get("username").strip()

        if not username:
            raise serializers.ValidationError({"error": "사용자명을 입력해 주세요."})
        if Customer.objects.filter(username=username).exists():
            raise serializers.ValidationError({"error": "이미 사용 중인 닉네임이에요."})
        
        attrs["usernmae"] = username

        consent = attrs.get("profile_consent", False)

        ### 미동의시 파기 ###
        if not consent:
            attrs["real_name"] = None
            attrs["phone"] = None
            attrs["address"] = None
            return attrs
        
        ### 전화번호 검증 ###
        phone = attrs.get("phone")
        if phone is not None and not PHONE_RE.match(phone):
            raise serializers.ValidationError({"phone": "전화번호 형식은 010-0000-0000 입니다."})
        
        ### 주소 검증 및 기본 주소로 설정(처음 주소이므로) ###
        addr = attrs.get("address")
        if addr is not None:
            if not isinstance(addr, dict):
                raise serializers.ValidationError({"address": "객체가 아닙니다."})
            if not addr.get("line"):
                raise serializers.ValidationError({"address.line": "주소는 필수입니다."})
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
                username = username,
                password = sha256_hex(raw_pw),
                profile_consent = consent,
                profile_consent_at = timezone.now() if consent else None,
                real_name = validated.get("real_name"),
                phone = validated.get("phone"),
                addresses = addresses,
            )
        except IntegrityError:
            raise serializers.ValidationError({"error": "이미 사용중인 닉네임이에요"})
    
class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        u, p = attrs["username"], attrs["password"]
        try:
            user = Customer.objects.get(username=u)
        except Customer.DoesNotExist:
            raise serializers.ValidationError("존재하지 않는 아이디예요.")
        
        if user.password != sha256_hex(p):
            raise serializers.ValidationError("비밀번호가 틀려요.")
        
        attrs["user"] = user
        return attrs
    
class MeSerializer(serializers.ModelSerializer):
    ### GET /api/auth/me 응답용 (읽기 전용) ###
    class Meta:
        model = Customer
        fields = (
            "customer_id", "username", "real_name", "phone", "addresses", "loyalty_tier", "profile_consent", "profile_consent_at"
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
    
class AddressCreateSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=32, required=False, allow_blank=True)
    line = serializers.CharField(max_length=255)
    lat = serializers.FloatField(required=False, allow_null=True)
    lng = serializers.FloatField(required=False, allow_null=True)
    is_default = serializers.BooleanField(required=False, default=False)

class SetDefaultAddressSerializer(serializers.Serializer):
    idx = serializers.IntegerField(required=False)
    # lable = serializers.CharField(required=False, max_length=32)

    ### label은 안됨.. 왜 안되는지는 모르겠음
    ### 0 1 2 index로 기본 주소 변경은 가능
    ### label로 변경하는 건 빼도 될듯?
    def validate(self, attrs):
        if "idx" in attrs:
            return attrs
        
        # if "label" in attrs:
        #     lbl = (attrs.get("label") or "").strip()
        #     if not lbl:
        #         raise serializers.ValidationError({"error": "label에 빈 문자열을 사용할 수 없어요."})
        #     attrs["label"] = lbl
        #     return attrs
        
        raise serializers.ValidationError({"error": "idx 또는 label은 필수입니다."})
    
class ConsentUpdateSerializer(serializers.Serializer):
    profile_consent = serializers.BooleanField()

