# Explorer performance indexes for TV shows.
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('tvshows', '0014_alter_tvshow_date_added'),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS tvshows_title_trgm_idx ON tvshows_tvshow USING GIN (title gin_trgm_ops);",
            reverse_sql="DROP INDEX IF EXISTS tvshows_title_trgm_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS tvshows_original_title_trgm_idx ON tvshows_tvshow USING GIN (original_title gin_trgm_ops);",
            reverse_sql="DROP INDEX IF EXISTS tvshows_original_title_trgm_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS tvshows_first_air_date_idx ON tvshows_tvshow (first_air_date);",
            reverse_sql="DROP INDEX IF EXISTS tvshows_first_air_date_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS tvshows_rating_idx ON tvshows_tvshow (rating);",
            reverse_sql="DROP INDEX IF EXISTS tvshows_rating_idx;",
        ),
    ]
