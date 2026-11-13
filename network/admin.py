from django.contrib import admin

from .models import Comment, Follow, Like, Post, Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "bio")
    search_fields = ("user__username",)


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("author", "created_at")
    search_fields = ("author__username", "content")
    list_filter = ("created_at",)


admin.site.register([Follow, Like, Comment])
