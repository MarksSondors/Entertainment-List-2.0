import shutil
import tempfile
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from custom_auth.models import CustomUser, Genre
from custom_auth.services import genre_images

MEDIA_ROOT = tempfile.mkdtemp()


def _image_bytes(fmt='JPEG', size=(2400, 1600), frames=1):
    out = BytesIO()
    imgs = [Image.new('RGB', size, (i * 40 % 255, 80, 120)) for i in range(frames)]
    if frames > 1:
        imgs[0].save(out, fmt, save_all=True, append_images=imgs[1:], duration=80, loop=0)
    else:
        imgs[0].save(out, fmt, quality=95)
    return out.getvalue()


@override_settings(MEDIA_ROOT=MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class GenreThumbnailTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def _genre(self, name='Drama', data=None, filename='drama.jpg'):
        genre = Genre(name=name)
        genre.background_image.save(filename, ContentFile(data or _image_bytes()), save=False)
        genre.save()
        return genre

    def test_save_builds_small_cover_cropped_webp(self):
        genre = self._genre()
        name = genre_images.thumbnail_name(genre)
        self.assertTrue(default_storage.exists(name))
        with default_storage.open(name) as fh, Image.open(fh) as img:
            self.assertEqual(img.format, 'WEBP')
            self.assertEqual(img.size, genre_images.THUMB_SIZE)
        self.assertLess(default_storage.size(name), genre.background_image.size)

    def test_animated_source_stays_animated(self):
        genre = self._genre(data=_image_bytes('GIF', (600, 400), frames=4), filename='anim.gif')
        with default_storage.open(genre_images.thumbnail_name(genre)) as fh, Image.open(fh) as img:
            self.assertTrue(img.is_animated)
            self.assertEqual(img.n_frames, 4)

    def test_missing_source_returns_none(self):
        genre = self._genre()
        default_storage.delete(genre.background_image.name)
        self.assertIsNone(genre_images.thumbnail_url(genre))

    def test_no_image_returns_none(self):
        self.assertIsNone(genre_images.thumbnail_url(Genre.objects.create(name='Empty')))

    def test_replaced_source_gets_new_url_and_old_thumb_removed(self):
        genre = self._genre()
        old = genre_images.thumbnail_name(genre)
        genre.background_image.save('drama2.jpg', ContentFile(_image_bytes(size=(1800, 1200))), save=True)
        new = genre_images.thumbnail_name(genre)
        self.assertNotEqual(old, new)
        self.assertTrue(default_storage.exists(new))
        self.assertFalse(default_storage.exists(old))

    def test_discover_api_serves_thumbnail_url(self):
        genre = self._genre()
        user = CustomUser.objects.create_user('g', email='g@example.com', password='pw')
        self.client.force_login(user)
        data = self.client.get(reverse('discover_genres')).json()
        item = next(g for g in data['genres'] if g['id'] == genre.id)
        self.assertIn('/genre_backgrounds/thumbs/', item['background_image'])
        self.assertTrue(item['background_image'].endswith('.webp'))
