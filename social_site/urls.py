import re

from django.contrib import admin
from django.conf import settings
from django.urls import include, path, re_path
from django.views.static import serve


def serve_media(request, path):
    return serve(request, path, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("network.urls")),
    re_path(
        rf"^{re.escape(settings.MEDIA_URL.lstrip('/'))}(?P<path>.*)$",
        serve_media,
    ),
]
