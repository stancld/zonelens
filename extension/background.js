const PRODUCTION_DOMAIN = 'https://strava-zones.com';
const DEVELOPMENT_DOMAIN = 'https://localhost:8000';
const IS_PRODUCTION_BUILD = true;

chrome.runtime.onInstalled.addListener(function(details) {
    if (details.reason === 'update') {
        console.log(`Zonelens updated to ${chrome.runtime.getManifest().version}.`);
        chrome.tabs.create({ url: `${IS_PRODUCTION_BUILD ? PRODUCTION_DOMAIN : DEVELOPMENT_DOMAIN}/api/changelog/` });
    }
});
