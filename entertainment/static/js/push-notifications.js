/**
 * Push Notification Manager for Entertainment List
 * Handles permission requests, subscription management, and notification display
 */

class PushNotificationManager {
    constructor() {
        this.vapidPublicKey = null;
        this.serviceWorkerRegistration = null;
        this.subscription = null;
        this.isSupported = this.checkSupport();
    }

    /**
     * Check if push notifications are supported
     */
    checkSupport() {
        return 'serviceWorker' in navigator && 
               'PushManager' in window && 
               'Notification' in window;
    }

    /**
     * Initialize the notification manager
     */
    async initialize() {
        if (!this.isSupported) {
            console.warn('Push notifications are not supported in this browser');
            return false;
        }

        try {
            // Get VAPID public key from server
            await this.fetchVapidPublicKey();
            
            // Register service worker if not already registered
            if (!this.serviceWorkerRegistration) {
                this.serviceWorkerRegistration = await navigator.serviceWorker.register('/static/sw.js');
                console.log('Service Worker registered:', this.serviceWorkerRegistration);
            }

            // Check existing subscription
            this.subscription = await this.serviceWorkerRegistration.pushManager.getSubscription();
            
            return true;
        } catch (error) {
            console.error('Error initializing push notifications:', error);
            return false;
        }
    }

    /**
     * Fetch VAPID public key from server
     */
    async fetchVapidPublicKey() {
        try {
            const response = await fetch('/api/notifications/vapid-public-key/', {
                headers: {
                    'Accept': 'application/json',
                }
            });
            
            if (!response.ok) {
                throw new Error('Failed to fetch VAPID public key');
            }
            
            const data = await response.json();
            this.vapidPublicKey = data.public_key;
            console.log('VAPID public key loaded');
        } catch (error) {
            console.error('Error fetching VAPID public key:', error);
            throw error;
        }
    }

    /**
     * Request notification permission from user
     */
    async requestPermission() {
        if (!this.isSupported) {
            throw new Error('Push notifications not supported');
        }

        const permission = await Notification.requestPermission();
        console.log('Notification permission:', permission);
        return permission === 'granted';
    }

    /**
     * Subscribe to push notifications
     */
    async subscribe() {
        try {
            // Check if already initialized
            if (!this.serviceWorkerRegistration) {
                await this.initialize();
            }

            // Validate VAPID public key
            if (!this.vapidPublicKey) {
                throw new Error('VAPID public key not loaded. Please generate keys using: python manage.py generate_vapid_keys');
            }

            console.log('VAPID Public Key:', this.vapidPublicKey);
            console.log('VAPID Key Length:', this.vapidPublicKey.length);

            // Request permission if needed
            if (Notification.permission !== 'granted') {
                const granted = await this.requestPermission();
                if (!granted) {
                    throw new Error('Notification permission denied');
                }
            }

            // Create push subscription
            const applicationServerKey = this.urlBase64ToUint8Array(this.vapidPublicKey);
            console.log('Converted applicationServerKey:', applicationServerKey);
            
            this.subscription = await this.serviceWorkerRegistration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: applicationServerKey
            });

            console.log('Push subscription created:', this.subscription);

            // Send subscription to server
            await this.sendSubscriptionToServer(this.subscription);
            
            return this.subscription;
        } catch (error) {
            console.error('Error subscribing to push notifications:', error);
            throw error;
        }
    }

    /**
     * Unsubscribe from push notifications
     */
    async unsubscribe() {
        try {
            if (!this.subscription) {
                this.subscription = await this.serviceWorkerRegistration.pushManager.getSubscription();
            }

            if (this.subscription) {
                // Unsubscribe from browser
                await this.subscription.unsubscribe();
                
                // Remove subscription from server
                await this.removeSubscriptionFromServer(this.subscription);
                
                this.subscription = null;
                console.log('Successfully unsubscribed from push notifications');
                return true;
            }
            
            return false;
        } catch (error) {
            console.error('Error unsubscribing from push notifications:', error);
            throw error;
        }
    }

    /**
     * Check if user is currently subscribed
     */
    async isSubscribed() {
        if (!this.serviceWorkerRegistration) {
            await this.initialize();
        }
        
        this.subscription = await this.serviceWorkerRegistration.pushManager.getSubscription();
        return this.subscription !== null;
    }

    /**
     * Send subscription to Django backend
     */
    async sendSubscriptionToServer(subscription) {
        const subscriptionJSON = subscription.toJSON();
        const deviceName = this.getDeviceName();

        const response = await fetch('/api/notifications/subscriptions/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.getCsrfToken(),
            },
            body: JSON.stringify({
                endpoint: subscriptionJSON.endpoint,
                p256dh: subscriptionJSON.keys.p256dh,
                auth: subscriptionJSON.keys.auth,
                device_name: deviceName,
            })
        });

        if (!response.ok) {
            throw new Error('Failed to send subscription to server');
        }

        console.log('Subscription sent to server');
        return await response.json();
    }

    /**
     * Remove subscription from Django backend
     */
    async removeSubscriptionFromServer(subscription) {
        const subscriptionJSON = subscription.toJSON();

        const response = await fetch('/api/notifications/subscriptions/unsubscribe_by_endpoint/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.getCsrfToken(),
            },
            body: JSON.stringify({
                endpoint: subscriptionJSON.endpoint,
            })
        });

        if (!response.ok) {
            throw new Error('Failed to remove subscription from server');
        }

        console.log('Subscription removed from server');
    }

    /**
     * Get device name for better management
     */
    getDeviceName() {
        const ua = navigator.userAgent;
        let device = 'Unknown Device';

        // Detect mobile
        if (/Mobile|Android|iPhone|iPad|iPod/.test(ua)) {
            if (/iPhone/.test(ua)) device = 'iPhone';
            else if (/iPad/.test(ua)) device = 'iPad';
            else if (/Android/.test(ua)) device = 'Android Device';
            else device = 'Mobile Device';
        } else {
            // Desktop
            if (/Mac/.test(ua)) device = 'Mac';
            else if (/Windows/.test(ua)) device = 'Windows PC';
            else if (/Linux/.test(ua)) device = 'Linux PC';
            else device = 'Desktop';
        }

        // Add browser
        if (/Chrome/.test(ua) && !/Edge/.test(ua)) device += ' (Chrome)';
        else if (/Firefox/.test(ua)) device += ' (Firefox)';
        else if (/Safari/.test(ua) && !/Chrome/.test(ua)) device += ' (Safari)';
        else if (/Edge/.test(ua)) device += ' (Edge)';

        return device;
    }

    /**
     * Convert base64 VAPID key to Uint8Array
     */
    urlBase64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding)
            .replace(/\-/g, '+')
            .replace(/_/g, '/');

        const rawData = window.atob(base64);
        const outputArray = new Uint8Array(rawData.length);

        for (let i = 0; i < rawData.length; ++i) {
            outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
    }

    /**
     * Get CSRF token from cookie
     */
    getCsrfToken() {
        const name = 'csrftoken';
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

    /**
     * Show a test notification (for debugging)
     */
    async showTestNotification() {
        if (!this.serviceWorkerRegistration) {
            await this.initialize();
        }

        await this.serviceWorkerRegistration.showNotification('Test Notification', {
            body: 'This is a test notification from Entertainment List',
            icon: '/static/favicon/web-app-manifest-192x192.png',
            badge: '/static/favicon/favicon-96x96.png',
            vibrate: [200, 100, 200],
            tag: 'test',
        });
    }
}

// Create global instance
window.pushNotificationManager = new PushNotificationManager();

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    await window.pushNotificationManager.initialize();
    console.log('Push Notification Manager initialized');
});
