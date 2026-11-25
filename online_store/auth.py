from functools import wraps
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from rest_framework import exceptions
from rest_framework.authentication import CSRFCheck
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken


User = get_user_model()


def set_jwt_cookies(response, user):
    """Issue fresh JWT refresh + access tokens in HttpOnly cookies."""
    refresh = RefreshToken.for_user(user)
    access_max_age = int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds())
    refresh_max_age = int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds())

    response.set_cookie(
        key=settings.JWT_COOKIE_ACCESS,
        value=str(refresh.access_token),
        max_age=access_max_age,
        httponly=True,
        secure=settings.JWT_COOKIE_SECURE,
        samesite=settings.JWT_COOKIE_SAMESITE,
        path='/',
    )
    response.set_cookie(
        key=settings.JWT_COOKIE_REFRESH,
        value=str(refresh),
        max_age=refresh_max_age,
        httponly=True,
        secure=settings.JWT_COOKIE_SECURE,
        samesite=settings.JWT_COOKIE_SAMESITE,
        path='/',
    )
    return refresh


def clear_jwt_cookies(response):
    """Remove JWT cookies on logout."""
    response.delete_cookie(settings.JWT_COOKIE_ACCESS, path='/')
    response.delete_cookie(settings.JWT_COOKIE_REFRESH, path='/')
    return response


def safe_redirect_url(request, raw_url: str, fallback: str = 'home') -> str:
    """Return raw_url only when it points to an allowed host/scheme; otherwise return fallback."""
    candidate = (raw_url or '').strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


class JWTCookieAuthentication(JWTAuthentication):
    """DRF authentication checking Authorization header first, then HttpOnly cookie."""

    def _enforce_csrf(self, request):
        check = CSRFCheck(lambda req: None)
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise exceptions.PermissionDenied(f'CSRF Failed: {reason}')

    def authenticate(self, request):
        header_auth = super().authenticate(request)
        if header_auth is not None:
            return header_auth

        raw_token = request.COOKIES.get(settings.JWT_COOKIE_ACCESS)
        if not raw_token:
            return None

        try:
            validated_token = self.get_validated_token(raw_token)
            user = self.get_user(validated_token)
        except (InvalidToken, TokenError):
            return None

        self._enforce_csrf(request)
        return user, validated_token


class JWTCookieMiddleware:
    """Populate request.user from JWT cookies for HTML template views."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rotated_tokens = None

        if not request.path.startswith('/admin/'):
            user, rotated_tokens = self._authenticate_from_cookies(request)
            if user is not None:
                request.user = user
            elif not getattr(request, 'user', None):
                request.user = AnonymousUser()
        elif not getattr(request, 'user', None) or not request.user.is_authenticated:
            user, rotated_tokens = self._authenticate_from_cookies(request)
            if user is not None:
                request.user = user
            elif not getattr(request, 'user', None):
                request.user = AnonymousUser()

        response = self.get_response(request)

        if rotated_tokens is not None:
            access_str, refresh_str = rotated_tokens
            access_max_age = int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds())
            refresh_max_age = int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds())
            response.set_cookie(
                key=settings.JWT_COOKIE_ACCESS,
                value=access_str,
                max_age=access_max_age,
                httponly=True,
                secure=settings.JWT_COOKIE_SECURE,
                samesite=settings.JWT_COOKIE_SAMESITE,
                path='/',
            )
            response.set_cookie(
                key=settings.JWT_COOKIE_REFRESH,
                value=refresh_str,
                max_age=refresh_max_age,
                httponly=True,
                secure=settings.JWT_COOKIE_SECURE,
                samesite=settings.JWT_COOKIE_SAMESITE,
                path='/',
            )

        return response

    def _authenticate_from_cookies(self, request):
        raw_access = request.COOKIES.get(settings.JWT_COOKIE_ACCESS)
        if raw_access:
            try:
                token = AccessToken(raw_access)
                user = User.objects.filter(id=token.get('user_id'), is_active=True).first()
                if user is not None:
                    return user, None
            except TokenError:
                pass

        raw_refresh = request.COOKIES.get(settings.JWT_COOKIE_REFRESH)
        if raw_refresh:
            try:
                old_refresh = RefreshToken(raw_refresh)
                user = User.objects.filter(id=old_refresh.get('user_id'), is_active=True).first()
                if user is not None:
                    if settings.SIMPLE_JWT.get('BLACKLIST_AFTER_ROTATION'):
                        try:
                            old_refresh.blacklist()
                        except Exception:
                            pass
                    new_refresh = RefreshToken.for_user(user)
                    return user, (str(new_refresh.access_token), str(new_refresh))
            except TokenError:
                pass

        return None, None


def jwt_login_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            next_path = safe_redirect_url(request, request.get_full_path(), fallback='')
            if next_path:
                return redirect(f"{settings.LOGIN_URL}?next={quote(next_path, safe='/:?=&')}")
            return redirect(settings.LOGIN_URL)
        return view_func(request, *args, **kwargs)
    return _wrapped


def wholesaler_required(view_func):
    @wraps(view_func)
    @jwt_login_required
    def _wrapped(request, *args, **kwargs):
        if not getattr(request.user, 'is_wholesaler', False):
            messages.error(request, 'This section is restricted to Wholesaler accounts.')
            return redirect('home')
        return view_func(request, *args, **kwargs)
    return _wrapped


def retailer_required(view_func):
    @wraps(view_func)
    @jwt_login_required
    def _wrapped(request, *args, **kwargs):
        if not getattr(request.user, 'is_retailer', False):
            messages.error(request, 'This section is restricted to Retailer accounts.')
            return redirect('home')
        return view_func(request, *args, **kwargs)
    return _wrapped
