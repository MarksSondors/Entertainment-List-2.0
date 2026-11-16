"""Add a GIN full-text search index for the people search path.

`custom_auth.services.search.text_search` builds a `SearchVector` on the
searched fields and matches it with the `@@` operator, and Django renders that
vector as

    to_tsvector('english', COALESCE("name", ''))

Django always wraps the searched columns in `COALESCE(..., '')` (see
`django.contrib.postgres.search.SearchVector.as_sql`), so the index expression
below has to match that exactly or the planner ignores it.

Without the index, a people search seq-scanned the whole table: 73,008 rows,
`Rows Removed by Filter: 73008`, ~160 ms per query and ~290 ms end to end.
Measured after this migration on a copy of the production table (same row
count): 'nolan' 224 ms -> 9 ms, 'travolta' 215 ms -> 6 ms.

It has to be a `GinIndex` - a plain `models.Index` builds a btree, which the
planner ignores for `@@` tsquery matching.

The index is deliberately NOT declared in the model's `Meta.indexes`: Django
cannot express this expression index as a model index without also trying to
recreate it with a plain (table-locking) `CREATE INDEX`, so it is created here
and kept out of the model state with `SeparateDatabaseAndState`.

Built CONCURRENTLY so applying it does not block writes on the table.
"""
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models

EXPRESSION = "to_tsvector('english', COALESCE(\"name\", ''))"


class Migration(migrations.Migration):
    # AddIndexConcurrently cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("custom_auth", "0028_slow_request"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                AddIndexConcurrently(
                    model_name="person",
                    index=GinIndex(
                        models.expressions.RawSQL(EXPRESSION, []),
                        name="custom_auth_person_name_fts_gin",
                    ),
                ),
            ],
            # Keep the index out of the model state: it is not declared in
            # Person.Meta.indexes, so leaving it in the state would make
            # `makemigrations` want to remove it again.
            state_operations=[],
        ),
    ]
