# Generated migration for Person model changes:
# - openlibrary_id field added
# - hardcover_id changed from UUIDField to IntegerField

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('custom_auth', '0021_mediaperson_custom_auth_content_6064fc_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='person',
            name='openlibrary_id',
            field=models.CharField(blank=True, db_index=True, max_length=50, null=True),
        ),
        # UUID -> Integer: cannot cast directly in PostgreSQL, drop and re-add
        migrations.RemoveField(
            model_name='person',
            name='hardcover_id',
        ),
        migrations.AddField(
            model_name='person',
            name='hardcover_id',
            field=models.IntegerField(blank=True, null=True, unique=True),
        ),
    ]
