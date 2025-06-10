// Helper function to get a cookie by name from a specific URL
async function getCookie(name, url) {
    return new Promise((resolve) => {
        if (typeof chrome !== "undefined" && chrome.cookies) {
            chrome.cookies.get({ url: url, name: name }, function(cookie) {
                if (chrome.runtime.lastError) {
                    console.warn(`Error getting cookie '${name}' from ${url}: ${chrome.runtime.lastError.message}`);
                    resolve(null);
                } else if (cookie) {
                    resolve(cookie.value);
                } else {
                    resolve(null); // Cookie not found
                }
            });
        } else {
            // This case should ideally not happen in a functioning extension popup.
            // Indicates a more fundamental issue (e.g., script running outside extension context, or chrome object is broken).
            console.error("chrome.cookies API is not available. Cannot retrieve cookies.");
            resolve(null);
        }
    });
}

document.addEventListener('DOMContentLoaded', function() {
    const loginButton = document.getElementById('loginButton');
    const syncButton = document.getElementById('syncButton');
    const viewMyHrZonesButton = document.getElementById('viewMyHrZonesButton');
    const afterDateInput = document.getElementById('afterDate');
    const statusMessage = document.getElementById('statusMessage');

    // Configuration for API endpoints
    const PRODUCTION_DOMAIN = 'https://strava-zones.com';
    const DEVELOPMENT_DOMAIN = 'https://localhost:8000';
    const IS_PRODUCTION_BUILD = true; // Set to true for production builds

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

    async function checkHrZonesAvailability() {
        try {
            const csrftoken = await getCookie('csrftoken', BACKEND_ORIGIN);
            if (!csrftoken) {
                console.error('CSRF token not found for HR zone check.');
                updateStatus('Error: CSRF token not found. Please log in to the backend.', 'error');
                return false;
            }

            const response = await fetch(`${API_BASE_URL}/user/hr-zone-status/`, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrftoken
                },
                credentials: 'include'
            });

            if (response.ok) {
                const data = await response.json();
                return data.has_hr_zones;
            } else {
                const errorData = await response.text();
                console.error('Failed to fetch HR zone status:', response.status, errorData);
                updateStatus(`Error checking HR zones: ${response.status}`, 'error');
                return false;
            }
        } catch (error) {
            console.error('Error during checkHrZonesAvailability:', error);
            updateStatus('Failed to connect to backend for HR zone check.', 'error');
            return false;
        }
    }

    async function updateSyncButtonState() {
        const hrZonesAvailable = await checkHrZonesAvailability();
        if (syncButton) {
            if (hrZonesAvailable) {
                syncButton.disabled = false;
                syncButton.title = 'Sync your Strava activities.';
            } else {
                syncButton.disabled = true;
                syncButton.title = 'Please fetch HR zones first.';
            }
        }
    }

    if (viewMyHrZonesButton) {
        viewMyHrZonesButton.addEventListener('click', function() {
            chrome.tabs.create({ url: `${API_BASE_URL}/user/hr-zones/` });
        });
    }

    if (loginButton) {
        loginButton.addEventListener('click', function() {
            statusMessage.textContent = ''; // Clear previous messages
            chrome.tabs.create({ url: `${API_BASE_URL}/auth/strava` });
        });
    }

    if (syncButton) {

        syncButton.addEventListener('click', async function() {
            syncButton.disabled = true;
            updateStatus('Syncing data from Strava...', 'info');

            try {
                const csrftoken = await getCookie('csrftoken', BACKEND_ORIGIN);

                if (!csrftoken) {
                    updateStatus('Error: CSRF token not found. Ensure you are logged in.', 'error');
                    syncButton.disabled = false;
                    return;
                }

                let requestBody = {};
                const syncAfterDate = afterDateInput.value; // Gets date as YYYY-MM-DD
                if (syncAfterDate) {
                    // Convert YYYY-MM-DD to YYYY-MM-DDTHH:MM:SSZ (start of selected day in UTC)
                    const dateObj = new Date(syncAfterDate + "T00:00:00.000Z");
                    requestBody.after_timestamp = dateObj.toISOString();
                }

                const response = await fetch(`${API_BASE_URL}/strava/sync-activities/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrftoken
                    },
                    body: JSON.stringify(requestBody),
                    credentials: 'include'
                });

                if (response.ok) {
                    const result = await response.json();
                    updateStatus(`Sync successful: ${result.synced_activities_count} activities synced. Last sync: ${new Date(result.last_synced_timestamp).toLocaleString()}`, 'success');
                } else {
                    const errorData = await response.text();
                    updateStatus(`Sync failed: ${response.status} ${response.statusText}. Check console.`, 'error');
                    console.error('Sync failed:', response.status, response.statusText, errorData);
                }
            } catch (error) {
                console.error('Sync request failed:', error);
                updateStatus(`Sync request failed: ${error.message || error.toString()}`, 'error');
            } finally {
                syncButton.disabled = false; // Re-enable button
            }
        });
    }

    // Initial state update for sync button based on HR zones
    updateSyncButtonState();
});
