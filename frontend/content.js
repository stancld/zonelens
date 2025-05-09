// Content script for Strava HR Zones Display
console.log("Strava HR Zones Display content script v2 loaded.");

const MONTHLY_SUMMARY_ID = 'hr-zones-monthly-summary-container';
const WEEKLY_SUMMARY_CLASS = 'injected-weekly-hr-summary'; // Class for easy removal

// Placeholder for User ID - replace with actual user ID retrieval logic
// const USER_ID = 1; // No longer sending hardcoded user_id for this view
const API_BASE_URL = 'https://localhost:8000/api';

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
    const monthFullName = monthMap[monthShortName] || monthShortName; // Use full name if mapped
    const monthNum = monthNumberMap[monthShortName];

    if (!monthNum) {
        console.error(`Invalid month short name: ${monthShortName}`);
        return null;
    }

    // Adjust API endpoint as per your backend design
    // Now targets /api/zones/ and expects year & month as query params.
    // User identification is expected to be handled by the backend via session.
    const apiUrl = `${API_BASE_URL}/zones/?year=${year}&month=${monthNum}`;
    console.log(`Fetching data from: ${apiUrl}`);

    try {
        const response = await fetch(apiUrl, {
            method: 'GET',
            credentials: 'include', // Send cookies (like session cookies) with the request
            headers: {
                'Content-Type': 'application/json',
                // Add any other headers your backend might expect, like X-CSRFToken if needed for non-GET
            }
        });
        if (!response.ok) {
            const errorText = await response.text();
            console.error(`Error fetching data: ${response.status} ${response.statusText}`, errorText);
            throw new Error(`HTTP error ${response.status}: ${errorText}`);
        }
        const data = await response.json();
        console.log("Data fetched successfully:", data);
        // Ensure data has displayName, zoneDefinitions, totalSummary, weeklySummaries
        // The backend should ideally provide 'displayName' and 'zoneDefinitions'
        // If not, we might need to reconstruct them or make them static here.
        // For now, assuming backend provides: year, displayName, totalSummary, weeklySummaries, zoneDefinitions
        return data;
    } catch (error) {
        console.error('Failed to fetch activity data:', error);
        return null; // Or throw error to be caught by caller for UI update
    }
}

function formatSecondsToHms(totalSeconds) {
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    // const seconds = totalSeconds % 60;
    // return `${hours}h ${minutes}m ${seconds}s`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
}

function findMatchingZoneKey(simplifiedZone, zoneTimesObject) {
  // simplifiedZone is like 'zone5', zoneTimesObject is like {'Z5 Anaerobic': 123, ...}
  // This regex looks for "Z<number>" at the start of the key,
  // ensuring it's not followed by another digit (e.g., Z1 should not match Z10).
  const prefixPattern = new RegExp("^" + simplifiedZone.replace('zone', 'Z') + "($|[^0-9])", "i");
  for (const key in zoneTimesObject) {
    if (prefixPattern.test(key)) {
      return key;
    }
  }
  console.warn(`No matching key found for simplified zone '${simplifiedZone}' in`, zoneTimesObject);
  return null;
}

function renderMonthlySummary(monthKey, data) {
  let summaryContainer = document.getElementById(MONTHLY_SUMMARY_ID);
  if (!summaryContainer) {
    summaryContainer = document.createElement('div');
    summaryContainer.id = MONTHLY_SUMMARY_ID;
    document.body.prepend(summaryContainer); // Prepend to body for now
  }

  const totalMonthSeconds = Object.values(data.totalSummary).reduce((sum, time) => sum + time, 0);
  if (totalMonthSeconds === 0) {
    summaryContainer.innerHTML = `<div class="strava-zones-header"><h3>No HR Data for ${data.displayName}</h3></div>`;
    return;
  }

  let contentHtml = `<div class="strava-zones-header"><h3>${data.displayName} HR Zone Summary</h3></div>`;
  contentHtml += '<div class="strava-zones-content">';

  const zonesOrder = ['zone5', 'zone4', 'zone3', 'zone2', 'zone1']; // Display order

  for (const zone of zonesOrder) {
    const actualZoneKey = findMatchingZoneKey(zone, data.totalSummary);
    const timeSeconds = actualZoneKey ? data.totalSummary[actualZoneKey] : 0;
    const percentage = totalMonthSeconds > 0 ? (timeSeconds / totalMonthSeconds) * 100 : 0;
    const timeFormatted = formatSecondsToHms(timeSeconds);

    contentHtml += `
      <div class="zone-row">
        <span class="zone-label">Zone ${zone.slice(-1)}: ${data.zoneDefinitions[zone]}</span>
        <div class="zone-bar-container">
          <div class="zone-bar ${zone}" style="width: ${percentage.toFixed(1)}%;"></div>
          <span class="zone-time-text">${timeFormatted} (${percentage.toFixed(0)}%)</span>
        </div>
      </div>
    `;
  }
  contentHtml += '</div>';
  summaryContainer.innerHTML = contentHtml;
  console.log(`Monthly summary for ${monthKey} rendered.`);
}

function renderWeeklySummaries(monthKey, data) {
  console.log(`Attempting to render weekly summaries for ${monthKey}.`);

  // **IMPORTANT**: This selector needs to be updated based on Strava's actual calendar structure.
  // Use Chrome DevTools to inspect the calendar and find the correct selector for week rows.
  // Examples: 'div.date-grid > div.week', 'table#calendar-table tbody tr',
  // '.rc-MonthWrapper .rc-Week'
  const weekRowSelector = 'table.month-calendar.marginless tbody tr';
  const weekRows = document.querySelectorAll(weekRowSelector);

  if (!weekRows.length) {
    console.warn(`No week rows found with selector: '${weekRowSelector}'. Weekly summaries not rendered.`);
    return;
  }

  const zonesOrder = ['zone5', 'zone4', 'zone3', 'zone2', 'zone1'];

  weekRows.forEach((row, index) => {
    if (data.weeklySummaries && data.weeklySummaries[index] && data.weeklySummaries[index].zone_times_seconds) {
      const weeklyZoneTimes = data.weeklySummaries[index].zone_times_seconds; // Get the nested object
      const totalWeekSeconds = zonesOrder.reduce((sum, zoneKey) => {
        const actualZoneKey = findMatchingZoneKey(zoneKey, weeklyZoneTimes);
        return sum + (actualZoneKey ? weeklyZoneTimes[actualZoneKey] : 0);
      }, 0);

      if (totalWeekSeconds > 0) {
        const panelContainer = document.createElement('div');
        panelContainer.className = WEEKLY_SUMMARY_CLASS;

        let panelHtml = '';
        for (const zoneKey of zonesOrder) {
          const actualZoneKey = findMatchingZoneKey(zoneKey, weeklyZoneTimes);
          const timeSeconds = actualZoneKey ? weeklyZoneTimes[actualZoneKey] : 0;
          const percentage = totalWeekSeconds > 0 ? (timeSeconds / totalWeekSeconds) * 100 : 0;
          const timeFormatted = formatSecondsToHms(timeSeconds);

          panelHtml += `
            <div class="weekly-zone-row">
              <span class="weekly-zone-label">Z${zoneKey.slice(-1)}</span>
              <div class="weekly-zone-bar-container">
                <div class="weekly-zone-bar ${zoneKey}" style="width: ${percentage.toFixed(1)}%;"></div>
              </div>
              <span class="weekly-zone-time-text">${timeFormatted} (${percentage.toFixed(0)}%)</span>
            </div>
          `;
        }
        panelContainer.innerHTML = panelHtml;

        // Create a new table cell (td) to hold our summary panel
        const summaryCell = document.createElement('td');
        summaryCell.className = 'strava-zones-weekly-summary-cell'; // For potential styling
        summaryCell.style.verticalAlign = 'top'; // Align panel to the top of the cell
        summaryCell.style.padding = '2px'; // Add a little padding

        summaryCell.appendChild(panelContainer);
        row.appendChild(summaryCell); // Add the new cell to the row (tr)
      }
    } else {
      // console.log(`No weekly data for week index ${index} or week row not found`);
    }
  });
  console.log(`Processed ${weekRows.length} potential week rows for weekly summaries.`);
}

function clearSummary() {
  const monthlyElement = document.getElementById(MONTHLY_SUMMARY_ID);
  if (monthlyElement) {
    monthlyElement.remove();
    console.log("Monthly summary element removed.");
  }

  const weeklyElements = document.querySelectorAll('.' + WEEKLY_SUMMARY_CLASS);
  weeklyElements.forEach(el => el.remove());
  if (weeklyElements.length > 0) {
    console.log(`${weeklyElements.length} weekly summary elements removed.`);
  }
}

function initHrZoneDisplay() {
  const currentHash = window.location.hash; // e.g., "#Feb"
  const monthKey = currentHash.startsWith('#') ? currentHash.substring(1) : null;

  // Simple check for 3-letter month abbreviations
  if (monthKey && /^[A-Za-z]{3}$/.test(monthKey)) {
    console.log(`Attempting to display HR Zones for month: ${monthKey}`);
    clearSummary(); // Clear previous summaries

    // Determine year (e.g., from Strava page, or assume current year)
    // For now, let's try to get it from a Strava element, otherwise default to current year.
    let year = new Date().getFullYear();
    const yearElement = document.querySelector('.selected-month .year'); // Example selector, might need adjustment
    if (yearElement && yearElement.textContent) {
        const parsedYear = parseInt(yearElement.textContent.trim(), 10);
        if (!isNaN(parsedYear)) {
            year = parsedYear;
        }
    }
    console.log(`Using year: ${year} for month: ${monthKey}`);

    fetchActivityData(year, monthKey).then(data => {
      // Check if monthly_summary and its zone_times_seconds exist and are not empty
      if (data && data.monthly_summary && data.monthly_summary.zone_times_seconds && Object.keys(data.monthly_summary.zone_times_seconds).length > 0) { // Check if data is valid
        // Ensure data.displayName and data.zoneDefinitions are present, or have fallbacks
        const displayData = {
            displayName: monthMap[monthKey] || monthKey, // Use monthMap, as data.displayName is not from API
            year: data.year || year,
            totalSummary: data.monthly_summary.zone_times_seconds, // Correct path to zone times
            // Use fetched zoneDefinitions, or a static fallback if not provided
            zoneDefinitions: { // data.zoneDefinitions is not from API, using fallback
                zone5: "Anaerobic", zone4: "Threshold", zone3: "Tempo", zone2: "Endurance (Easy)", zone1: "Warm up (Easy)"
            },
            weeklySummaries: data.weekly_summaries || [] // Correct key for weekly summaries
        };
        renderMonthlySummary(monthKey, displayData);
        renderWeeklySummaries(monthKey, displayData);
      } else {
        console.warn(`No valid data received for ${monthMap[monthKey] || monthKey}, ${year} or error in fetching. Monthly summary was:`, data ? data.monthly_summary : 'no data');
        // Display a message indicating no data or an error
        const summaryContainer = document.getElementById(MONTHLY_SUMMARY_ID) || document.createElement('div');
        if (!summaryContainer.id) {
            summaryContainer.id = MONTHLY_SUMMARY_ID;
            document.body.prepend(summaryContainer);
        }
        summaryContainer.innerHTML = `<div class="strava-zones-header"><h3>Data for ${monthMap[monthKey] || monthKey} ${year} not available.</h3></div>`;
      }
    });

  } else {
    console.log("Not on a specific month view, clearing summary.", monthKey);
    clearSummary();
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

// More robust SPA navigation detection might be needed if hashchange/popstate are not enough.
// Consider MutationObserver on a stable part of the page or title changes.

console.log("Strava HR Zones Display event listeners attached.");
