# Generated by Django 5.1.7 on 2025-04-05 15:30

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('movies', '0010_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='collection',
            name='tmdb_id',
            field=models.IntegerField(blank=True, null=True, unique=True),
        ),
    ]
