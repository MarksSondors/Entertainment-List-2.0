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

// Review Modal Functions
function openReviewModal(isEdit = false) {
    document.getElementById('reviewModal').style.display = 'block';
    document.getElementById('reviewModalTitle').textContent = isEdit ? 'Edit Review' : 'Write a Review';
    
    if (isEdit && window.userReviewId) {
        document.getElementById('rating').value = window.userReviewRating;
        document.getElementById('ratingValue').textContent = window.userReviewRating;
        document.getElementById('reviewText').value = window.userReviewText;
        // Set the watch date if it exists, otherwise use today's date
        if (window.userWatchDate) {
            document.getElementById('watchDate').value = window.userWatchDate;
        }
        
        // Add hidden input for review ID
        let reviewIdInput = document.getElementById('reviewId');
        if (!reviewIdInput) {
            reviewIdInput = document.createElement('input');
            reviewIdInput.type = 'hidden';
            reviewIdInput.id = 'reviewId';
            document.getElementById('reviewModal').appendChild(reviewIdInput);
        }
        reviewIdInput.value = window.userReviewId;
    } else {
        document.getElementById('rating').value = 5;
        document.getElementById('ratingValue').textContent = 5;
        document.getElementById('reviewText').value = '';
        // For new reviews, set today's date as default
        const today = new Date().toISOString().split('T')[0];
        document.getElementById('watchDate').value = today;
        
        // Remove review ID if exists
        const reviewIdInput = document.getElementById('reviewId');
        if (reviewIdInput) reviewIdInput.remove();
    }
}

function closeReviewModal() {
    document.getElementById('reviewModal').style.display = 'none';
}

function submitReview(contentId, contentType) {
    const rating = document.getElementById('rating').value;
    const reviewText = document.getElementById('reviewText').value;
    const watchDate = document.getElementById('watchDate').value;
    const reviewId = document.getElementById('reviewId')?.value;
    
    const endpoint = `/${contentType}s/reviews/`;  // Note: Add 's' to make endpoint plural
    const method = reviewId ? 'PUT' : 'POST';
    const body = reviewId ? 
        JSON.stringify({
            review_id: reviewId,
            rating: rating,
            review_text: reviewText,
            date_added: watchDate
        }) : 
        JSON.stringify({
            [`${contentType}_id`]: contentId,
            rating: rating,
            review_text: reviewText,
            date_added: watchDate
        });
    
    fetch(endpoint, {
        method: method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: body
    })
    .then(response => {
        // First check if response is ok before trying to parse JSON
        if (!response.ok) {
            throw new Error(`Server responded with ${response.status}: ${response.statusText}`);
        }
        
        // Check content type to ensure we're getting JSON
        const contentType = response.headers.get('content-type');
        if (contentType && contentType.includes('application/json')) {
            return response.json();
        } else {
            // If not JSON, get the text and throw an error
            return response.text().then(text => {
                throw new Error(`Expected JSON but got: ${text.substring(0, 100)}...`);
            });
        }
    })
    .then(data => {
        closeReviewModal();
        loadReviews(contentId, contentType);
        
        // If this was a new review, change button to "Edit Review"
        if (!reviewId) {
            const reviewButton = document.querySelector('button[onclick*="openReviewModal"]');
            if (reviewButton) {
                reviewButton.textContent = 'Edit Review';
                reviewButton.onclick = function() { openReviewModal(true); };
            }
        }
    })
    .catch(error => {
        console.error('Error submitting review:', error);
        alert(`Error submitting review: ${error.message}`);
    });
}

function loadReviews(contentId, contentType) {
    // Make sure contentType is properly formatted (e.g., 'movie', not 'movies')
    const singularType = contentType.endsWith('s') ? contentType : contentType + 's';
    
    fetch(`/${singularType}/reviews/?${contentType}_id=${contentId}`)
    .then(response => {
        if (!response.ok) {
            throw new Error(`Server responded with ${response.status}: ${response.statusText}`);
        }
        
        // Check content type to ensure we're getting JSON
        const contentType = response.headers.get('content-type');
        if (contentType && contentType.includes('application/json')) {
            return response.json();
        } else {
            // If not JSON, get the text and throw an error
            return response.text().then(text => {
                throw new Error(`Expected JSON but got: ${text.substring(0, 100)}...`);
            });
        }
    })
    .then(reviews => {
        const reviewsTable = document.getElementById('reviewsTableBody') || 
                            document.querySelector('.sunken-panel table tbody');
        
        if (!reviewsTable) {
            console.error('Reviews table body element not found');
            return;
        }
        
        reviewsTable.innerHTML = ''; // Clear existing reviews
        
        if (reviews.length === 0) {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td colspan="5" style="padding: 4px; text-align: center;">
                    No reviews yet. Be the first to review this ${contentType}!
                </td>
            `;
            reviewsTable.appendChild(row);
            return;
        }
        
        const currentUsername = document.querySelector('meta[name="username"]')?.content || '';
        
        reviews.forEach(review => {
            const row = document.createElement('tr');
            
            // Format the review date
            const reviewDate = new Date(review.date_added);
            const formattedReviewDate = reviewDate.toLocaleDateString('en-US', { 
                year: 'numeric', 
                month: 'short', 
                day: 'numeric' 
            });
            
            // Format the watched date if available
            let watchedDateDisplay = '';
            if (review.date_watched) {
                const watchedDate = new Date(review.date_watched);
                watchedDateDisplay = watchedDate.toLocaleDateString('en-US', {
                    year: 'numeric',
                    month: 'short',
                    day: 'numeric'
                });
            }
            
            // Check if current user is the review author
            const isAuthor = review.user === currentUsername;
            const actionButtons = isAuthor ? `
                <button onclick="editReview(${review.id}, ${review.rating}, '${review.review_text ? review.review_text.replace(/'/g, "\\'") : ''}')" 
                        style="font-size: 12px; background-color: #C3C3C3; border: 1px outset; padding: 2px; margin-right: 5px;">
                    Edit
                </button>
                <button onclick="deleteReview(${review.id}, '${contentType}')" 
                        style="font-size: 12px; background-color: #C3C3C3; border: 1px outset; padding: 2px;">
                    Delete
                </button>
            ` : '';
            
            row.innerHTML = `
                <td style="padding: 4px; border-bottom: 1px solid #ddd;">${review.user}</td>
                <td style="padding: 4px; text-align: center; border-bottom: 1px solid #ddd;">${review.rating}/10</td>
                <td style="padding: 4px; border-bottom: 1px solid #ddd;">${review.review_text || ''}</td>
                <td style="padding: 4px; text-align: right; border-bottom: 1px solid #ddd;">
                    ${formattedReviewDate}
                    <div>${actionButtons}</div>
                </td>
                <td style="padding: 4px; text-align: right; border-bottom: 1px solid #ddd;">
                    ${watchedDateDisplay}
                </td>
            `;
            
            reviewsTable.appendChild(row);
        });
        
        // Check if current user has already reviewed this content
        const userReview = reviews.find(review => review.user === currentUsername);
        if (userReview) {
            const reviewButton = document.querySelector('button[onclick*="openReviewModal"]');
            if (reviewButton) {
                reviewButton.textContent = 'Edit Review';
                reviewButton.onclick = function() { openReviewModal(true); };
            }
            // Store the user's review ID 
            window.userReviewId = userReview.id;
            window.userReviewRating = userReview.rating;
            window.userReviewText = userReview.review_text;
            window.userWatchDate = userReview.date_watched;
        }
    })
    .catch(error => {
        console.error('Error loading reviews:', error);
        const reviewsTable = document.getElementById('reviewsTableBody') || 
                            document.querySelector('.sunken-panel table tbody');
        
        if (reviewsTable) {
            reviewsTable.innerHTML = `
                <tr>
                    <td colspan="5" style="padding: 4px; text-align: center;">
                        Error loading reviews: ${error.message}
                    </td>
                </tr>
            `;
        }
    });
}

function editReview(id, rating, text) {
    window.userReviewId = id;
    window.userReviewRating = rating;
    window.userReviewText = text;
    openReviewModal(true);
}

function deleteReview(id, contentType) {
    if (confirm('Are you sure you want to delete your review?')) {
        const endpoint = `/${contentType}s/reviews/`;
        
        fetch(endpoint, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({
                review_id: id
            })
        })
        .then(response => {
            if (response.ok) {
                const contentId = new URLSearchParams(window.location.search).get('id') || 
                                 window.location.pathname.split('/').filter(Boolean).pop();
                
                loadReviews(contentId, contentType);
                // Reset the review button
                const reviewButton = document.querySelector('button[onclick*="openReviewModal"]');
                if (reviewButton) {
                    reviewButton.textContent = 'Write a Review';
                    reviewButton.onclick = function() { openReviewModal(); };
                }
                window.userReviewId = null;
            } else {
                return response.json().then(data => {
                    throw new Error(data.error || 'Failed to delete review');
                }).catch(() => {
                    throw new Error(`Server responded with ${response.status}: ${response.statusText}`);
                });
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert(error.message || 'Error deleting review');
        });
    }
}

// Initialize review system
document.addEventListener('DOMContentLoaded', function() {
    const ratingSlider = document.getElementById('rating');
    if (ratingSlider) {
        const ratingValue = document.getElementById('ratingValue');
        ratingSlider.addEventListener('input', function() {
            ratingValue.textContent = this.value;
        });
    }
    
    // Add metadata for current user if not already present
    if (!document.querySelector('meta[name="username"]')) {
        const meta = document.createElement('meta');
        meta.name = 'username';
        meta.content = document.body.dataset.username || '';
        document.head.appendChild(meta);
    }
});