from django.contrib import admin

# Register your models here.
from .models import CustomUser, Genre, Person, Keyword, Country

admin.site.register(CustomUser)
admin.site.register(Genre)
admin.site.register(Person)
admin.site.register(Keyword)
admin.site.register(Country)