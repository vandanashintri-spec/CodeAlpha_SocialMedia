from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy
from django.urls import path

from . import views
from .forms import EmailVerifiedAuthenticationForm

urlpatterns = [
    path("", views.feed, name="feed"),
    path("discover/", views.discover, name="discover"),
    path("signup/", views.signup, name="signup"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            authentication_form=EmailVerifiedAuthenticationForm,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "verify-email/sent/",
        views.verification_pending,
        name="verification_pending",
    ),
    path(
        "verify-email/resend/",
        views.resend_verification,
        name="resend_verification",
    ),
    path(
        "verify-email/<uidb64>/<token>/",
        views.verify_email,
        name="verify_email",
    ),
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="registration/password_reset_form.html",
            email_template_name="registration/password_reset_email.txt",
            subject_template_name="registration/password_reset_subject.txt",
            success_url=reverse_lazy("password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password-reset/sent/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="registration/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="registration/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
    path("profile/edit/", views.edit_profile, name="edit_profile"),
    path("users/<str:username>/followers/", views.followers_list, name="followers"),
    path("users/<str:username>/following/", views.following_list, name="following"),
    path("users/<str:username>/", views.profile, name="profile"),
    path("users/<str:username>/follow/", views.toggle_follow, name="toggle_follow"),
    path("posts/create/", views.create_post, name="create_post"),
    path("posts/<int:pk>/edit/", views.edit_post, name="edit_post"),
    path("posts/<int:pk>/delete/", views.delete_post, name="delete_post"),
    path("posts/<int:pk>/like/", views.toggle_like, name="toggle_like"),
    path("posts/<int:pk>/comment/", views.add_comment, name="add_comment"),
]
