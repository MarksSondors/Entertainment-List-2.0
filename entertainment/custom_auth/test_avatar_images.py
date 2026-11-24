import re
import shutil
import tempfile
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from custom_auth.models import CustomUser
from custom_auth.services import avatar_images

MEDIA_ROOT = tempfile.mkdtemp()
LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def _image_bytes(fmt='PNG', size=(1080, 1080)):
    out = BytesIO()
    Image.new('RGB', size, (200, 80, 120)).save(out, fmt)
    return out.getvalue()


def _user(username='av', data=None, filename='face.png'):
    user = CustomUser.objects.create_user(username, email=f'{username}@example.com', password='x')
    if data is not False:
        user.profile_picture.save(filename, ContentFile(data or _image_bytes()), save=True)
    return user


@override_settings(MEDIA_ROOT=MEDIA_ROOT, SECURE_SSL_REDIRECT=False, CACHES=LOCMEM)
class AvatarThumbnailTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def test_save_builds_small_webp_and_keeps_original(self):
        user = _user()
        name = avatar_images.thumbnail_name(user)
        self.assertTrue(default_storage.exists(name))
        with default_storage.open(name) as fh, Image.open(fh) as img:
            self.assertEqual(img.format, 'WEBP')
            self.assertEqual(max(img.size), avatar_images.THUMB_MAX)
        self.assertLess(default_storage.size(name), user.profile_picture.size)
        with user.profile_picture.open('rb') as fh, Image.open(fh) as img:
            self.assertEqual(img.size, (1080, 1080))

    def test_aspect_ratio_kept_and_small_images_not_upscaled(self):
        wide = _user('wide', _image_bytes(size=(1200, 600)))
        with default_storage.open(avatar_images.thumbnail_name(wide)) as fh, Image.open(fh) as img:
            self.assertEqual(img.size, (256, 128))
        tiny = _user('tiny', _image_bytes(size=(64, 64)))
        with default_storage.open(avatar_images.thumbnail_name(tiny)) as fh, Image.open(fh) as img:
            self.assertEqual(img.size, (64, 64))

    def test_get_profile_picture_serves_thumbnail(self):
        user = _user()
        url = user.get_profile_picture()
        self.assertIn('/profile_pictures/thumbs/', url)
        self.assertTrue(url.endswith('.webp'))

    def test_missing_source_falls_back_to_original_url(self):
        user = _user()
        default_storage.delete(avatar_images.thumbnail_name(user))
        default_storage.delete(user.profile_picture.name)
        self.assertEqual(user.get_profile_picture(), user.profile_picture.url)

    def test_no_picture(self):
        user = _user(data=False)
        self.assertIsNone(avatar_images.thumbnail_url(user))
        self.assertEqual(user.get_profile_picture(), 'https://www.gravatar.com/avatar/')

    def test_replaced_picture_gets_new_url_and_old_thumb_removed(self):
        user = _user()
        old = avatar_images.thumbnail_name(user)
        user.profile_picture.save('face2.png', ContentFile(_image_bytes(size=(900, 900))), save=True)
        new = avatar_images.thumbnail_name(user)
        self.assertNotEqual(old, new)
        self.assertTrue(default_storage.exists(new))
        self.assertFalse(default_storage.exists(old))

    def test_corrupt_upload_does_not_break(self):
        user = _user(data=b'not an image', filename='bad.png')
        self.assertIsNone(avatar_images.thumbnail_url(user))
        self.assertEqual(user.get_profile_picture(), user.profile_picture.url)


@override_settings(MEDIA_ROOT=MEDIA_ROOT, SECURE_SSL_REDIRECT=False, CACHES=LOCMEM)
class ProfilePageImageTests(TestCase):
    def test_profile_page_has_no_hash_or_dead_placeholder_images(self):
        user = _user('prof')
        self.client.force_login(user)
        html = self.client.get(reverse('profile_page')).content.decode()
        self.assertNotRegex(html, r'src="#"')
        self.assertNotIn('via.placeholder.com', html)
        avatar = re.search(r'<img src="([^"]+)" alt="Profile Picture" class="profile-picture"', html)
        self.assertIsNotNone(avatar)
        self.assertIn('/profile_pictures/thumbs/', avatar.group(1))
