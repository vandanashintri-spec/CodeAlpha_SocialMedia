from django.contrib import messages
from django.conf import settings
from django.shortcuts import redirect

from .models import Profile


class VerifiedEmailRequiredMiddleware:
    allowed_url_names = {
        "login",
        "logout",
        "signup",
        "verify_email",
        "verification_pending",
        "resend_verification",
        "password_reset",
        "password_reset_done",
        "password_reset_confirm",
        "password_reset_complete",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = request.user
        if request.path.startswith((settings.STATIC_URL, settings.MEDIA_URL)):
            return None
        route_name = getattr(request.resolver_match, "url_name", None)
        if (
            user.is_authenticated
            and not self._is_verified(user)
            and route_name not in self.allowed_url_names
        ):
            messages.info(
                request,
                "Verify your email address to use the Connect community.",
            )
            return redirect("verification_pending")
        return None

    @staticmethod
    def _is_verified(user):
        try:
            return user.profile.email_verified
        except Profile.DoesNotExist:
            return True
