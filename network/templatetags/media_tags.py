from django import template

register = template.Library()


@register.filter
def media_exists(image):
    if not image:
        return False
    try:
        return image.storage.exists(image.name)
    except OSError:
        return False
