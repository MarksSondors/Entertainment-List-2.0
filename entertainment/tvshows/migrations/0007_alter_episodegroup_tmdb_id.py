# Generated by Django 5.2 on 2025-04-22 09:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tvshows', '0006_alter_episodegroup_tmdb_id_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='episodegroup',
            name='tmdb_id',
            field=models.CharField(blank=True, max_length=30, null=True, unique=True),
        ),
    ]
