import datetime, jwt
from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework import exceptions
from .models import Customer

def _jwt_secret(): return getattr(settings, "JWT_SECRET", settings.SECRET_KEY)
def _jwt_alg(): return getattr(settings, "JWT_ALG", "HS256")
def _jwt_expires_min(): return int(getattr(settings, "JWT_EXPIRES_MIN", 120))

def createAccessToken(user: Customer) -> str:
    now = timezone.now()
    payload = {
        "sub": str(user.pk),
        "username": user.username,
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(minutes=_jwt_expires_min())).timestamp()),
        "typ": "access"
    }

    return jwt.encode(payload, _jwt_secret(), algorithm=_jwt_alg())

def parseToken(token: str) -> dict:
    return jwt.decode(token, _jwt_secret(), algorithms=_jwt_alg())

class JWTAuthentication(BaseAuthentication):
    # keyword = "Bearer"

    def authenticate(self, request):
        # # 1) Authorization 헤더 우선
        # auth = request.headers.get("Authorization")
        token = None
        # if auth and auth.startswith(self.keyword + " "):
        #     token = auth.split(" ", 1)[1].strip()

        # 2) 없으면 쿠키에서 시도
        if not token:
            token = request.COOKIES.get("token")

        if not token:
            return None

        try:
            data = jwt.decode(token, _jwt_secret(), algorithms=[_jwt_alg()])
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed("토큰이 만료되었습니다.")
        except jwt.InvalidTokenError:
            raise exceptions.AuthenticationFailed("유효하지 않은 토큰입니다.")

        try:
            user = Customer.objects.get(pk=data.get("sub"))
        except Customer.DoesNotExist:
            raise exceptions.AuthenticationFailed("사용자를 찾을 수 없습니다.")

        return (user, token)
