# Generated by Django 5.1.7 on 2025-03-28 19:17

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('custom_auth', '0009_movie_delete_review'),
        ('movies', '0008_alter_movie_cast_alter_movie_composer_and_more'),
    ]

    operations = [
        migrations.DeleteModel(
            name='Movie',
        ),
    ]
