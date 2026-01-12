from django import forms

class ImdbImportForm(forms.Form):
    ratings_file = forms.FileField(
        label="Ratings.csv",
        required=False,
        help_text="Upload your ratings.csv file from IMDb export.",
        widget=forms.FileInput(attrs={'accept': '.csv'})
    )
    watchlist_file = forms.FileField(
        label="Watchlist.csv",
        required=False,
        help_text="Upload your watchlist.csv file from IMDb export.",
        widget=forms.FileInput(attrs={'accept': '.csv'})
    )

    def clean(self):
        cleaned_data = super().clean()
        ratings_file = cleaned_data.get('ratings_file')
        watchlist_file = cleaned_data.get('watchlist_file')

        if not ratings_file and not watchlist_file:
            raise forms.ValidationError("Please upload at least one file (Ratings or Watchlist).")
        return cleaned_data
