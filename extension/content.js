const MONTHLY_SUMMARY_ID = 'hr-zones-monthly-summary-container';
const WEEKLY_SUMMARY_CLASS = 'injected-weekly-hr-summary';

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
            credentials: 'include', // Send cookies (like session cookies) with the request
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
    console.log(`Monthly summary for ${monthKey} rendered.`);
}

function renderWeeklySummaries(data) {
    const weekRowSelector = 'table.month-calendar.marginless tbody tr';
    const weekRows = document.querySelectorAll(weekRowSelector);

    if (!weekRows.length) {
        console.warn(`No week rows found with selector: '${weekRowSelector}'. Weekly summaries not rendered.`);
        return;
    }

    // Dynamically generate orderedSimplifiedZoneKeys from data.zoneDefinitions
    const simplifiedZoneKeys = Object.keys(data.zoneDefinitions || {});
    if (simplifiedZoneKeys.length === 0) {
        console.warn("No zone definitions found in data.zoneDefinitions for weekly summaries. Cannot render.");
        return;
    }

    simplifiedZoneKeys.sort((a, b) => {
        const numA = parseInt(a.replace('zone', ''), 10);
        const numB = parseInt(b.replace('zone', ''), 10);
        return numA - numB;
    });
    const orderedSimplifiedZoneKeys = simplifiedZoneKeys.reverse();

    weekRows.forEach((row, index) => {
        if (data.weeklySummaries && data.weeklySummaries[index] && data.weeklySummaries[index].zone_times_seconds) {
            const weeklyActivityZoneTimes = data.weeklySummaries[index].zone_times_seconds;

            let totalWeekSeconds = 0;
            for (const key of orderedSimplifiedZoneKeys) {
                const actualName = data.zoneDefinitions[key];
                if (actualName && weeklyActivityZoneTimes[actualName]) {
                    totalWeekSeconds += weeklyActivityZoneTimes[actualName];
                }
            }

            if (totalWeekSeconds > 0) {
                const panelContainer = document.createElement('div');
                panelContainer.className = WEEKLY_SUMMARY_CLASS;

                let panelHtml = '';
                for (const simplifiedZoneKey of orderedSimplifiedZoneKeys) {
                    const zoneNumberStr = simplifiedZoneKey.replace('zone', '');

                    const actualUserDefinedName = data.zoneDefinitions[simplifiedZoneKey];
                    const timeSeconds = actualUserDefinedName ? (weeklyActivityZoneTimes[actualUserDefinedName] || 0) : 0;

                    const percentage = totalWeekSeconds > 0 ? (timeSeconds / totalWeekSeconds) * 100 : 0;
                    const timeFormatted = formatSecondsToHms(timeSeconds);

                    panelHtml += `
                      <div class="weekly-zone-row">
                        <span class="weekly-zone-label">Z${zoneNumberStr}</span>
                        <div class="weekly-zone-bar-container">
                          <div class="weekly-zone-bar zone${zoneNumberStr}" style="width: ${percentage.toFixed(1)}%;"></div>
                        </div>
                        <span class="weekly-zone-time-text">${timeFormatted} (${percentage.toFixed(0)}%)</span>
                      </div>
                    `;
                }
                panelContainer.innerHTML = panelHtml;

                // Create a new table cell (td) to hold our summary panel
                const summaryCell = document.createElement('td');
                summaryCell.className = 'strava-zones-weekly-summary-cell';
                summaryCell.style.verticalAlign = 'top';
                summaryCell.style.padding = '2px';

                summaryCell.appendChild(panelContainer);
                row.appendChild(summaryCell); // Add the new cell to the row (tr)
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

function initHrZoneDisplay() {
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

// Initial load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initHrZoneDisplay);
} else {
    initHrZoneDisplay();
}

// Listen for hash changes (SPA navigation between months)
window.addEventListener('hashchange', initHrZoneDisplay);

// Strava might also use popstate for navigation in some cases
window.addEventListener('popstate', initHrZoneDisplay);
