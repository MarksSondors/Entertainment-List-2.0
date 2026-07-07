# Explorer performance indexes for Person:
# * Trigram GIN on `name` — powers the `?q=` fuzzy search.
# * Partial btree indexes per role flag on `name` — makes queries like
#   "actors ordered by name" a tiny index scan instead of a full seq scan
#   over Person rows.
# * date_of_birth btree — sort by born.
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('custom_auth', '0024_person_media_count'),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS person_name_trgm_idx ON custom_auth_person USING GIN (name gin_trgm_ops);",
            reverse_sql="DROP INDEX IF EXISTS person_name_trgm_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS person_date_of_birth_idx ON custom_auth_person (date_of_birth);",
            reverse_sql="DROP INDEX IF EXISTS person_date_of_birth_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS person_actor_name_idx ON custom_auth_person (name) WHERE is_actor;",
            reverse_sql="DROP INDEX IF EXISTS person_actor_name_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS person_director_name_idx ON custom_auth_person (name) WHERE is_director;",
            reverse_sql="DROP INDEX IF EXISTS person_director_name_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS person_musician_name_idx ON custom_auth_person (name) WHERE is_musician;",
            reverse_sql="DROP INDEX IF EXISTS person_musician_name_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS person_book_author_name_idx ON custom_auth_person (name) WHERE is_book_author;",
            reverse_sql="DROP INDEX IF EXISTS person_book_author_name_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS person_screenwriter_name_idx ON custom_auth_person (name) WHERE is_screenwriter;",
            reverse_sql="DROP INDEX IF EXISTS person_screenwriter_name_idx;",
        ),
    ]
