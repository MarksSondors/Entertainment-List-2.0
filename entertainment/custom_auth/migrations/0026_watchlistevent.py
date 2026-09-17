import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('custom_auth', '0025_person_explorer_indexes'),
    ]

    operations = [
        migrations.CreateModel(
            name='WatchlistEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('object_id', models.PositiveIntegerField(db_index=True)),
                ('action', models.CharField(choices=[('add', 'add'), ('remove', 'remove')], max_length=6)),
                ('date', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('content_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='watchlist_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [
                    models.Index(fields=['user', 'content_type', 'date'], name='custom_auth_user_id_ct_date_idx'),
                ],
            },
        ),
        # Backfill: existing watchlist rows become 'add' events dated from
        # their date_added, so the chart has real history for adds.
        migrations.RunSQL(
            sql="""
                INSERT INTO custom_auth_watchlistevent (user_id, content_type_id, object_id, action, date)
                SELECT w.user_id, w.content_type_id, w.object_id, 'add', w.date_added
                FROM custom_auth_watchlist w
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
