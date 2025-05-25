from django.db import migrations
from django.contrib.postgres.operations import TrigramExtension

class Migration(migrations.Migration):
    dependencies = [
        ('movies', '0001_initial'),  # Replace with your latest migration
    ]

    operations = [
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS movies_title_trgm_idx ON movies_movie USING GIN (title gin_trgm_ops);",
            reverse_sql="DROP INDEX IF EXISTS movies_title_trgm_idx;"
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS movies_original_title_trgm_idx ON movies_movie USING GIN (original_title gin_trgm_ops);",
            reverse_sql="DROP INDEX IF EXISTS movies_original_title_trgm_idx;"
        ),
    ]
