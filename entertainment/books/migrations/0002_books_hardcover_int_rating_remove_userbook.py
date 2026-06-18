# Migration for books app:
# - All hardcover_id fields changed from UUIDField to IntegerField
# - Book.rating (FloatField) added
# - UserBook model removed (replaced by generic Watchlist)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('books', '0001_initial'),
        ('custom_auth', '0022_person_openlibrary_id_alter_hardcover_id'),
    ]

    operations = [
        migrations.DeleteModel(
            name='UserBook',
        ),
        # UUID -> Integer: cannot cast directly in PostgreSQL, drop and re-add
        migrations.RemoveField(model_name='bookcollection', name='hardcover_id'),
        migrations.AddField(
            model_name='bookcollection',
            name='hardcover_id',
            field=models.IntegerField(blank=True, null=True, unique=True),
        ),
        migrations.RemoveField(model_name='bookseries', name='hardcover_id'),
        migrations.AddField(
            model_name='bookseries',
            name='hardcover_id',
            field=models.IntegerField(blank=True, null=True, unique=True),
        ),
        migrations.RemoveField(model_name='publisher', name='hardcover_id'),
        migrations.AddField(
            model_name='publisher',
            name='hardcover_id',
            field=models.IntegerField(blank=True, null=True, unique=True),
        ),
        migrations.RemoveField(model_name='book', name='hardcover_id'),
        migrations.AddField(
            model_name='book',
            name='hardcover_id',
            field=models.IntegerField(unique=True),
        ),
        migrations.AddField(
            model_name='book',
            name='rating',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
