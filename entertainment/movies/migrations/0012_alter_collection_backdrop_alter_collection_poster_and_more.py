# Generated by Django 5.1.7 on 2025-04-06 11:49

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('custom_auth', '0025_delete_movie'),
        ('movies', '0011_collection_tmdb_id'),
    ]

    operations = [
        migrations.AlterField(
            model_name='collection',
            name='backdrop',
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='collection',
            name='poster',
            field=models.URLField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='Movie',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=255)),
                ('original_title', models.CharField(max_length=100)),
                ('description', models.TextField(blank=True, null=True)),
                ('poster', models.ImageField(blank=True, null=True, upload_to='movie_posters/')),
                ('backdrop', models.ImageField(blank=True, null=True, upload_to='movie_backdrops/')),
                ('release_date', models.DateField()),
                ('tmdb_id', models.IntegerField(unique=True)),
                ('imdb_id', models.CharField(blank=True, max_length=20, null=True)),
                ('runtime', models.IntegerField()),
                ('rating', models.FloatField()),
                ('trailer', models.URLField(blank=True, null=True)),
                ('is_anime', models.BooleanField(default=False)),
                ('status', models.CharField(blank=True, max_length=50, null=True)),
                ('collection', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='movies', to='movies.collection')),
                ('countries', models.ManyToManyField(to='custom_auth.country')),
                ('genres', models.ManyToManyField(to='custom_auth.genre')),
                ('keywords', models.ManyToManyField(to='custom_auth.keyword')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
