from django.contrib import admin

# Register your models here.
from .models import CustomUser, Genre, Person

admin.site.register(CustomUser)
admin.site.register(Genre)
admin.site.register(Person)