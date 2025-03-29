# Generated by Django 5.1.7 on 2025-03-29 13:06

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('custom_auth', '0009_movie_delete_review'),
    ]

    operations = [
        migrations.RenameField(
            model_name='person',
            old_name='is_composer',
            new_name='is_original_music_composer',
        ),
        migrations.RemoveField(
            model_name='movie',
            name='cast',
        ),
        migrations.RemoveField(
            model_name='movie',
            name='composer',
        ),
        migrations.RemoveField(
            model_name='movie',
            name='directors',
        ),
        migrations.RemoveField(
            model_name='movie',
            name='producers',
        ),
        migrations.RemoveField(
            model_name='movie',
            name='writers',
        ),
        migrations.RemoveField(
            model_name='person',
            name='is_actor',
        ),
        migrations.RemoveField(
            model_name='person',
            name='is_producer',
        ),
        migrations.AddField(
            model_name='person',
            name='is_comic_artist',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='person',
            name='is_graphic_novelist',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='person',
            name='is_novelist',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='person',
            name='is_original_story',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='person',
            name='is_screenwriter',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='person',
            name='is_story',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='person',
            name='is_tv_creator',
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name='MediaPerson',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('object_id', models.PositiveIntegerField()),
                ('role', models.CharField(max_length=50)),
                ('character_name', models.CharField(blank=True, max_length=100, null=True)),
                ('order', models.PositiveIntegerField(default=0)),
                ('content_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype')),
                ('person', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='custom_auth.person')),
            ],
            options={
                'ordering': ['order'],
            },
        ),
    ]
