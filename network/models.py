from django.conf import settings
from django.core.validators import MinLengthValidator
from django.db import models
from django.urls import reverse


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    bio = models.CharField(max_length=280, blank=True)
    image = models.ImageField(upload_to="profiles/", blank=True)
    email_verified = models.BooleanField(default=True)

    def __str__(self):
        return f"@{self.user.username}"

    @property
    def followers_count(self):
        return self.user.followers.count()

    @property
    def following_count(self):
        return self.user.following.count()

    def get_absolute_url(self):
        return reverse("profile", kwargs={"username": self.user.username})


class DataImportReceipt(models.Model):
    source_sha256 = models.CharField(max_length=64, primary_key=True)
    imported_counts = models.JSONField(default=dict)
    imported_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Connect data import {self.source_sha256[:12]}"


class Post(models.Model):
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posts"
    )
    content = models.TextField(
        max_length=2000, validators=[MinLengthValidator(1)]
    )
    image = models.ImageField(upload_to="posts/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Post by @{self.author.username} ({self.created_at:%Y-%m-%d})"

    @property
    def likes_count(self):
        return self.likes.count()


class Follow(models.Model):
    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="following"
    )
    following = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="followers"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "following"], name="unique_user_follow"
            ),
            models.CheckConstraint(
                condition=~models.Q(follower=models.F("following")),
                name="prevent_self_follow",
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"@{self.follower.username} follows @{self.following.username}"


class Like(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="likes"
    )
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="likes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "post"], name="unique_user_like")
        ]


class Comment(models.Model):
    post = models.ForeignKey(
        Post, on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comments"
    )
    content = models.CharField(max_length=500, validators=[MinLengthValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment by @{self.author.username} on post {self.post_id}"
