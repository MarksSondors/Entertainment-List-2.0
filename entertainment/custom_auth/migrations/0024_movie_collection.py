# Generated by Django 5.1.7 on 2025-04-05 15:22

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('custom_auth', '0023_remove_person_is_band_member_and_more'),
        ('movies', '0010_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='movie',
            name='collection',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='movies', to='movies.collection'),
        ),
    ]
