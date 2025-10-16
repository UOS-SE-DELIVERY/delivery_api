import datetime, jwt
from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework import exceptions
from .models import Staff

COOKIE_NAME = "access"
JWT_ALG = getattr(settings, "JWT_ALG", "HS256")
JWT_TTL = int(getattr(settings, "JWT_EXPIRES_SECONDS", 60*60*24*14))  # 14일
JWT_SECRET = getattr(settings, "JWT_SECRET", settings.SECRET_KEY)

def issue_access_token(staff: Staff) -> str:
    now = timezone.now()
    payload = {
        "sub": str(staff.pk),
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(seconds=JWT_TTL)).timestamp()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

    if isinstance(token, (bytes, bytearray)):
        token = token.decode("ascii")
    return token


def set_auth_cookie(response, token: str):
    secure = bool(getattr(settings, "JWT_COOKIE_SECURE", not settings.DEBUG))
    samesite = getattr(settings, "JWT_COOKIE_SAMESITE", "Lax")
    domain = getattr(settings, "JWT_COOKIE_DOMAIN", None)
    response.set_cookie(
        key=COOKIE_NAME, value=token, max_age=JWT_TTL,
        httponly=True, secure=secure, samesite=samesite, domain=domain, path="/"
    )

def clear_auth_cookie(response):
    domain = getattr(settings, "JWT_COOKIE_DOMAIN", None)
    response.delete_cookie(COOKIE_NAME, path="/", domain=domain)

class StaffJWTAuthentication(BaseAuthentication):
    """
    accounts와 동일한 JWT-쿠키 인증. sub를 Staff PK로 해석.
    """
    def authenticate(self, request):
        token = request.COOKIES.get(COOKIE_NAME)
        if not token:
            return None
        try:
            data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed("로그인이 만료되었습니다.")
        except jwt.InvalidTokenError:
            raise exceptions.AuthenticationFailed("유효하지 않은 토큰입니다(1).")

        sub = data.get("sub")
        if not sub:
            raise exceptions.AuthenticationFailed("유효하지 않은 토큰입니다(no sub).")

        try:
            user = Staff.objects.get(pk=sub, is_active=True)
        except Staff.DoesNotExist:
            raise exceptions.AuthenticationFailed("사용자를 찾을 수 없습니다.")
        return (user, token)
