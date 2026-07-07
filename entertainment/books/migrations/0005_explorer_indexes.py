# Explorer performance indexes for books.
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('books', '0004_alter_book_date_added'),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS books_title_trgm_idx ON books_book USING GIN (title gin_trgm_ops);",
            reverse_sql="DROP INDEX IF EXISTS books_title_trgm_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS books_original_title_trgm_idx ON books_book USING GIN (original_title gin_trgm_ops);",
            reverse_sql="DROP INDEX IF EXISTS books_original_title_trgm_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS books_published_date_idx ON books_book (published_date);",
            reverse_sql="DROP INDEX IF EXISTS books_published_date_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS books_rating_idx ON books_book (rating);",
            reverse_sql="DROP INDEX IF EXISTS books_rating_idx;",
        ),
    ]
