import datetime, jwt
from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework import exceptions
from .models import Customer

# 쿠키 이름(로그인/로그아웃 뷰와 반드시 동일)
COOKIE_NAME = getattr(settings, "JWT_COOKIE_NAME", "access")

def _jwt_secret() -> str:
    return getattr(settings, "JWT_SECRET", settings.SECRET_KEY)

def _jwt_alg() -> str:
    return getattr(settings, "JWT_ALG", "HS256")

def _jwt_expires_min() -> int:
    # 기본 7일 = 10080분 (로그인 쿠키 max_age와 일치시키는 걸 권장)
    return int(getattr(settings, "JWT_EXPIRES_MIN", 60 * 24 * 7))

def createAccessToken(user: Customer) -> str:
    now = timezone.now()
    payload = {
        "sub": str(user.pk),
        "username": user.username,
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(minutes=_jwt_expires_min())).timestamp()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_jwt_alg())

def parseToken(token: str) -> dict:
    return jwt.decode(token, _jwt_secret(), algorithms=[_jwt_alg()])

class JWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        token = request.COOKIES.get(COOKIE_NAME)
        if not token:
            return None  # 익명 허용

        try:
            data = jwt.decode(token, _jwt_secret(), algorithms=[_jwt_alg()])
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed("토큰이 만료되었습니다.")
        except jwt.InvalidTokenError:
            raise exceptions.AuthenticationFailed("유효하지 않은 토큰입니다.")

        sub = data.get("sub")
        if not sub:
            raise exceptions.AuthenticationFailed("유효하지 않은 토큰입니다.")

        try:
            user = Customer.objects.get(pk=sub)
        except Customer.DoesNotExist:
            raise exceptions.AuthenticationFailed("사용자를 찾을 수 없습니다.")

        return (user, token)
