document.addEventListener('DOMContentLoaded', function() {
    const loginButton = document.getElementById('loginButton');
    const viewMyHrZonesButton = document.getElementById('viewMyHrZonesButton');
    const statusMessage = document.getElementById('statusMessage');

    // Configuration for API endpoints
    const PRODUCTION_DOMAIN = 'https://strava-zones.com';
    const DEVELOPMENT_DOMAIN = 'https://localhost:8000';
    const IS_PRODUCTION_BUILD = true;

    const BACKEND_ORIGIN = IS_PRODUCTION_BUILD ? PRODUCTION_DOMAIN : DEVELOPMENT_DOMAIN;
    const API_BASE_URL = `${BACKEND_ORIGIN}/api`;

    function updateStatus(message, type) {
        statusMessage.textContent = message;
        statusMessage.className = ''; // Clear existing classes
        if (type === 'success') {
            statusMessage.classList.add('status-success');
        } else if (type === 'error') {
            statusMessage.classList.add('status-error');
        } else if (type === 'info') {
            statusMessage.classList.add('status-info');
        } else {
            statusMessage.textContent = message; // Default, no class
        }
    }

    // Simple two-state auth check
    async function checkAuthState() {
        // Default state: disabled, greyed out, with an info message
        viewMyHrZonesButton.disabled = true;
        viewMyHrZonesButton.style.backgroundColor = '#e0e0e0';
        viewMyHrZonesButton.style.color = '#a0a0a0';
        viewMyHrZonesButton.style.cursor = 'not-allowed';
        loginButton.style.display = 'block';
        updateStatus('Please log in and connect to Strava.', 'info');

        try {
            const response = await fetch(`${API_BASE_URL}/profile/`, {
                method: 'GET',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include'
            });

            if (response.ok) {
                const data = await response.json();
                if (data.strava_id) {
                    // OK state: enabled, default style, with a success message
                    viewMyHrZonesButton.disabled = false;
                    viewMyHrZonesButton.style.backgroundColor = '';
                    viewMyHrZonesButton.style.color = '';
                    viewMyHrZonesButton.style.cursor = 'pointer';
                    loginButton.style.display = 'none';
                    updateStatus(`Logged in.`, 'info');
                }
            }
            // Any other case (not ok, no strava_id) keeps the default disabled state.

        } catch (error) {
            console.error('Auth check failed:', error);
            updateStatus('Unexpected error.', 'error');
        }
    }

    // Run the check when the popup opens
    checkAuthState();

    if (viewMyHrZonesButton) {
        viewMyHrZonesButton.addEventListener('click', function() {
            // The browser will prevent the click if the button is disabled
            chrome.tabs.create({ url: `${API_BASE_URL}/user/hr-zones/` });
        });
    }

    if (loginButton) {
        loginButton.addEventListener('click', function() {
            statusMessage.textContent = ''; // Clear previous messages
            chrome.tabs.create({ url: `${API_BASE_URL}/auth/strava` });
        });
    }
});
