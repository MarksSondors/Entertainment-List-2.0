from django.contrib import admin
from .models import Game, GameGenre, Platform, GameDeveloper, GamePublisher, GameCollection

# Register your models here.

@admin.register(GameDeveloper)
class GameDeveloperAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'rawg_id')
    search_fields = ('name', 'slug')

@admin.register(GamePublisher)
class GamePublisherAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'rawg_id')
    search_fields = ('name', 'slug')

@admin.register(GameCollection)
class GameCollectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'rawg_id')
    search_fields = ('name', 'slug')

@admin.register(GameGenre)
class GameGenreAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'rawg_id')
    search_fields = ('name', 'slug')

@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'rawg_id')
    search_fields = ('name', 'slug')

@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ('title', 'release_date', 'rating', 'date_added')
    search_fields = ('title', 'description')
    list_filter = ('release_date', 'date_added')
    ordering = ('-date_added',)
    readonly_fields = ('date_added', 'date_updated')
    date_hierarchy = 'release_date'
    autocomplete_fields = ['genres', 'platforms', 'keywords', 'developers', 'publishers', 'game_collection']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('title', 'release_date', 'description')
        }),
        ('Visual Assets', {
            'fields': ('poster', 'backdrop'),
            'classes': ('collapse',)
        }),
        ('Ratings', {
            'fields': ('rating', 'metacritic')
        }),
        ('External IDs', {
            'fields': ('rawg_id', 'rawg_slug'),
            'classes': ('collapse',)
        }),
        ('Relationships', {
            'fields': ('genres', 'platforms', 'keywords', 'developers', 'publishers', 'game_collection')
        }),
        ('Metadata', {
            'fields': ('added_by', 'date_added', 'date_updated'),
            'classes': ('collapse',)
        }),
    )
