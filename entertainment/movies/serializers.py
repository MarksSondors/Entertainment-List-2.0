from rest_framework import serializers

from explorer.utils import build_thumbnail

from .models import Movie


class MovieSerializer(serializers.ModelSerializer):
    class Meta:
        model = Movie
        fields = '__all__'


class MovieListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for explorer list cards."""

    media_type = serializers.SerializerMethodField()
    thumbnail = serializers.SerializerMethodField()
    year = serializers.SerializerMethodField()
    genre_names = serializers.SerializerMethodField()
    user_rating = serializers.FloatField(read_only=True, allow_null=True)
    user_rating_count = serializers.IntegerField(read_only=True, allow_null=True)
    on_my_watchlist = serializers.BooleanField(read_only=True, default=False)
    reviewed_by_me = serializers.BooleanField(read_only=True, default=False)

    class Meta:
        model = Movie
        fields = [
            "id",
            "media_type",
            "title",
            "original_title",
            "tmdb_id",
            "thumbnail",
            "year",
            "release_date",
            "digital_release_date",
            "physical_release_date",
            "rating",
            "user_rating",
            "user_rating_count",
            "on_my_watchlist",
            "reviewed_by_me",
            "runtime",
            "status",
            "is_anime",
            "genre_names",
            "date_added",
        ]

    def get_media_type(self, obj):
        return "movies"

    def get_thumbnail(self, obj):
        return build_thumbnail(obj.poster)

    def get_year(self, obj):
        return obj.release_date.year if obj.release_date else None

    def get_genre_names(self, obj):
        return [g.name for g in obj.genres.all()]
