// Strava HR Zones Display popup script
// This file can be used for logic related to the extension's popup window.
// For now, it handles login and sync button clicks.

// Helper function to get a cookie by name from a specific URL
async function getCookie(name, url) {
    return new Promise((resolve, reject) => {
        if (typeof chrome !== "undefined" && chrome.cookies) {
            chrome.cookies.get({ url: url, name: name }, function(cookie) {
                if (chrome.runtime.lastError) {
                    // Log error but still resolve with null as the cookie might not exist
                    console.warn(`Error getting cookie '${name}' from ${url}: ${chrome.runtime.lastError.message}`);
                    resolve(null);
                } else if (cookie) {
                    resolve(cookie.value);
                } else {
                    resolve(null); // Cookie not found
                }
            });
        } else {
            // Fallback for environments where chrome.cookies is not available (e.g. testing outside extension)
            // This fallback won't work for cross-origin httpOnly cookies in a real extension context.
            console.warn("chrome.cookies API not available. Falling back to document.cookie (may not work for httpOnly cookies).");
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
            resolve(cookieValue);
        }
    });
}

document.addEventListener('DOMContentLoaded', function() {
    const loginButton = document.getElementById('loginButton');
    const syncButton = document.getElementById('syncButton');
    const afterDateInput = document.getElementById('afterDate');
    const statusMessage = document.getElementById('statusMessage');

    const API_BASE_URL = 'https://localhost:8000/api'; // Ensure this matches your backend

    if (loginButton) {
        loginButton.addEventListener('click', function() {
            statusMessage.textContent = ''; // Clear previous messages
            chrome.tabs.create({ url: `${API_BASE_URL}/auth/strava` });
        });
    }

    if (syncButton) {
        // Function to update status message and apply class
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

        syncButton.addEventListener('click', async function() {
            syncButton.disabled = true;
            updateStatus('Syncing data from Strava...', 'info');

            try {
                const backendOrigin = 'https://localhost:8000'; // Define base origin
                const csrftoken = await getCookie('csrftoken', backendOrigin); // Await the async call

                if (!csrftoken) {
                    updateStatus('Error: CSRF token not found. Ensure you are logged in to the backend and cookie settings are correct.', 'error');
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
                    const result = await response.json(); // Assuming backend sends JSON
                    updateStatus(`Sync successful: ${result.synced_activities_count} activities synced. Last sync: ${new Date(result.last_synced_timestamp).toLocaleString()}`, 'success');
                    console.log('Sync successful:', result);
                } else {
                    const errorData = await response.text(); // Try to get error text
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
});
