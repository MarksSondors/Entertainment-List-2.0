from rest_framework import serializers

from .models import Person


class PersonListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for explorer list cards."""

    media_type = serializers.SerializerMethodField()
    thumbnail = serializers.SerializerMethodField()
    year = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()
    media_count = serializers.IntegerField(read_only=True)
    user_rating = serializers.FloatField(read_only=True, allow_null=True)
    user_rating_count = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = Person
        fields = [
            "id",
            "media_type",
            "name",
            "thumbnail",
            "year",
            "date_of_birth",
            "date_of_death",
            "roles",
            "media_count",
            "user_rating",
            "user_rating_count",
        ]

    def get_media_type(self, obj):
        return "people"

    def get_thumbnail(self, obj):
        return obj.profile_picture or None

    def get_year(self, obj):
        return obj.date_of_birth.year if obj.date_of_birth else None

    def get_roles(self, obj):
        role_map = [
            ("is_actor", "Actor"),
            ("is_director", "Director"),
            ("is_screenwriter", "Screenwriter"),
            ("is_musician", "Musician"),
            ("is_book_author", "Author"),
            ("is_novelist", "Novelist"),
            ("is_comic_artist", "Comic Artist"),
            ("is_graphic_novelist", "Graphic Novelist"),
            ("is_original_music_composer", "Composer"),
            ("is_tv_creator", "TV Creator"),
        ]
        return [label for attr, label in role_map if getattr(obj, attr, False)]
