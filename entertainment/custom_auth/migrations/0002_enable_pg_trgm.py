from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [
        ('custom_auth', '0001_initial'),  # Replace with your latest migration
    ]

    operations = [
        TrigramExtension(),
    ]
