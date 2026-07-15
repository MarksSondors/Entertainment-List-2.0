from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('movies', '0009_alter_movie_original_title'),
    ]

    operations = [
        migrations.AddField(
            model_name='movie',
            name='digital_release_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='movie',
            name='physical_release_date',
            field=models.DateField(blank=True, null=True),
        ),
    ]