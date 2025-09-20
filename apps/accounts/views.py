from typing import Any, Dict, List
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from .serializers import (
    SignupSerializer, LoginSerializer, MeSerializer,
    ContactUpdateSerializer, AddressCreateSerializer, SetDefaultAddressSerializer,
    ConsentUpdateSerializer
)
from .auth import createAccessToken
from .models import Customer

def ensureDefaultUnique(addrs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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

class SignupView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        ser = SignupSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()

        return Response({"message": "ok", "customer_id": user.pk}, status=201) # 201 CREATED
    
class LoginView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        ser = LoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.validated_data["user"]
        access = createAccessToken(user)

        resp = Response({"access": access}, status=200)
        resp.set_cookie(
            key="token",
            value=access,
            httponly=True,
            path="/",
        )

        return resp
    
class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        resp = Response(status=204)
        resp.delete_cookie("access_token", path="/")
        
        return resp
    
class MeView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        return Response(MeSerializer(request.user).data, status=200)

# class MeView(APIView):
#     permission_classes = [IsAuthenticated]
#     def get(self, request):
#         u = request.user
#         return Response({
#             "customer_id": u.customer_id,
#             "username": getattr(u, "username", None),
#             "real_name": getattr(u, "real_name", None),
#             "phone": getattr(u, "phone", None),
#             "addresses": getattr(u, "addresses", []),
#             "loyalty_tier": getattr(u, "loyalty_tier", None),
#             "profile_consent": getattr(u, "profile_consent", None),
#             "profile_consent_at": getattr(u, "profile_consent_at", None),
#         }, status=200)
    
class ContactUpdateView(APIView):
    permission_classes = [IsAuthenticated]
    
    def patch(self, request):
        user: Customer = request.user
        
        if not user.profile_consent:
            return Response({"error": "프로필 동의가 필요합니다."}, status=403) # 403 Forbidden
        
        ser = ContactUpdateSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        changed: List[str] = []

        if "real_name" in ser.validated_data:
            user.real_name = ser.validated_data["real_name"]
            changed.append("real_name")

        if "phone" in ser.validated_data:
            user.phone = ser.validated_data["phone"]
            changed.append("phone")

        if changed:
            user.save(update_fields=changed)

        return Response(MeSerializer(user).data, status=200) # 200 OK
    
class AddressCreateView(APIView):
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        user: Customer = request.user
        
        if not user.profile_consent:
            return Response({"error": "프로필 동의가 필요합니다."}, status=403)
        
        ser = AddressCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        addrs: List[Dict[str, Any]] = list(user.addresses or [])
        if len(addrs) >= 3:
            return Response({"error": "주소는 최대 3개까지 저장할 수 있습니다."}, status=400) # 400 or what?
        
        new_addr = dict(ser.validated_data)
        if not new_addr.get("label"):
            new_addr["label"] = "새 장소"

        if new_addr.get("is_default"):
            for a in addrs:
                a["is_default"] = False

        addrs.append(new_addr)
        user.addresses = ensureDefaultUnique(addrs)
        user.save(update_fields=["addresses"])

        return Response({"addresses": user.addresses}, status=201)

class AddressSetDefaultView(APIView):
    ### 기본 주소 전환(idx 또는 label) ###
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        user: Customer = request.user

        if not user.profile_consent:
            return Response({"detail": "프로필 동의가 필요합니다."}, status=403)
        
        ser = SetDefaultAddressSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        addrs: List[Dict[str, Any]] = list(user.addresses or [])
        if not addrs:
            return Response({"detail": "저장된 주소가 없습니다."}, status=400)

        target_idx = None
        if "idx" in ser.validated_data and ser.validated_data["idx"] is not None:
            i = ser.validated_data["idx"]
            if not (0 <= i < len(addrs)):
                return Response({"detail": "idx 범위를 벗어났습니다."}, status=400)
            target_idx = i

        # elif "label" in ser.validated_data:
        #     label = ser.validated_data["label"]
        #     for i, a in enumerate(addrs):
        #         if a.get("label") == label:
        #             target_idx = i
        #             break
        #     if target_idx is None:
        #         return Response({"detail": "label에 해당하는 주소가 없습니다."}, status=404)

        for i, a in enumerate(addrs):
            a["is_default"] = (i == target_idx)

        user.addresses = addrs
        user.save(update_fields=["addresses"])

        return Response({"addresses": user.addresses}, status=200)

class ConsentUpdateView(APIView):
    ### 동의 토글. 철회 시 개인정보 즉시 파기(주문 스냅샷은 별개) ###
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        user: Customer = request.user
        ser = ConsentUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        want = ser.validated_data["profile_consent"]

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
                "profile_consent","profile_consent_at",
                "real_name","phone","addresses"
            ])

        return Response(MeSerializer(user).data, status=200) 