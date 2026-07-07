# Explorer performance indexes for games.
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0006_alter_game_date_added'),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS games_title_trgm_idx ON games_game USING GIN (title gin_trgm_ops);",
            reverse_sql="DROP INDEX IF EXISTS games_title_trgm_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS games_original_title_trgm_idx ON games_game USING GIN (original_title gin_trgm_ops);",
            reverse_sql="DROP INDEX IF EXISTS games_original_title_trgm_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS games_release_date_idx ON games_game (release_date);",
            reverse_sql="DROP INDEX IF EXISTS games_release_date_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS games_rating_idx ON games_game (rating);",
            reverse_sql="DROP INDEX IF EXISTS games_rating_idx;",
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS games_metacritic_idx ON games_game (metacritic);",
            reverse_sql="DROP INDEX IF EXISTS games_metacritic_idx;",
        ),
    ]
