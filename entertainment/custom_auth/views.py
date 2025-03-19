from django.shortcuts import render

# Create your views here.

def login_page(request):
    if request.method == 'POST':
        # Handle the form submission
        pass
    return render(request, 'login_page.html')