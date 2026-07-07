# Explorer performance indexes for movies (adds the ones missing from
# 0002_add_trigram_indexes: rating idx for sort-by-rating).
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('movies', '0007_alter_movie_date_added'),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS movies_rating_idx ON movies_movie (rating);",
            reverse_sql="DROP INDEX IF EXISTS movies_rating_idx;",
        ),
    ]
