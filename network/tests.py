import json
import re
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.core import serializers
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from botocore.exceptions import BotoCoreError
from PIL import Image

from .models import Comment, DataImportReceipt, Follow, Like, Post, Profile


def png_upload(filename):
    image_data = BytesIO()
    Image.new("RGB", (4, 4), color="purple").save(image_data, format="PNG")
    return SimpleUploadedFile(filename, image_data.getvalue(), content_type="image/png")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SocialNetworkTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="StrongPass123!"
        )
        self.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="StrongPass123!"
        )
        self.charlie = User.objects.create_user(
            username="charlie", email="charlie@example.com", password="StrongPass123!"
        )
        self.charlie.profile.bio = "Always looking for good ideas."
        self.charlie.profile.save()
        self.post = Post.objects.create(author=self.alice, content="A sunny day.")

    def test_signup_login_logout_and_profile_creation(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "diana",
                "email": "diana@example.com",
                "password1": "StrongPass123!xyz",
                "password2": "StrongPass123!xyz",
            },
        )
        self.assertRedirects(response, reverse("verification_pending"))
        user = User.objects.get(username="diana")
        self.assertTrue(hasattr(user, "profile"))
        self.assertFalse(user.profile.email_verified)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(len(mail.outbox), 1)
        verification_path = self._email_path(mail.outbox[-1].body)
        response = self.client.get(verification_path)
        self.assertRedirects(response, reverse("login"))
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.email_verified)
        self.assertTrue(self.client.login(username="diana", password="StrongPass123!xyz"))

        self.client.post(reverse("logout"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(
            self.client.login(username="alice", password="StrongPass123!")
        )

    @staticmethod
    def _email_path(body):
        match = re.search(r"https?://[^\s]+", body)
        if not match:
            raise AssertionError("Expected an absolute link in the email body.")
        return urlparse(match.group(0)).path

    def test_unverified_users_cannot_login_or_use_protected_features(self):
        self.bob.profile.email_verified = False
        self.bob.profile.save(update_fields=["email_verified"])
        response = self.client.post(
            reverse("login"),
            {"username": "bob", "password": "StrongPass123!"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "verify your email")
        self.client.force_login(self.bob)
        response = self.client.get(reverse("feed"))
        self.assertRedirects(response, reverse("verification_pending"))

    def test_verification_resend_is_generic_and_sends_only_for_unverified_user(self):
        self.bob.profile.email_verified = False
        self.bob.profile.save(update_fields=["email_verified"])
        response = self.client.post(
            reverse("resend_verification"), {"email": self.bob.email}
        )
        self.assertRedirects(response, reverse("verification_pending"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("verify-email/", mail.outbox[0].body)

        mail.outbox.clear()
        response = self.client.post(
            reverse("resend_verification"), {"email": "unknown@example.com"}
        )
        self.assertRedirects(response, reverse("verification_pending"))
        self.assertEqual(len(mail.outbox), 0)

        self.bob.profile.email_verified = True
        self.bob.profile.save(update_fields=["email_verified"])
        self.client.post(reverse("resend_verification"), {"email": self.bob.email})
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_and_expired_verification_links_are_rejected(self):
        self.bob.profile.email_verified = False
        self.bob.profile.save(update_fields=["email_verified"])
        self.client.post(
            reverse("resend_verification"), {"email": self.bob.email}
        )
        valid_path = self._email_path(mail.outbox[-1].body)
        invalid_path = valid_path.rsplit("/", 2)[0] + "/invalid-token/"
        response = self.client.get(invalid_path)
        self.assertRedirects(response, reverse("verification_pending"))
        self.bob.profile.refresh_from_db()
        self.assertFalse(self.bob.profile.email_verified)

        with override_settings(PASSWORD_RESET_TIMEOUT=-1):
            response = self.client.get(valid_path)
        self.assertRedirects(response, reverse("verification_pending"))
        self.bob.profile.refresh_from_db()
        self.assertFalse(self.bob.profile.email_verified)

    def test_verification_email_failure_keeps_account_unverified_and_retryable(self):
        with patch(
            "network.views.send_verification_email",
            side_effect=OSError("private SMTP diagnostic"),
        ):
            response = self.client.post(
                reverse("signup"),
                {
                    "username": "diana",
                    "email": "diana@example.com",
                    "password1": "StrongPass123!xyz",
                    "password2": "StrongPass123!xyz",
                },
            )
        self.assertRedirects(response, reverse("verification_pending"))
        user = User.objects.get(username="diana")
        self.assertFalse(user.profile.email_verified)
        response = self.client.post(
            reverse("resend_verification"), {"email": user.email}
        )
        self.assertRedirects(response, reverse("verification_pending"))
        self.assertEqual(len(mail.outbox), 1)

    def test_complete_password_reset_flow(self):
        response = self.client.post(
            reverse("password_reset"), {"email": self.alice.email}
        )
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        reset_path = self._email_path(mail.outbox[-1].body)

        response = self.client.get(reset_path, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a new password")
        response = self.client.post(
            response.request["PATH_INFO"],
            {
                "new_password1": "ChangedStrong123!xyz",
                "new_password2": "ChangedStrong123!xyz",
            },
        )
        self.assertRedirects(response, reverse("password_reset_complete"))
        self.assertTrue(
            self.client.login(
                username="alice", password="ChangedStrong123!xyz"
            )
        )

    def test_password_reset_response_does_not_disclose_unknown_emails(self):
        known = self.client.post(
            reverse("password_reset"), {"email": self.alice.email}
        )
        mail.outbox.clear()
        unknown = self.client.post(
            reverse("password_reset"), {"email": "unknown@example.com"}
        )
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.url, unknown.url)
        self.assertEqual(len(mail.outbox), 0)

    def test_registration_rejects_mismatched_passwords(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "diana",
                "email": "diana@example.com",
                "password1": "StrongPass123!xyz",
                "password2": "DifferentPass123!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="diana").exists())

    def test_profile_edit_updates_account_bio_and_picture(self):
        from io import BytesIO
        from tempfile import TemporaryDirectory

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from PIL import Image

        image_bytes = BytesIO()
        Image.new("RGB", (8, 8), color="purple").save(image_bytes, format="PNG")
        self.client.force_login(self.alice)
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                image = SimpleUploadedFile(
                    "profile.png", image_bytes.getvalue(), content_type="image/png"
                )
                response = self.client.post(
                    reverse("edit_profile"),
                    {
                        "username": "alice-new",
                        "email": "alice-new@example.com",
                        "bio": "Hello from Connect.",
                        "image": image,
                    },
                )
                self.assertRedirects(response, reverse("profile", args=["alice-new"]))
                self.alice.refresh_from_db()
                self.assertEqual(self.alice.email, "alice-new@example.com")
                self.assertEqual(self.alice.profile.bio, "Hello from Connect.")
                self.assertTrue(self.alice.profile.image.name.startswith("profiles/"))

    def test_profile_upload_storage_failure_is_reported_without_partial_account_update(self):
        self.client.force_login(self.alice)
        with patch(
            "network.views.ProfileForm.save",
            side_effect=BotoCoreError(error_message="private storage diagnostic"),
        ):
            response = self.client.post(
                reverse("edit_profile"),
                {
                    "username": "alice-new",
                    "email": self.alice.email,
                    "bio": "New bio",
                    "image": png_upload("storage-failure.png"),
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "store this image")
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.username, "alice")
        self.assertEqual(self.alice.profile.bio, "")

    def test_user_fixture_import_does_not_create_duplicate_profile(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from django.core import serializers
        from django.core.management import call_command

        fixture_data = serializers.serialize(
            "json", [self.charlie, self.charlie.profile]
        )
        self.charlie.delete()
        with TemporaryDirectory() as fixture_dir:
            fixture_path = Path(fixture_dir) / "connect-data.json"
            fixture_path.write_text(json.dumps(json.loads(fixture_data)), encoding="utf-8")
            call_command("loaddata", str(fixture_path), verbosity=0)

        restored_user = User.objects.get(username="charlie")
        self.assertEqual(restored_user.profile.bio, "Always looking for good ideas.")

    def test_invalid_and_oversized_profile_images_are_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.alice)
        invalid_image = SimpleUploadedFile(
            "not-an-image.png", b"not an image", content_type="image/png"
        )
        response = self.client.post(
            reverse("edit_profile"),
            {
                "username": "alice",
                "email": "alice@example.com",
                "bio": "",
                "image": invalid_image,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.alice.profile.image)

        oversized_image = SimpleUploadedFile(
            "large.png",
            b"x" * (6 * 1024 * 1024),
            content_type="image/png",
        )
        response = self.client.post(
            reverse("edit_profile"),
            {
                "username": "alice",
                "email": "alice@example.com",
                "bio": "",
                "image": oversized_image,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.alice.profile.image)

    def test_only_post_owner_can_edit_or_delete(self):
        self.client.force_login(self.bob)
        self.assertEqual(
            self.client.post(
                reverse("edit_post", args=[self.post.pk]), {"content": "Changed."}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(reverse("delete_post", args=[self.post.pk])).status_code,
            403,
        )
        self.post.refresh_from_db()
        self.assertEqual(self.post.content, "A sunny day.")

        self.client.force_login(self.alice)
        response = self.client.post(
            reverse("edit_post", args=[self.post.pk]), {"content": "A rainy day."}
        )
        self.assertRedirects(response, reverse("feed"))
        self.post.refresh_from_db()
        self.assertEqual(self.post.content, "A rainy day.")
        response = self.client.post(reverse("delete_post", args=[self.post.pk]))
        self.assertRedirects(response, reverse("feed"))
        self.assertFalse(Post.objects.filter(pk=self.post.pk).exists())

    def test_like_and_unlike_are_idempotent(self):
        self.client.force_login(self.bob)
        url = reverse("toggle_like", args=[self.post.pk])
        self.client.post(url)
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(Like.objects.filter(user=self.bob, post=self.post).count(), 1)
        self.client.post(url)
        self.assertEqual(Like.objects.filter(user=self.bob, post=self.post).count(), 0)

    def test_duplicate_comment_text_is_saved_as_separate_comments(self):
        self.client.force_login(self.bob)
        url = reverse("add_comment", args=[self.post.pk])
        self.client.post(url, {"content": "Same comment"})
        self.client.post(url, {"content": "Same comment"})
        self.assertEqual(
            Comment.objects.filter(
                post=self.post, author=self.bob, content="Same comment"
            ).count(),
            2,
        )

    def test_empty_comment_is_rejected(self):
        self.client.force_login(self.bob)
        response = self.client.post(
            reverse("add_comment", args=[self.post.pk]), {"content": "  "}
        )
        self.assertRedirects(response, reverse("feed"))
        self.assertFalse(Comment.objects.filter(post=self.post).exists())

    def test_comments_are_saved_with_authenticated_author(self):
        self.client.force_login(self.bob)
        response = self.client.post(
            reverse("add_comment", args=[self.post.pk]), {"content": "Lovely!"}
        )
        self.assertRedirects(response, reverse("feed"))
        comment = Comment.objects.get(post=self.post)
        self.assertEqual(comment.author, self.bob)
        self.assertEqual(comment.content, "Lovely!")

    def test_follow_counts_toggle_and_self_follow_is_blocked(self):
        self.client.force_login(self.bob)
        url = reverse("toggle_follow", args=["alice"])
        self.client.post(url)
        self.assertEqual(Follow.objects.filter(follower=self.bob, following=self.alice).count(), 1)
        self.assertEqual(self.alice.profile.followers_count, 1)
        self.assertEqual(self.bob.profile.following_count, 1)
        self.client.post(url)
        self.assertFalse(Follow.objects.filter(follower=self.bob, following=self.alice).exists())
        self.client.post(reverse("toggle_follow", args=["bob"]))
        self.assertFalse(Follow.objects.filter(follower=self.bob, following=self.bob).exists())

    def test_follow_statistics_are_live_links(self):
        Follow.objects.create(follower=self.bob, following=self.alice)
        Follow.objects.create(follower=self.alice, following=self.charlie)

        response = self.client.get(reverse("profile", args=["alice"]))

        self.assertContains(response, 'href="#user-posts"')
        self.assertContains(response, reverse("followers", args=["alice"]))
        self.assertContains(response, reverse("following", args=["alice"]))
        self.assertContains(response, "<strong>1</strong>", html=True)
        self.assertEqual(self.alice.followers.count(), 1)
        self.assertEqual(self.alice.following.count(), 1)

    def test_followers_page_shows_people_and_follow_back_action(self):
        Follow.objects.create(follower=self.bob, following=self.alice)
        Follow.objects.create(follower=self.charlie, following=self.alice)
        self.client.force_login(self.bob)

        url = reverse("followers", args=["alice"])
        response = self.client.get(url)

        self.assertContains(response, "@charlie")
        self.assertContains(response, "Always looking for good ideas.")
        self.assertContains(response, "Follow Back")
        self.assertContains(response, reverse("profile", args=["charlie"]))
        self.assertNotContains(response, "Unfollow")

        response = self.client.post(
            reverse("toggle_follow", args=["charlie"]),
            {"action": "follow", "next": url},
        )
        self.assertRedirects(response, url)
        self.assertTrue(
            Follow.objects.filter(follower=self.bob, following=self.charlie).exists()
        )
        self.client.post(
            reverse("toggle_follow", args=["charlie"]),
            {"action": "follow", "next": url},
        )
        self.assertEqual(
            Follow.objects.filter(follower=self.bob, following=self.charlie).count(), 1
        )
        response = self.client.get(url)
        self.assertContains(response, "Unfollow")
        self.assertNotContains(response, "Follow Back")
        self.client.post(
            reverse("toggle_follow", args=["charlie"]),
            {"action": "unfollow", "next": url},
        )
        self.assertContains(self.client.get(url), "Follow Back")

    def test_following_page_unfollow_updates_list_and_count(self):
        Follow.objects.create(follower=self.alice, following=self.bob)
        Follow.objects.create(follower=self.alice, following=self.charlie)
        self.client.force_login(self.alice)
        url = reverse("following", args=["alice"])

        response = self.client.get(url)
        self.assertContains(response, "@bob")
        self.assertContains(response, "@charlie")
        self.assertContains(response, "2 following")

        response = self.client.post(
            reverse("toggle_follow", args=["bob"]),
            {"action": "unfollow", "next": url},
        )
        self.assertRedirects(response, url)
        self.assertFalse(
            Follow.objects.filter(follower=self.alice, following=self.bob).exists()
        )
        response = self.client.get(url)
        self.assertNotContains(response, "@bob")
        self.assertContains(response, "@charlie")
        self.assertContains(response, "1 following")

    def test_follow_action_is_post_only_and_requires_authentication(self):
        url = reverse("toggle_follow", args=["alice"])
        self.client.force_login(self.bob)
        self.assertEqual(self.client.get(url).status_code, 405)

        self.client.logout()
        response = self.client.post(url, {"action": "follow"})
        self.assertRedirects(response, f"{reverse('login')}?next={url}")
        self.assertFalse(
            Follow.objects.filter(follower=self.bob, following=self.alice).exists()
        )

        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.bob)
        self.assertEqual(
            csrf_client.post(url, {"action": "follow"}).status_code, 403
        )

    def test_follow_list_pages_handle_empty_lists(self):
        response = self.client.get(reverse("followers", args=["alice"]))
        self.assertContains(response, "No followers just yet.")
        response = self.client.get(reverse("following", args=["alice"]))
        self.assertContains(response, "Not following anyone yet.")

    def test_feed_and_profile_show_posts(self):
        feed_response = self.client.get(reverse("feed"))
        profile_response = self.client.get(reverse("profile", args=["alice"]))
        self.assertContains(feed_response, "A sunny day.")
        self.assertContains(profile_response, "A sunny day.")
        self.assertContains(profile_response, "followers")

    def test_post_form_rejects_blank_content(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("create_post"), {"content": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Post.objects.filter(author=self.alice).count(), 1)

    def test_post_upload_storage_failure_is_reported(self):
        self.client.force_login(self.alice)
        with patch(
            "network.views.Post.save",
            side_effect=BotoCoreError(error_message="private storage diagnostic"),
        ):
            response = self.client.post(
                reverse("create_post"),
                {
                    "content": "Storage failure should remain clear.",
                    "image": png_upload("storage-failure.png"),
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "store this image")
        self.assertFalse(
            Post.objects.filter(
                author=self.alice, content="Storage failure should remain clear."
            ).exists()
        )

    def test_post_image_upload_validation_rejects_invalid_and_oversized_files(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.alice)
        for filename, payload in (
            ("not-an-image.png", b"not an image"),
            ("large.png", b"x" * (6 * 1024 * 1024)),
        ):
            with self.subTest(filename=filename):
                image = SimpleUploadedFile(
                    filename, payload, content_type="image/png"
                )
                response = self.client.post(
                    reverse("create_post"),
                    {"content": "This post must not be saved.", "image": image},
                )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(
                    Post.objects.filter(
                        author=self.alice, content="This post must not be saved."
                    ).exists()
                )

        gif_data = BytesIO()
        Image.new("RGB", (4, 4), color="purple").save(gif_data, format="GIF")
        response = self.client.post(
            reverse("create_post"),
            {
                "content": "Unsupported image format.",
                "image": SimpleUploadedFile(
                    "animated.gif", gif_data.getvalue(), content_type="image/gif"
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Post.objects.filter(author=self.alice, content="Unsupported image format.").exists()
        )

    def test_discover_search_filters_users_and_is_linked(self):
        response = self.client.get(reverse("discover"), {"q": "ali"})
        self.assertContains(response, "@alice")
        self.assertNotContains(response, "@bob")
        self.assertContains(response, reverse("profile", args=["alice"]))

    def test_sqlite_fixture_import_is_validated_atomic_and_idempotent(self):
        created_at = timezone.now()
        source_user = User(
            pk=900001,
            username="transfer-user",
            email="transfer@example.com",
            password=make_password("PreserveThisHash123!"),
            is_active=True,
        )
        source_profile = Profile(
            pk=900002,
            user=source_user,
            bio="Transferred intact.",
            email_verified=True,
        )
        source_post = Post(
            pk=900003,
            author=source_user,
            content="Transferred post.",
            created_at=created_at,
            updated_at=created_at,
        )
        source_comment = Comment(
            pk=900004,
            post=source_post,
            author=source_user,
            content="Transferred comment.",
            created_at=created_at,
        )
        source_like = Like(
            pk=900005, user=source_user, post=self.post, created_at=created_at
        )
        source_follow = Follow(
            pk=900006,
            follower=source_user,
            following=self.bob,
            created_at=created_at,
        )
        fixture = serializers.serialize(
            "json",
            [
                source_user,
                source_profile,
                source_post,
                source_comment,
                source_like,
                source_follow,
            ],
        )

        with tempfile.TemporaryDirectory() as directory:
            fixture_path = Path(directory) / "connect-data.json"
            fixture_path.write_text(fixture, encoding="utf-8")
            call_command(
                "migrate_connect_data",
                "--source",
                str(fixture_path),
                "--dry-run",
                verbosity=0,
            )
            self.assertFalse(User.objects.filter(pk=source_user.pk).exists())
            self.assertFalse(DataImportReceipt.objects.exists())

            call_command(
                "migrate_connect_data", "--source", str(fixture_path), verbosity=0
            )
            self.assertEqual(User.objects.get(pk=source_user.pk).password, source_user.password)
            self.assertEqual(
                User.objects.get(pk=source_user.pk).profile.bio, "Transferred intact."
            )
            self.assertTrue(
                Comment.objects.filter(pk=source_comment.pk, post_id=source_post.pk).exists()
            )
            self.assertTrue(
                Like.objects.filter(pk=source_like.pk, post=self.post).exists()
            )
            self.assertTrue(
                Follow.objects.filter(
                    pk=source_follow.pk, follower_id=source_user.pk, following=self.bob
                ).exists()
            )

            call_command(
                "migrate_connect_data", "--source", str(fixture_path), verbosity=0
            )
            self.assertEqual(User.objects.filter(pk=source_user.pk).count(), 1)
            self.assertEqual(Post.objects.filter(pk=source_post.pk).count(), 1)
            self.assertEqual(Comment.objects.filter(pk=source_comment.pk).count(), 1)
            self.assertEqual(Like.objects.filter(pk=source_like.pk).count(), 1)
            self.assertEqual(Follow.objects.filter(pk=source_follow.pk).count(), 1)
            self.assertEqual(DataImportReceipt.objects.count(), 1)

    def test_sqlite_fixture_conflicts_fail_without_overwriting_target_data(self):
        fixture = serializers.serialize("json", [self.alice])
        record = json.loads(fixture)
        record[0]["fields"]["email"] = "different@example.com"
        with tempfile.TemporaryDirectory() as directory:
            fixture_path = Path(directory) / "conflicting-data.json"
            fixture_path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command(
                    "migrate_connect_data",
                    "--source",
                    str(fixture_path),
                    verbosity=0,
                )
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.email, "alice@example.com")
        self.assertFalse(DataImportReceipt.objects.exists())

    def test_post_image_upload_is_saved(self):
        from io import BytesIO
        from tempfile import TemporaryDirectory

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from PIL import Image

        image_bytes = BytesIO()
        Image.new("RGB", (4, 4), color="purple").save(image_bytes, format="PNG")
        self.client.force_login(self.alice)
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                image = SimpleUploadedFile(
                    "moment.png", image_bytes.getvalue(), content_type="image/png"
                )
                response = self.client.post(
                    reverse("create_post"),
                    {"content": "A picture!", "image": image},
                )
                self.assertRedirects(response, reverse("feed"))
                self.assertTrue(
                    Post.objects.get(content="A picture!").image.name.startswith("posts/")
                )

    def test_image_only_post_is_not_accepted_without_content(self):
        from io import BytesIO
        from tempfile import TemporaryDirectory

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from PIL import Image

        image_bytes = BytesIO()
        Image.new("RGB", (4, 4), color="purple").save(image_bytes, format="PNG")
        image = SimpleUploadedFile(
            "moment.png", image_bytes.getvalue(), content_type="image/png"
        )
        self.client.force_login(self.alice)
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse("create_post"),
                    {"content": "   ", "image": image},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Post.objects.filter(author=self.alice).count(), 1)

    def test_unauthenticated_users_cannot_use_protected_actions(self):
        protected_posts = [
            ("create_post", []),
            ("edit_post", [self.post.pk]),
            ("delete_post", [self.post.pk]),
            ("toggle_like", [self.post.pk]),
            ("add_comment", [self.post.pk]),
            ("edit_profile", []),
            ("toggle_follow", ["alice"]),
        ]
        for route_name, args in protected_posts:
            with self.subTest(route=route_name):
                response = self.client.post(reverse(route_name, args=args), {})
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response.url)

    def test_all_primary_pages_render_successfully(self):
        paths = [
            reverse("feed"),
            reverse("discover"),
            reverse("login"),
            reverse("signup"),
            reverse("profile", args=["alice"]),
            reverse("followers", args=["alice"]),
            reverse("following", args=["alice"]),
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
