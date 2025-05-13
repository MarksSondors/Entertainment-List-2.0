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

document.addEventListener('DOMContentLoaded', function() {
    const reviewButton = document.querySelector('button[onclick="openReviewModal()"]');
    if (reviewButton) {
        reviewButton.addEventListener('click', function() {
            openReviewModal();
        });
    }
});

window.addEventListener('click', function(event) {
        const modal = document.getElementById('reviewDetailModal');
        if (event.target === modal) {
            closeReviewDetailModal();
        }
    });

// Review Modal Functions
function closeReviewModal() {
    document.getElementById('reviewModal').style.display = 'none';
    document.getElementById('overlay').style.display = 'none';
}

// Helper function to truncate review text for display
function truncateText(text, maxLength) {
    if (!text) return '<em>No text review</em>';
    return text.length > maxLength ? text.substring(0, maxLength) + '...' : text;
}

// Review detail modal functions
function openReviewDetailModal(review, isAuthor, contentType) {
    // Set modal content
    document.getElementById('reviewModalUsername').textContent = review.user;
    document.getElementById('reviewModalRating').textContent = review.rating;
    
    const reviewDate = new Date(review.date_added);
    const formattedDate = reviewDate.toLocaleDateString('en-US', { 
        year: 'numeric', month: 'short', day: 'numeric' 
    });
    document.getElementById('reviewModalDate').textContent = formattedDate;
    
    document.getElementById('reviewModalText').textContent = review.review_text || 'No review text provided.';
    
    // Set action buttons if user is the author
    if (isAuthor) {
        document.getElementById('reviewModalActions').innerHTML = `
            <button onclick="editReview(${review.id}, ${review.rating}, '${review.review_text ? review.review_text.replace(/'/g, "\\'") : ''}', '${review.date_added}'); closeReviewDetailModal();" 
                    style="font-family: 'MS Sans Serif', Arial, sans-serif; background-color: #C3C3C3; border: 2px outset #fff; padding: 3px 8px; cursor: pointer;">
                Edit
            </button>
            <button onclick="if(confirm('Are you sure you want to delete this review?')) { deleteReview(${review.id}, '${contentType}'); closeReviewDetailModal(); }" 
                    style="font-family: 'MS Sans Serif', Arial, sans-serif; background-color: #C3C3C3; border: 2px outset #fff; padding: 3px 8px; cursor: pointer;">
                Delete
            </button>
        `;
    } else {
        document.getElementById('reviewModalActions').innerHTML = `
            <button onclick="closeReviewDetailModal()" 
                    style="font-family: 'MS Sans Serif', Arial, sans-serif; background-color: #C3C3C3; border: 2px outset #fff; padding: 3px 8px; cursor: pointer;">
                Close
            </button>
        `;
    }
    
    // Show modal
    document.getElementById('reviewDetailModal').style.display = 'block';
}

function closeReviewDetailModal() {
    document.getElementById('reviewDetailModal').style.display = 'none';
}

// Add event listener to close modal when clicking outside
document.addEventListener('DOMContentLoaded', function() {
    // ...existing code...
    
    // Close review detail modal when clicking outside
    window.addEventListener('click', function(event) {
        const modal = document.getElementById('reviewDetailModal');
        if (event.target === modal) {
            closeReviewDetailModal();
        }
    });
});

function submitReview(contentId, contentType, event) {
    if (event) event.preventDefault();
    
    // When editing, ensure the review ID is included
    let reviewId;
    const reviewIdInput = document.getElementById('reviewId');
    
    if (window.userReviewId && reviewIdInput) {
        // If we're editing and have the input, use its value
        reviewId = reviewIdInput.value || window.userReviewId;
    } else if (window.userReviewId) {
        // Fallback to window variable if input element doesn't exist
        reviewId = window.userReviewId;
    } else {
        // No review ID means we're creating a new review
        reviewId = null;
    }
    
    const form = document.getElementById('reviewForm');
    const rating = document.getElementById('rating').value;
    const reviewText = document.getElementById('reviewText').value;
    const reviewDate = document.getElementById('reviewDate')?.value;
    
    // Debug to check if reviewId is being properly set
    console.log("Review ID:", reviewId, "Is Edit:", !!reviewId);
    
    const isEdit = !!reviewId;
    const requestData = {
        rating: rating,
        review_text: reviewText,
    };
    
    if (isEdit) {
        requestData.review_id = reviewId;
        if (reviewDate) {
            requestData.date_added = reviewDate; // Ensure we're sending date_added
        }
    } else {
        requestData[`${contentType}_id`] = contentId;
        if (reviewDate) {
            requestData.date_added = reviewDate; // For new reviews too
        }
    }
    
    // Show loading indicator if present
    const loadingIndicator = document.getElementById('reviewLoadingIndicator');
    if (loadingIndicator) loadingIndicator.style.display = 'flex';
    
    // Use explicit PUT for edits to ensure correct method
    const method = isEdit ? 'PUT' : 'POST';
    console.log("Using HTTP method:", method);
    
    fetch(`/${contentType}s/reviews/`, {
        method: method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify(requestData)
    })
    .then(response => {
        if (response.ok) {
            document.getElementById('reviewModal').style.display = 'none';
            document.getElementById('overlay').style.display = 'none';
            loadReviews(contentId, contentType);
            form.reset();
            document.getElementById('ratingValue').textContent = '5';
            if (isEdit) {
                window.userReviewId = null;
                window.userReviewRating = null;
                window.userReviewText = null;
                window.userReviewDate = null;
            }
        } else {
            return response.json().then(data => {
                throw new Error(data.error || 'Failed to submit review');
            });
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert(error.message || 'Error submitting review');
    })
    .finally(() => {
        // Hide loading indicator
        if (loadingIndicator) loadingIndicator.style.display = 'none';
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
                <button onclick="editReview(${review.id}, ${review.rating}, '${review.review_text ? review.review_text.replace(/'/g, "\\'") : ''}', '${review.date_added}')" 
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
                reviewButton.onclick = function() { 
                    editReview(userReview.id, userReview.rating, userReview.review_text || '', userReview.date_added); 
                };
            }
            // Store the user's review data - this is the key change
            window.userReviewId = userReview.id;
            window.userReviewRating = userReview.rating;
            window.userReviewText = userReview.review_text;
            
            // Store both the original ISO date and the local date
            window.userWatchDate = userReview.date_added;
            
            // Store the date without time component in user's local timezone
            const localDate = new Date(userReview.date_added);
            window.userWatchLocalDate = localDate.getFullYear() + '-' + 
                                      String(localDate.getMonth() + 1).padStart(2, '0') + '-' + 
                                      String(localDate.getDate()).padStart(2, '0');
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

function editReview(id, rating, text, date) {
    // Store review data in window variables
    window.userReviewId = id;
    window.userReviewRating = rating;
    
    // Fix for review text: decode HTML entities
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    window.userReviewText = textarea.value;
    
    // Fix for date: preserve the original ISO string to avoid timezone issues
    if (date) {
        // Store the complete ISO string instead of just the date part
        window.userWatchDate = date;
        console.log("Stored watch date:", window.userWatchDate);
    } else {
        window.userWatchDate = new Date().toISOString();
    }
    
    // Open the review modal in edit mode
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

// Enhanced Review Modal Functions
function updateRatingDisplay(value) {
    const ratingValue = document.getElementById('ratingValue');
    if (ratingValue) {
        ratingValue.textContent = value;
    }
}

// Universal openReviewModal that handles all use cases
function openReviewModal(param1, param2, title) {
    const modal = document.getElementById('reviewModal');
    const overlay = document.getElementById('overlay');
    
    if (!modal || !overlay) {
        console.error('Review modal elements not found');
        return;
    }
    
    // Determine if this is an edit operation based on parameter OR existing user review
    // This fixes the issue when the button calls openReviewModal() without parameters
    const isEdit = (typeof param1 === 'boolean' && param1) || 
                  (param1 === undefined && window.userReviewId);
    
    console.log("Opening review modal, isEdit:", isEdit, "userReviewId:", window.userReviewId);
    
    // Set modal title
    const modalTitle = document.getElementById('reviewModalTitle');
    if (modalTitle) {
        if (typeof title === 'string') {
            modalTitle.textContent = `Review ${title}`;
        } else {
            modalTitle.textContent = isEdit ? 'Edit Review' : 'Write a Review';
        }
    }
    
    // For edit operations, pre-populate with existing review data
    if (isEdit && window.userReviewId) {
        const rating = document.getElementById('rating');
        const ratingValue = document.getElementById('ratingValue');
        const reviewText = document.getElementById('reviewText');
        const reviewDate = document.getElementById('reviewDate');
        const charCount = document.getElementById('charCount');
        
        if (rating) rating.value = window.userReviewRating;
        if (ratingValue) ratingValue.textContent = window.userReviewRating;
        if (reviewText) reviewText.value = window.userReviewText || '';
        if (charCount) charCount.textContent = (window.userReviewText || '').length + ' characters';
        
        // Set the watch date if it exists
        if (reviewDate) {
            if (window.userWatchDate) {
                console.log("Setting form date to:", window.userWatchDate);
                // Use the locally stored date value that's already in the correct timezone
                if (window.userWatchLocalDate) {
                    reviewDate.value = window.userWatchLocalDate;
                } else {
                    // Fallback to extracting date from the ISO string
                    const dateOnly = window.userWatchDate.split('T')[0];
                    reviewDate.value = dateOnly;
                }
            } else {
                reviewDate.value = new Date().toISOString().split('T')[0];
            }
        }
        
        // Add hidden input for review ID
        let reviewIdInput = document.getElementById('reviewId');
        if (!reviewIdInput) {
            reviewIdInput = document.createElement('input');
            reviewIdInput.type = 'hidden';
            reviewIdInput.id = 'reviewId';
            reviewIdInput.name = 'reviewId';
            const form = document.getElementById('reviewForm');
            if (form) form.appendChild(reviewIdInput);
        }
        reviewIdInput.value = window.userReviewId;
        console.log("Set review ID for edit:", window.userReviewId);
    } else {
        // Reset form for new review
        const rating = document.getElementById('rating');
        const ratingValue = document.getElementById('ratingValue');
        const reviewText = document.getElementById('reviewText');
        const reviewDate = document.getElementById('reviewDate');
        const charCount = document.getElementById('charCount');
        const form = document.getElementById('reviewForm');
        
        if (form) form.reset();
        if (rating) rating.value = 5;
        if (ratingValue) ratingValue.textContent = '5';
        if (reviewText) reviewText.value = '';
        if (charCount) charCount.textContent = '0 characters';
        
        // For new reviews, set today's date as default
        if (reviewDate) {
            const today = new Date().toISOString().split('T')[0];
            reviewDate.value = today;
        }
        
        // Remove review ID if exists
        const reviewIdInput = document.getElementById('reviewId');
        if (reviewIdInput) reviewIdInput.remove();
    }
    
    // Make sure overlay is below modal
    overlay.style.zIndex = "999";
    modal.style.zIndex = "1000";
    
    // Show modal and overlay
    modal.style.display = 'block';
    overlay.style.display = 'block';
    
    // Prevent background scrolling
    document.body.style.overflow = 'hidden';
}

function closeReviewModal() {
    const modal = document.getElementById('reviewModal');
    const overlay = document.getElementById('overlay');
    
    if (modal) modal.style.display = 'none';
    if (overlay) overlay.style.display = 'none';
    
    // Re-enable scrolling
    document.body.style.overflow = 'auto';
}

// Initialize review system with enhanced event handlers
document.addEventListener('DOMContentLoaded', function() {
    // Set up review text character counter
    const reviewText = document.getElementById('reviewText');
    if (reviewText) {
        reviewText.addEventListener('input', function() {
            const charCount = document.getElementById('charCount');
            if (charCount) {
                const currentLength = this.value.length;
                charCount.textContent = currentLength + ' characters';
            }
        });
    }
    
    // Add overlay click handler to close the modal
    const overlay = document.getElementById('overlay');
    if (overlay) {
        overlay.addEventListener('click', function(event) {
            closeReviewModal();
            event.stopPropagation();
        });
    }

    // Prevent clicks inside the modal from bubbling up
    const reviewModal = document.getElementById('reviewModal');
    if (reviewModal) {
        reviewModal.addEventListener('click', function(event) {
            event.stopPropagation();
        });
    }
    
    // Initialize rating slider
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