from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile

from .models import Comment, Post, Profile

User = get_user_model()
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def validate_uploaded_image(image, max_size):
    if not isinstance(image, UploadedFile):
        return image
    if image.size > max_size:
        raise ValidationError(f"Choose an image smaller than {max_size // (1024 * 1024)} MB.")
    image_format = getattr(getattr(image, "image", None), "format", None)
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError("Upload a JPEG, PNG, or WebP image.")
    return image


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "An account with this email already exists. Try logging in or resetting your password."
            )
        return email


class EmailVerifiedAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        try:
            is_verified = user.profile.email_verified
        except Profile.DoesNotExist:
            is_verified = True
        if not is_verified:
            raise forms.ValidationError(
                "Please verify your email before logging in. You can request another verification link below.",
                code="email_unverified",
            )


class ResendVerificationForm(forms.Form):
    email = forms.EmailField()


class PostForm(forms.ModelForm):
    MAX_IMAGE_SIZE = 5 * 1024 * 1024

    class Meta:
        model = Post
        fields = ("content", "image")
        widgets = {
            "content": forms.Textarea(
                attrs={"rows": 3, "placeholder": "What would you like to share?"}
            ),
            "image": forms.ClearableFileInput(attrs={"accept": "image/*"}),
        }

    def clean_content(self):
        content = self.cleaned_data["content"].strip()
        if not content:
            raise forms.ValidationError("A post cannot be empty.")
        return content

    def clean_image(self):
        return validate_uploaded_image(
            self.cleaned_data.get("image"), self.MAX_IMAGE_SIZE
        )


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ("content",)
        widgets = {"content": forms.TextInput(attrs={"placeholder": "Write a comment..."})}

    def clean_content(self):
        content = self.cleaned_data["content"].strip()
        if not content:
            raise forms.ValidationError("A comment cannot be empty.")
        return content


class ProfileForm(forms.ModelForm):
    MAX_IMAGE_SIZE = 5 * 1024 * 1024

    class Meta:
        model = Profile
        fields = ("bio", "image")
        widgets = {"bio": forms.Textarea(attrs={"rows": 4})}

    def clean_image(self):
        return validate_uploaded_image(
            self.cleaned_data.get("image"), self.MAX_IMAGE_SIZE
        )


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("username", "email")
