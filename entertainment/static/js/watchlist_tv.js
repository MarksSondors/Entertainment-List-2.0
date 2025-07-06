/**
 * Add the current TV show to the user's watchlist
 */
function addToWatchlist() {
    // Get TV show ID from the button's data attribute
    const button = document.querySelector('button[onclick="addToWatchlist()"]');
    const tvShowId = button.getAttribute('data-tvshow-id');
    
    if (!tvShowId) {
        console.error('TV show ID not found');
        alert('Error: Could not identify the TV show. Please refresh and try again.');
        return;
    }
    
    fetch('/tvshows/watchlist/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken'),
        },
        body: JSON.stringify({
            id: tvShowId
        })
    })
    .then(response => {
        if (!response.ok) {
            throw new Error('Network response was not ok');
        }
        return response.json();
    })
    .then(data => {
        // Update UI to show the TV show is now in watchlist
        const addButton = document.querySelector('button[onclick="addToWatchlist()"]');
        addButton.outerHTML = `<button class="retro-btn" onclick="removeFromWatchlist()" data-tvshow-id="${tvShowId}">Remove from Watchlist</button>`;
    })
    .catch(error => {
        console.error('Error adding to watchlist:', error);
        alert('Failed to add TV show to watchlist. Please try again.');
    });
}

/**
 * Remove the current TV show from the user's watchlist
 */
function removeFromWatchlist() {
    // Get TV show ID from the button's data attribute
    const button = document.querySelector('button[onclick="removeFromWatchlist()"]');
    const tvShowId = button.getAttribute('data-tvshow-id');
    
    if (!tvShowId) {
        console.error('TV show ID not found');
        alert('Error: Could not identify the TV show. Please refresh and try again.');
        return;
    }
    
    fetch('/tvshows/watchlist/', {
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken'),
        },
        body: JSON.stringify({
            id: tvShowId
        })
    })
    .then(response => {
        if (!response.ok) {
            throw new Error('Network response was not ok');
        }
        // Handle 204 No Content responses
        if (response.status === 204) {
            return {}; // Return empty object instead of parsing JSON
        }
        return response.json();
    })
    .then(_ => {
        // Update UI to show the TV show is not in watchlist
        const removeButton = document.querySelector('button[onclick="removeFromWatchlist()"]');
        removeButton.outerHTML = `<button class="retro-btn" onclick="addToWatchlist()" data-tvshow-id="${tvShowId}">Add to Watchlist</button>`;
    })
    .catch(error => {
        console.error('Error removing from watchlist:', error);
        alert('Failed to remove TV show from watchlist. Please try again.');
    });
}

/**
 * Get CSRF token from cookie for secure requests
 */
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}