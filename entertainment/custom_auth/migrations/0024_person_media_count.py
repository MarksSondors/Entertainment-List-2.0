# Explorer: denormalized Person.media_count column.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('custom_auth', '0023_keyword_add_hardcover_tag_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='person',
            name='media_count',
            field=models.PositiveIntegerField(default=0, db_index=True),
        ),
    ]
