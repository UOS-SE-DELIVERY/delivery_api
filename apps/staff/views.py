from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.http import StreamingHttpResponse, HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views import View

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import Staff
from .auth import StaffJWTAuthentication, issue_access_token, set_auth_cookie, clear_auth_cookie
from .permissions import IsOwnerOrManager
from .serializers import (
    StaffLoginSerializer,
    CouponSerializer, MembershipSerializer,
    StaffMeSerializer,
    StaffOrderDetailSerializer,
)
from apps.promotion.models import Coupon, Membership
from apps.orders.models import Order
from .eventbus import iter_order_notifications
import json

# ---------- Auth ----------
class StaffLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = StaffLoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        username = (ser.validated_data["username"] or "").strip()
        password = ser.validated_data["password"]

        staff = Staff.objects.filter(username__iexact=username).first()
        if not staff or not staff.check_password(password):
            return Response({"detail": "아이디 또는 비밀번호가 올바르지 않습니다."},
                            status=status.HTTP_400_BAD_REQUEST)

        token = issue_access_token(staff)
        resp = Response({"status": True, "staff_id": staff.pk})
        set_auth_cookie(resp, token)
        return resp

class StaffLogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        resp = Response({"status": True})
        clear_auth_cookie(resp)
        return resp

# ---------- Me ----------
class StaffMeView(APIView):
    authentication_classes = []  # 수동 인증으로 403/200 제어

    def get(self, request):
        auth = StaffJWTAuthentication()
        res = auth.authenticate(request)
        if not res:
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        user, _ = res
        return Response(StaffMeSerializer(user).data, status=status.HTTP_200_OK)

# ---------- Coupons ----------
class CouponsView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Coupon.objects.order_by("-valid_from", "code")
        return Response(CouponSerializer(qs, many=True).data)

    def post(self, request):
        if not IsOwnerOrManager().has_permission(request, self):
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        ser = CouponSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obj = ser.save()
        return Response(CouponSerializer(obj).data, status=status.HTTP_201_CREATED)

class CouponDetailView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_object(self, code: str) -> Coupon:
        return get_object_or_404(Coupon, code=code.upper())

    def get(self, request, code: str):
        obj = self.get_object(code)
        return Response(CouponSerializer(obj).data)

    def patch(self, request, code: str):
        if not IsOwnerOrManager().has_permission(request, self):
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        obj = self.get_object(code)
        ser = CouponSerializer(instance=obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(CouponSerializer(obj).data)

    def delete(self, request, code: str):
        if not IsOwnerOrManager().has_permission(request, self):
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        obj = self.get_object(code)
        obj.active = False
        obj.save(update_fields=["active"])
        return Response(status=status.HTTP_204_NO_CONTENT)

# ---------- Memberships ----------
class MembershipsView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        customer_id = request.query_params.get("customer")
        qs = Membership.objects.all().order_by("customer_id")
        if customer_id:
            qs = qs.filter(customer_id=customer_id)
        return Response(MembershipSerializer(qs, many=True).data)

    def post(self, request):
        if not IsOwnerOrManager().has_permission(request, self):
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        ser = MembershipSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if Membership.objects.filter(customer=ser.validated_data["customer"]).exists():
            return Response({"detail": "이미 해당 고객의 멤버십이 존재합니다."}, status=status.HTTP_400_BAD_REQUEST)
        obj = ser.save()
        return Response(MembershipSerializer(obj).data, status=status.HTTP_201_CREATED)

class MembershipDetailView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_object(self, customer_id: int) -> Membership:
        return get_object_or_404(Membership, customer_id=customer_id)

    def get(self, request, customer_id: int):
        obj = self.get_object(customer_id)
        return Response(MembershipSerializer(obj).data)

    def patch(self, request, customer_id: int):
        if not IsOwnerOrManager().has_permission(request, self):
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        obj = self.get_object(customer_id)
        ser = MembershipSerializer(instance=obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        if "customer" in ser.validated_data and ser.validated_data["customer"].pk != obj.customer_id:
            return Response({"detail": "customer는 변경할 수 없습니다."}, status=status.HTTP_400_BAD_REQUEST)
        ser.save()
        return Response(MembershipSerializer(obj).data)

    def delete(self, request, customer_id: int):
        if not IsOwnerOrManager().has_permission(request, self):
            return Response({"detail": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)
        obj = self.get_object(customer_id)
        obj.active = False
        obj.save(update_fields=["active"])
        return Response(status=status.HTTP_204_NO_CONTENT)

# ---------- Orders: 단건 상세 ----------
class StaffOrderDetailView(APIView):
    """
    GET /api/staff/orders/<int:order_id>
    인증: StaffJWTAuthentication (access 쿠키)
    응답: 주문 기본 + dinners + dinner.items(+options) + dinner.options + 쿠폰 사용 + 멤버십 요약
    """
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id: int):
        order = (
            Order.objects
            .select_related("customer")
            .prefetch_related(
                "dinners__items__item",
                "dinners__items__options",
                "dinners__options",
                "coupon_redemptions__coupon",
            )
            .get(pk=order_id)
        )
        data = StaffOrderDetailSerializer(order).data
        return Response(data, status=status.HTTP_200_OK)

# ---------- SSE (Orders) ----------
def _sse_headers(resp: StreamingHttpResponse) -> StreamingHttpResponse:
    resp["Content-Type"] = "text/event-stream; charset=utf-8"
    resp["Cache-Control"] = "no-cache, no-transform"

    return resp

@method_decorator(csrf_exempt, name="dispatch")
class OrdersSSEView(View):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def dispatch(self, request, *args, **kwargs):
        # 수동 인증(StreamingHttpResponse + DRF 혼용 시 안전)
        authed = False
        for cls in self.authentication_classes:
            inst = cls()
            res = inst.authenticate(request)
            if res:
                request.user, request.auth = res
                authed = True
                break
        if not authed:
            return HttpResponse("Unauthorized", status=401)
        for perm in self.permission_classes:
            if not perm().has_permission(request, self):
                return HttpResponse("Forbidden", status=403)
        return super().dispatch(request, *args, **kwargs)

    def _bootstrap(self, request):
        status_param = (request.GET.get("status") or "").strip()
        since_param = (request.GET.get("since") or "").strip()
        limit = int(request.GET.get("limit") or 20)
        limit = max(1, min(limit, 100))

        qs = Order.objects.all().order_by("-ordered_at")
        if status_param:
            statuses = [s.strip() for s in status_param.split(",") if s.strip()]
            qs = qs.filter(status__in=statuses)
        if since_param:
            dt = parse_datetime(since_param)
            if dt:
                qs = qs.filter(ordered_at__gte=dt)

        out = []
        for o in qs[:limit]:
            out.append({
                "id": o.id,
                "status": o.status,
                "ordered_at": o.ordered_at.isoformat(),
                "customer_id": o.customer_id,
                "order_source": o.order_source,
                "subtotal_cents": o.subtotal_cents,
                "total_cents": o.total_cents,
                "receiver_name": o.receiver_name,
                "place_label": o.place_label,
            })
        return out

    def get(self, request):
        def stream():
            # 1) 반드시 첫 프레임을 즉시 보냄 (여기서 헤더/첫 바이트가 전송됨)
            yield 'event: ready\ndata: {"ok": true}\n\n'

            # 2) 현재 스냅샷 제공(가볍게)
            try:
                snap = self._bootstrap(request)
                yield "event: bootstrap\ndata: " + json.dumps({"orders": snap}) + "\n\n"
            except Exception as e:
                yield "event: error\ndata: " + json.dumps({"message": f"bootstrap failed: {e}"}) + "\n\n"

            # 3) 이후부터 실시간 이벤트
            try:
                for payload in iter_order_notifications():
                    # payload 예: {"event":"order_created","order_id":10,...}
                    if isinstance(payload, str):
                        yield "event: message\ndata: " + json.dumps({"raw": payload}) + "\n\n"
                    else:
                        ev = payload.get("event") or "message"
                        yield f"event: {ev}\n" + "data: " + json.dumps(payload) + "\n\n"
            except Exception as e:
                yield "event: error\ndata: " + json.dumps({"message": f"stream error: {e}"}) + "\n\n"

        return _sse_headers(StreamingHttpResponse(stream()))