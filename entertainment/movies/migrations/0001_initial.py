# Generated by Django 5.1.7 on 2025-03-19 14:26

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('custom_auth', '0002_country_alter_genre_tmdb_id_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Movie',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=100)),
                ('original_title', models.CharField(max_length=100)),
                ('poster', models.URLField()),
                ('backdrops', models.JSONField()),
                ('release_date', models.DateField()),
                ('tmdb_id', models.IntegerField()),
                ('runtime', models.IntegerField()),
                ('plot', models.TextField()),
                ('rating', models.FloatField()),
                ('trailer', models.URLField(blank=True, null=True)),
                ('cast', models.ManyToManyField(related_name='cast', to='custom_auth.person')),
                ('countries', models.ManyToManyField(to='custom_auth.country')),
                ('directors', models.ManyToManyField(related_name='directors', to='custom_auth.person')),
                ('genres', models.ManyToManyField(to='custom_auth.genre')),
                ('producers', models.ManyToManyField(related_name='producers', to='custom_auth.person')),
                ('writers', models.ManyToManyField(related_name='writers', to='custom_auth.person')),
            ],
        ),
    ]
