from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout  # Import the logout function
# Create your views here.

def login_page(request):
    if request.user.is_authenticated:
        return redirect('home_page')
    return render(request, 'login_page.html')

def login_request(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('home_page')
        else:
            return redirect('login_page')
    return render(request, 'login_page.html')

def logout_request(request):
    if request.user.is_authenticated:
        logout(request)  # Use the logout function
    return redirect('login_page')

def home_page(request):
    if request.user.is_authenticated:
        context = {
            'user': request.user
        }
        return render(request, 'home_page.html', context)
    else:
        return redirect('login_page')