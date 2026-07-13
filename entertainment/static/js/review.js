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

class ReviewModalController {
    constructor() {
        this.modal = document.getElementById('reviewModal');
        this.overlay = document.getElementById('reviewModalOverlay');
        this.form = document.getElementById('reviewForm');
        this.state = null;

        if (!this.modal || !this.overlay || !this.form) {
            return;
        }

        this.rating = document.getElementById('rating');
        this.ratingValue = document.getElementById('ratingValue');
        this.reviewText = document.getElementById('reviewText');
        this.reviewDate = document.getElementById('reviewDate');
        this.reviewId = document.getElementById('reviewId');
        this.charCount = document.getElementById('charCount');
        this.loadingIndicator = document.getElementById('reviewLoadingIndicator');
        this.submitButton = document.getElementById('submitBtn');
        this.title = document.getElementById('reviewModalTitle');

        this.form.addEventListener('submit', event => this.submit(event));
        this.rating.addEventListener('input', () => this.updateRatingDisplay());
        this.reviewText.addEventListener('input', () => this.updateCharacterCount());
        this.overlay.addEventListener('click', () => this.close());
        this.modal.querySelectorAll('[data-review-modal-close]').forEach(button => {
            button.addEventListener('click', () => this.close());
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && !this.modal.hidden) {
                this.close();
            }
        });
    }

    getDefaultState() {
        return {
            contentId: this.modal.dataset.reviewContentId,
            contentType: this.modal.dataset.reviewMediaType,
            title: this.modal.dataset.reviewTitle,
        };
    }

    open(options = {}) {
        if (!this.modal) {
            return;
        }

        const defaults = this.getDefaultState();
        const legacyReview = window.userReviewId && !options.forceNew ? {
            id: window.userReviewId,
            rating: window.userReviewRating,
            reviewText: window.userReviewText,
            dateAdded: window.userWatchDate,
        } : null;
        const review = options.review || legacyReview;

        this.state = { ...defaults, ...options, review };
        this.form.reset();
        this.reviewId.value = review?.id || '';
        this.rating.value = review?.rating || 5;
        this.reviewText.value = review?.reviewText || '';
        this.reviewDate.value = this.toLocalDate(review?.dateAdded) || this.today();
        this.title.textContent = review ? 'Edit Review' : (this.state.title ? `Review ${this.state.title}` : 'Write a Review');
        this.updateRatingDisplay();
        this.updateCharacterCount();
        this.overlay.hidden = false;
        this.modal.hidden = false;
        document.body.style.overflow = 'hidden';
        window.setTimeout(() => this.reviewText.focus(), 0);
    }

    close() {
        if (!this.modal) {
            return;
        }

        this.modal.hidden = true;
        this.overlay.hidden = true;
        document.body.style.overflow = '';
    }

    async submit(event) {
        event.preventDefault();
        if (!this.state) {
            return;
        }

        const isEdit = Boolean(this.reviewId.value);
        const requestData = {
            rating: this.rating.value,
            review_text: this.reviewText.value,
            date_added: this.reviewDate.value,
        };

        if (isEdit) {
            requestData.review_id = this.reviewId.value;
        } else if (this.state.contentType === 'tvshow') {
            requestData.tv_show_id = this.state.contentId;
            requestData.season_id = this.state.seasonId || null;
            requestData.episode_subgroup_id = this.state.episodeSubgroupId || null;
        } else {
            requestData[`${this.state.contentType}_id`] = this.state.contentId;
        }

        this.setSubmitting(true);
        try {
            const response = await fetch(this.endpoint(), {
                method: isEdit ? 'PUT' : 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken'),
                },
                body: JSON.stringify(requestData),
            });
            const responseData = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(responseData.error || 'Failed to submit review');
            }

            const review = {
                id: responseData.review_id || responseData.id || this.reviewId.value,
                rating: Number(this.rating.value),
                reviewText: this.reviewText.value,
                dateAdded: this.reviewDate.value,
            };
            this.close();
            if (typeof this.state.onSuccess === 'function') {
                this.state.onSuccess(review, responseData);
            } else if (this.state.contentId && this.state.contentType) {
                loadReviews(this.state.contentId, this.state.contentType);
            }
        } catch (error) {
            console.error('Error submitting review:', error);
            alert(error.message || 'Error submitting review');
        } finally {
            this.setSubmitting(false);
        }
    }

    endpoint() {
        return this.state.contentType === 'tvshow'
            ? '/tvshows/reviews/'
            : `/${this.state.contentType}s/reviews/`;
    }

    updateRatingDisplay() {
        this.ratingValue.textContent = this.rating.value;
    }

    updateCharacterCount() {
        this.charCount.textContent = `${this.reviewText.value.length} characters`;
    }

    setSubmitting(isSubmitting) {
        this.submitButton.disabled = isSubmitting;
        this.loadingIndicator.hidden = !isSubmitting;
    }

    today() {
        return new Date().toISOString().slice(0, 10);
    }

    toLocalDate(value) {
        if (!value) {
            return '';
        }
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) {
            return String(value).slice(0, 10);
        }
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    }
}

window.ReviewModal = null;

function initializeReviewModal() {
    if (!window.ReviewModal) {
        const controller = new ReviewModalController();
        if (controller.modal) {
            window.ReviewModal = controller;
        }
    }
    return window.ReviewModal;
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeReviewModal, { once: true });
} else {
    initializeReviewModal();
}

function openReviewModal(options) {
    const controller = initializeReviewModal();
    if (!controller) {
        return;
    }
    controller.open(typeof options === 'object' ? options : {});
}

function closeReviewModal() {
    window.ReviewModal?.close();
}

function updateRatingDisplay() {
    window.ReviewModal?.updateRatingDisplay();
}

function editReview(id, rating, text, date) {
    window.ReviewModal?.open({
        review: { id, rating, reviewText: text, dateAdded: date },
    });
}

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
    const controller = initializeReviewModal();
    if (!controller) {
        return;
    }

    const options = typeof param1 === 'object' && param1 !== null
        ? param1
        : typeof title === 'string' ? { title } : {};
    controller.open(options);
}

function closeReviewModal() {
    window.ReviewModal?.close();
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