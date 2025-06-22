const MONTHLY_SUMMARY_ID = 'hr-zones-monthly-summary-container';
const WEEKLY_SUMMARY_CLASS = 'injected-weekly-hr-summary';

// --- State ---
let showWeeklyAsPercentage = true;
let currentMonthData = null; // To store data for re-renders


const PRODUCTION_DOMAIN = 'https://strava-zones.com';
const DEVELOPMENT_DOMAIN = 'https://localhost:8000';
const IS_PRODUCTION_BUILD = false; // Set to true for production builds

const BACKEND_ORIGIN = IS_PRODUCTION_BUILD ? PRODUCTION_DOMAIN : DEVELOPMENT_DOMAIN;
const API_BASE_URL = `${BACKEND_ORIGIN}/api`;

// Month name map for API consistency if needed (Strava hash vs API expectation)
const monthMap = {
    'Jan': 'January', 'Feb': 'February', 'Mar': 'March', 'Apr': 'April',
    'May': 'May', 'Jun': 'June', 'Jul': 'July', 'Aug': 'August',
    'Sep': 'September', 'Oct': 'October', 'Nov': 'November', 'Dec': 'December'
};

// Reverse map to get month number from short name (e.g., Feb -> 2)
const monthNumberMap = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4,
    'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8,
    'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
};

async function fetchActivityData(year, monthShortName) {
    const monthNum = monthNumberMap[monthShortName];

    if (!monthNum) {
        console.error(`Invalid month short name: ${monthShortName}`);
        return null;
    }

    // Now targets /api/zones/ and expects year & month as query params.
    // User identification is expected to be handled by the backend via session.
    const apiUrl = `${API_BASE_URL}/zones/?year=${year}&month=${monthNum}`;

    try {
        const response = await fetch(apiUrl, {
            method: 'GET',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
            }
        });
        if (!response.ok) {
            const errorText = await response.text();
            console.error(`Error fetching data: ${response.status} ${response.statusText}`, errorText);
            throw new Error(`HTTP error ${response.status}: ${errorText}`);
        }
        const data = await response.json();
        return data;
    } catch (error) {
        console.error('Failed to fetch activity data:', error);
        return null;
    }
}

function formatSecondsToHms(totalSeconds) {
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
}

function renderMonthlySummary(monthKey, data) {
    let summaryContainer = document.getElementById(MONTHLY_SUMMARY_ID);
    if (!summaryContainer) {
        summaryContainer = document.createElement('div');
        summaryContainer.id = MONTHLY_SUMMARY_ID;
        document.body.prepend(summaryContainer);
    }

    const totalMonthSeconds = Object.values(data.totalSummary).reduce((sum, time) => sum + time, 0);
    if (totalMonthSeconds === 0) {
        summaryContainer.innerHTML = `<div class="strava-zones-header"><h3>No HR Data for ${data.displayName}</h3></div>`;
        return;
    }

    let contentHtml = `<div class="strava-zones-header"><h3>${data.displayName} HR Zone Summary</h3></div>`;
    contentHtml += '<div class="strava-zones-content">';

    // Dynamically generate orderedSimplifiedZoneKeys from data.zoneDefinitions
    const simplifiedZoneKeys = Object.keys(data.zoneDefinitions || {});
    if (simplifiedZoneKeys.length === 0) {
        console.warn("No zone definitions found in data.zoneDefinitions for monthly summary.");
        summaryContainer.innerHTML += '<p>No heart rate zone definitions found.</p>';
        contentHtml += '</div>';
        summaryContainer.innerHTML = contentHtml;
        return;
    }

    simplifiedZoneKeys.sort((a, b) => {
        const numA = parseInt(a.replace('zone', ''), 10);
        const numB = parseInt(b.replace('zone', ''), 10);
        return numA - numB;
    });
    const orderedSimplifiedZoneKeys = simplifiedZoneKeys.reverse();

    for (const simplifiedZoneKey of orderedSimplifiedZoneKeys) {
        const zoneNumberStr = simplifiedZoneKey.replace('zone', '');

        const actualUserDefinedName = data.zoneDefinitions[simplifiedZoneKey];
        const timeSeconds = actualUserDefinedName ? (data.totalSummary[actualUserDefinedName] || 0) : 0;

        const percentage = totalMonthSeconds > 0 ? (timeSeconds / totalMonthSeconds) * 100 : 0;
        const timeFormatted = formatSecondsToHms(timeSeconds);

        const displayFriendlyZoneName = actualUserDefinedName || `Zone ${zoneNumberStr}`;

        contentHtml += `
          <div class="zone-row">
            <span class="zone-label">Z${zoneNumberStr}: ${displayFriendlyZoneName}</span>
            <div class="zone-bar-container">
              <div class="zone-bar zone${zoneNumberStr}" style="width: ${percentage.toFixed(1)}%;"></div>
              <span class="zone-time-text">${timeFormatted} (${percentage.toFixed(0)}%)</span>
            </div>
          </div>
        `;
    }
    contentHtml += '</div>';
    summaryContainer.innerHTML = contentHtml;

    // Add toggle for weekly view
    const toggleContainer = document.createElement('div');
    toggleContainer.className = 'strava-zones-toggle-container';
    toggleContainer.innerHTML = `
        <label for="weekly-view-toggle-input" class="switch">
            <input type="checkbox" id="weekly-view-toggle-input" ${showWeeklyAsPercentage ? 'checked' : ''}>
            <span class="slider round"></span>
        </label>
        <span id="weekly-view-toggle-label"></span>
    `;
    summaryContainer.appendChild(toggleContainer);

    const toggleInput = document.getElementById('weekly-view-toggle-input');
    const toggleLabel = document.getElementById('weekly-view-toggle-label');

    function updateToggleLabel() {
        if (toggleLabel) {
            toggleLabel.textContent = showWeeklyAsPercentage ? 'Weekly: by %' : 'Weekly: by time';
        }
    }

    if (toggleInput) {
        updateToggleLabel(); // Set initial state
        toggleInput.addEventListener('change', (event) => {
            showWeeklyAsPercentage = event.target.checked;
            updateToggleLabel();
            if (currentMonthData) {
                renderWeeklySummaries(currentMonthData);
            }
        });
    }

    console.log(`Monthly summary for ${monthKey} rendered.`);
}

function renderWeeklySummaries(data) {
    // Clear previously injected weekly summaries to handle re-renders
    document.querySelectorAll('.strava-zones-weekly-summary-cell').forEach(el => el.remove());

    const weekRowSelector = 'table.month-calendar.marginless tbody tr';
    const weekRows = document.querySelectorAll(weekRowSelector);

    if (!weekRows.length || !data.weeklySummaries) {
        console.warn('ZoneLens: Calendar week rows or weekly summaries not found for rendering.');
        return;
    }

    const orderedSimplifiedZoneKeys = Object.keys(data.zoneDefinitions || {}).sort((a, b) => {
        const numA = parseInt(a.replace('zone', ''), 10);
        const numB = parseInt(b.replace('zone', ''), 10);
        return numB - numA; // descending
    });

    if (orderedSimplifiedZoneKeys.length === 0) {
        console.warn("No zone definitions found for weekly summaries.");
        return;
    }

    let maxSingleZoneTimeInMonth = 0;
    if (!showWeeklyAsPercentage) {
        for (const week of data.weeklySummaries) {
            if (week.zone_times_seconds) {
                for (const time of Object.values(week.zone_times_seconds)) {
                    if (time > maxSingleZoneTimeInMonth) {
                        maxSingleZoneTimeInMonth = time;
                    }
                }
            }
        }
    }

    weekRows.forEach((row, index) => {
        if (data.weeklySummaries[index] && data.weeklySummaries[index].zone_times_seconds) {
            const weeklyActivityZoneTimes = data.weeklySummaries[index].zone_times_seconds;
            const totalWeekSeconds = Object.values(weeklyActivityZoneTimes).reduce((sum, time) => sum + time, 0);

            if (totalWeekSeconds > 0) {
                let summaryHtml = '';
                for (const simplifiedZoneKey of orderedSimplifiedZoneKeys) {
                    const zoneNumberStr = simplifiedZoneKey.replace('zone', '');
                    const zoneName = data.zoneDefinitions[simplifiedZoneKey] || `Zone ${zoneNumberStr}`;
                    const timeInZone = weeklyActivityZoneTimes[zoneName] || 0;

                    const percentage = totalWeekSeconds > 0 ? (timeInZone / totalWeekSeconds) * 100 : 0;
                    const timeFormatted = formatSecondsToHms(timeInZone);

                    let barWidthPercentage;
                    if (showWeeklyAsPercentage) {
                        barWidthPercentage = percentage;
                    } else {
                        barWidthPercentage = maxSingleZoneTimeInMonth > 0 ? (timeInZone / maxSingleZoneTimeInMonth) * 100 : 0;
                    }

                    summaryHtml += `
                        <div class="weekly-zone-row" title="${zoneName}: ${timeFormatted}">
                            <div class="weekly-zone-label">Z${zoneNumberStr}</div>
                            <div class="weekly-zone-bar-container">
                                <div class="weekly-zone-bar zone${zoneNumberStr}" style="width: ${barWidthPercentage.toFixed(1)}%;"></div>
                            </div>
                            <div class="weekly-zone-time-text">${timeFormatted} (${percentage.toFixed(0)}%)</div>
                        </div>
                    `;
                }

                if (summaryHtml) {
                    const panelContainer = document.createElement('div');
                    panelContainer.className = WEEKLY_SUMMARY_CLASS;
                    panelContainer.innerHTML = summaryHtml;

                    const summaryCell = document.createElement('td');
                    summaryCell.className = 'strava-zones-weekly-summary-cell';
                    summaryCell.style.verticalAlign = 'top';
                    summaryCell.style.padding = '2px';

                    summaryCell.appendChild(panelContainer);
                    row.appendChild(summaryCell);
                }
            }
        }
    });
}

function clearAllInjectedUI() {
    const monthlyElement = document.getElementById(MONTHLY_SUMMARY_ID);
    if (monthlyElement) {
        monthlyElement.remove();
    }

    const weeklyElements = document.querySelectorAll('.' + WEEKLY_SUMMARY_CLASS);
    weeklyElements.forEach(el => el.remove());
}

async function injectSyncStatus() {
    const syncStatusUrl = `${API_BASE_URL}/profile/sync_status`;
    try {
        const response = await fetch(syncStatusUrl, {
            method: 'GET',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
        });

        console.log(response)

        if (response.ok) {
            const data = await response.json();
            if (data.sync_status) {
                const { num_processed, total_activities } = data.sync_status;
                const statusDiv = document.createElement('div');
                statusDiv.id = 'zonelens-sync-status';

                statusDiv.style.textAlign = 'center';
                statusDiv.style.fontSize = '14px';
                statusDiv.style.marginBottom = '15px';
                statusDiv.style.padding = '10px';
                statusDiv.style.border = '1px solid #bde5f8';
                statusDiv.style.backgroundColor = '#f0f8ff';
                statusDiv.style.color = '#00529b';
                statusDiv.style.borderRadius = '5px';
                statusDiv.style.maxWidth = '800px';
                statusDiv.style.margin = '0 auto 15px auto';

                let statusHtml = `Initial activity sync: <strong>${num_processed}/${total_activities}</strong> activities synced.`;
                console.log(num_processed)
                console.log(total_activities)
                if (num_processed < total_activities) {
                    statusHtml += `<br><span style="font-size:0.9em;">(Processing may take some time to complete. Data in calendar may be incomplete.)</span>`;
                }
                statusDiv.innerHTML = statusHtml;

                const calendarHeader = document.querySelector('#calendar-header');
                if (calendarHeader) {
                    if (!document.getElementById(statusDiv.id)) {
                        calendarHeader.parentNode.insertBefore(statusDiv, calendarHeader);
                    }
                }
            }
        }
    } catch (error) {
        console.error('ZoneLens: Failed to fetch profile for sync status:', error);
    }
}

async function initHrZoneDisplay() {
    const currentPath = window.location.pathname; // e.g., /athlete/calendar/2024
    const currentHash = window.location.hash;    // e.g., "#Jul"

    // Extract month key from hash (e.g., "Jul" from "#Jul" or "#Jul-2024")
    const monthKey = currentHash.startsWith('#') ? currentHash.substring(1, 4) : null;

    // Simple check for 3-letter month abbreviations
    if (monthKey && /^[A-Za-z]{3}$/.test(monthKey)) {
        clearAllInjectedUI();

        let yearToUse = null;

        // 1. Try to extract year from pathname
        const pathParts = currentPath.split('/');
        // Expected: /athlete/calendar/YYYY or /athletes/NNNNN/calendar/YYYY
        const calendarKeywordIndex = pathParts.indexOf('calendar');
        if (calendarKeywordIndex !== -1 && pathParts.length > calendarKeywordIndex + 1) {
            const yearStr = pathParts[calendarKeywordIndex + 1];
            if (/^\d{4}$/.test(yearStr)) {
                yearToUse = parseInt(yearStr, 10);
            }
        }

        // 2. If not in path, try from DOM element
        if (!yearToUse) {
            const yearElement = document.querySelector('.selected-month .year');
            if (yearElement && yearElement.textContent) {
                const parsedYear = parseInt(yearElement.textContent.trim(), 10);
                if (!isNaN(parsedYear)) {
                    yearToUse = parsedYear;
                }
            }
        }

        // 3. If still no year, default to current year
        if (!yearToUse) {
            yearToUse = new Date().getFullYear();
        }

        fetchActivityData(yearToUse, monthKey).then(data => {
            // Check if monthly_summary and its zone_times_seconds exist and are not empty
            if (data && data.monthly_summary && data.monthly_summary.zone_times_seconds && Object.keys(data.monthly_summary.zone_times_seconds).length > 0) { // Check if data is valid
                const displayData = {
                    displayName: monthMap[monthKey] || monthKey,
                    year: data.year || yearToUse,
                    totalSummary: data.monthly_summary.zone_times_seconds,
                    zoneDefinitions: data.zone_definitions || {},
                    weeklySummaries: data.weekly_summaries || []
                };
                currentMonthData = displayData;
                renderMonthlySummary(monthKey, displayData);
                renderWeeklySummaries(displayData);
            } else {
                console.warn(`No valid data received for ${monthMap[monthKey] || monthKey}, ${yearToUse} or error in fetching. Monthly summary was:`, data ? data.monthly_summary : 'no data');
                // Display a message indicating no data or an error
                const summaryContainer = document.getElementById(MONTHLY_SUMMARY_ID) || document.createElement('div');
                if (!summaryContainer.id) {
                    summaryContainer.id = MONTHLY_SUMMARY_ID;
                    document.body.prepend(summaryContainer);
                }
                summaryContainer.innerHTML = `<div class="strava-zones-header"><h3>Data for ${monthMap[monthKey] || monthKey} ${yearToUse} not available.</h3></div>`;
            }
        });

    } else {
        clearAllInjectedUI();
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    if (window.location.pathname.includes('/athlete/calendar')) {
        console.log("ZoneLens: Calendar page detected. Initializing.");
        injectSyncStatus();
        initHrZoneDisplay();

        // Re-run for month changes
        window.addEventListener('hashchange', initHrZoneDisplay);
        window.addEventListener('popstate', initHrZoneDisplay);
    }
}
