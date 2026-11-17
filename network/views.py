from smtplib import SMTPException

from botocore.exceptions import BotoCoreError
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.db.models import BooleanField, Count, Exists, OuterRef, Q, Value
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .email_verification import send_verification_email
from .forms import (
    CommentForm,
    EmailVerifiedAuthenticationForm,
    PostForm,
    ProfileForm,
    ResendVerificationForm,
    SignUpForm,
    UserProfileForm,
)
from .models import Comment, Follow, Like, Post, Profile


def _safe_return_url(request):
    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return "feed"


def feed(request):
    posts = Post.objects.select_related("author", "author__profile").annotate(
        likes_total=Count("likes", distinct=True),
        comments_total=Count("comments", distinct=True),
    )
    if request.user.is_authenticated:
        posts = posts.annotate(
            user_liked=Exists(
                Like.objects.filter(post=OuterRef("pk"), user=request.user)
            )
        )
    else:
        posts = posts.annotate(user_liked=Value(False, output_field=BooleanField()))
    posts = posts.prefetch_related("comments__author", "comments__author__profile")
    people_to_follow = User.objects.exclude(pk=request.user.pk) if request.user.is_authenticated else User.objects.all()
    if request.user.is_authenticated:
        people_to_follow = people_to_follow.exclude(
            followers__follower=request.user
        )
    people_to_follow = people_to_follow.select_related("profile").annotate(
        followers_total=Count("followers", distinct=True),
        posts_total=Count("posts", distinct=True),
    ).order_by("-followers_total", "username")[:4]
    return render(
        request,
        "network/feed.html",
        {
            "posts": posts,
            "post_form": PostForm(),
            "people_to_follow": people_to_follow,
        },
    )



def signup(request):
    if request.user.is_authenticated:
        return redirect("feed")

    form = SignUpForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = form.save()
        user.profile.email_verified = True
        user.profile.save(update_fields=["email_verified"])

        messages.success(request, "Your Connect account was created successfully. You can now log in.")
        return redirect("login")

    return render(request, "registration/signup.html", {"form": form})

def verification_pending(request):
    initial_email = request.session.get("pending_verification_email", "")
    return render(
        request,
        "registration/verification_pending.html",
        {"form": ResendVerificationForm(initial={"email": initial_email})},
    )


@require_POST
def resend_verification(request):
    form = ResendVerificationForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "registration/verification_pending.html",
            {"form": form},
            status=400,
        )

    email = form.cleaned_data["email"]
    user = User.objects.filter(email__iexact=email).first()
    send_failed = False
    if user:
        try:
            verified = user.profile.email_verified
        except Profile.DoesNotExist:
            verified = True
        if not verified:
            try:
                send_verification_email(user, request)
            except (OSError, SMTPException):
                send_failed = True

    if send_failed:
        messages.error(
            request, "We could not send the email right now. Please try again later."
        )
    else:
        messages.success(
            request,
            "If that address belongs to an unverified Connect account, a new link is on its way.",
        )
    request.session["pending_verification_email"] = email
    return redirect("verification_pending")


def verify_email(request, uidb64, token):
    user = None
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.select_related("profile").get(pk=user_id)
    except (
        TypeError,
        ValueError,
        OverflowError,
        UnicodeError,
        User.DoesNotExist,
        ValidationError,
    ):
        pass

    if user is None or not default_token_generator.check_token(user, token):
        messages.error(
            request,
            "This verification link is invalid or expired. Request a new link to continue.",
        )
        return redirect("verification_pending")

    if not user.profile.email_verified:
        user.profile.email_verified = True
        user.profile.save(update_fields=["email_verified"])
    request.session.pop("pending_verification_email", None)
    messages.success(request, "Your email is verified. You can now log in.")
    return redirect("login")


@login_required
def create_post(request):
    form = PostForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if form.is_valid():
            post = form.save(commit=False)
            post.author = request.user
            try:
                post.save()
            except (BotoCoreError, OSError):
                form.add_error("image", "We couldn't store this image. Please try again later.")
                messages.error(request, "Your post was not saved because its image could not be stored.")
                return render(
                    request,
                    "network/post_form.html",
                    {"form": form, "title": "Create a post"},
                )
            messages.success(request, "Your post is live.")
            return redirect("feed")
        messages.error(request, "Please correct the errors in your post.")
    return render(request, "network/post_form.html", {"form": form, "title": "Create a post"})


@login_required
def edit_post(request, pk):
    post = get_object_or_404(Post, pk=pk)
    if post.author_id != request.user.id:
        return HttpResponseForbidden("You can only edit your own posts.")
    form = PostForm(request.POST or None, request.FILES or None, instance=post)
    if request.method == "POST":
        if form.is_valid():
            try:
                form.save()
            except (BotoCoreError, OSError):
                form.add_error("image", "We couldn't store this image. Please try again later.")
                messages.error(request, "Your post was not updated because its image could not be stored.")
                return render(
                    request,
                    "network/post_form.html",
                    {"form": form, "title": "Edit your post"},
                )
            messages.success(request, "Your post was updated.")
            return redirect("feed")
        messages.error(request, "Please correct the errors in your post.")
    return render(
        request, "network/post_form.html", {"form": form, "title": "Edit your post"}
    )


@login_required
@require_POST
def delete_post(request, pk):
    post = get_object_or_404(Post, pk=pk)
    if post.author_id != request.user.id:
        return HttpResponseForbidden("You can only delete your own posts.")
    post.delete()
    messages.success(request, "Your post was deleted.")
    return redirect(_safe_return_url(request))


@login_required
@require_POST
def toggle_like(request, pk):
    post = get_object_or_404(Post, pk=pk)
    like, created = Like.objects.get_or_create(user=request.user, post=post)
    if not created:
        like.delete()
    return redirect(_safe_return_url(request))


@login_required
@require_POST
def add_comment(request, pk):
    post = get_object_or_404(Post, pk=pk)
    form = CommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.post = post
        comment.author = request.user
        comment.save()
        messages.success(request, "Your comment was added.")
    else:
        messages.error(request, "Please enter a comment of up to 500 characters.")
    return redirect(_safe_return_url(request))


def profile(request, username):
    profile_user = get_object_or_404(
        User.objects.select_related("profile"), username=username
    )
    posts = profile_user.posts.select_related("author", "author__profile").annotate(
        likes_total=Count("likes", distinct=True),
        comments_total=Count("comments", distinct=True),
    )
    if request.user.is_authenticated:
        posts = posts.annotate(
            user_liked=Exists(
                Like.objects.filter(post=OuterRef("pk"), user=request.user)
            )
        )
    else:
        posts = posts.annotate(user_liked=Value(False, output_field=BooleanField()))
    is_following = (
        request.user.is_authenticated
        and Follow.objects.filter(follower=request.user, following=profile_user).exists()
    )
    return render(
        request,
        "network/profile.html",
        {
            "profile_user": profile_user,
            "posts": posts,
            "posts_count": posts.count(),
            "followers_count": profile_user.followers.count(),
            "following_count": profile_user.following.count(),
            "is_following": is_following,
            "post_form": PostForm(),
        },
    )


def followers_list(request, username):
    profile_user = get_object_or_404(
        User.objects.select_related("profile"), username=username
    )
    relationships = profile_user.followers.select_related(
        "follower__profile"
    )
    followed_ids = set()
    if request.user.is_authenticated:
        followed_ids = set(
            request.user.following.values_list("following_id", flat=True)
        )
    return render(
        request,
        "network/follow_list.html",
        {
            "profile_user": profile_user,
            "relationships": relationships,
            "list_type": "followers",
            "followed_ids": followed_ids,
        },
    )


def following_list(request, username):
    profile_user = get_object_or_404(
        User.objects.select_related("profile"), username=username
    )
    relationships = profile_user.following.select_related(
        "following__profile"
    )
    followed_ids = set()
    if request.user.is_authenticated:
        followed_ids = set(
            request.user.following.values_list("following_id", flat=True)
        )
    return render(
        request,
        "network/follow_list.html",
        {
            "profile_user": profile_user,
            "relationships": relationships,
            "list_type": "following",
            "followed_ids": followed_ids,
        },
    )


@login_required
@require_POST
def toggle_follow(request, username):
    target = get_object_or_404(User, username=username)
    if target == request.user:
        messages.error(request, "You cannot follow yourself.")
    elif request.POST.get("action") == "follow":
        Follow.objects.get_or_create(follower=request.user, following=target)
    elif request.POST.get("action") == "unfollow":
        Follow.objects.filter(follower=request.user, following=target).delete()
    else:
        follow, created = Follow.objects.get_or_create(
            follower=request.user, following=target
        )
        if not created:
            follow.delete()
    if request.POST.get("next"):
        return redirect(_safe_return_url(request))
    return redirect("profile", username=target.username)


@login_required
def edit_profile(request):
    profile_form = ProfileForm(request.POST or None, request.FILES or None, instance=request.user.profile)
    user_form = UserProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST":
        if profile_form.is_valid() and user_form.is_valid():
            try:
                profile_form.save()
            except (BotoCoreError, OSError):
                profile_form.add_error(
                    "image", "We couldn't store this image. Please try again later."
                )
                messages.error(
                    request,
                    "Your profile was not updated because its image could not be stored.",
                )
                return render(
                    request,
                    "network/edit_profile.html",
                    {"profile_form": profile_form, "user_form": user_form},
                )
            user_form.save()
            messages.success(request, "Your profile was updated.")
            return redirect("profile", username=request.user.username)
        messages.error(request, "Please correct the errors in your profile.")
    return render(
        request,
        "network/edit_profile.html",
        {"profile_form": profile_form, "user_form": user_form},
    )


def discover(request):
    query = request.GET.get("q", "").strip()
    users = User.objects.select_related("profile").all()
    if request.user.is_authenticated:
        users = users.exclude(pk=request.user.pk)
    if query:
        users = users.filter(Q(username__icontains=query))
    users = users.annotate(
        followers_total=Count("followers", distinct=True),
        posts_total=Count("posts", distinct=True),
    ).order_by("-followers_total", "username")[:50]
    followed_ids = set()
    if request.user.is_authenticated:
        followed_ids = set(
            request.user.following.values_list("following_id", flat=True)
        )
    return render(
        request,
        "network/discover.html",
        {"users": users, "query": query, "followed_ids": followed_ids},
    )
