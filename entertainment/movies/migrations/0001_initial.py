# Generated by Django 5.1.7 on 2025-04-09 07:07

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('custom_auth', '0002_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Collection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, unique=True)),
                ('description', models.TextField(blank=True, null=True)),
                ('poster', models.URLField(blank=True, null=True)),
                ('backdrop', models.URLField(blank=True, null=True)),
                ('tmdb_id', models.IntegerField(blank=True, null=True, unique=True)),
            ],
        ),
        migrations.CreateModel(
            name='Movie',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=255)),
                ('original_title', models.CharField(max_length=100)),
                ('description', models.TextField(blank=True, null=True)),
                ('poster', models.URLField(blank=True, null=True)),
                ('backdrop', models.URLField(blank=True, null=True)),
                ('release_date', models.DateField()),
                ('tmdb_id', models.IntegerField(unique=True)),
                ('imdb_id', models.CharField(blank=True, max_length=20, null=True)),
                ('runtime', models.IntegerField()),
                ('rating', models.FloatField()),
                ('trailer', models.URLField(blank=True, null=True)),
                ('is_anime', models.BooleanField(default=False)),
                ('status', models.CharField(blank=True, max_length=50, null=True)),
                ('date_added', models.DateTimeField(auto_now_add=True)),
                ('date_updated', models.DateTimeField(auto_now=True)),
                ('added_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='added_movies', to=settings.AUTH_USER_MODEL)),
                ('collection', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='movies', to='movies.collection')),
                ('countries', models.ManyToManyField(to='custom_auth.country')),
                ('genres', models.ManyToManyField(to='custom_auth.genre')),
                ('keywords', models.ManyToManyField(to='custom_auth.keyword')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
