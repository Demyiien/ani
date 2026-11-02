import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';
import { readFile, mkdir } from 'node:fs/promises';

const areas = JSON.parse(await readFile('public/philippines-municipalities.json', 'utf8')).features;
assert.equal(areas.length, 1642);
await mkdir('test-results', { recursive: true });

const baseUrl = process.env.VERIFY_URL || 'http://127.0.0.1:5173';
const browser = await chromium.launch({ channel: 'msedge', headless: true });

// 1. Verify initial landing selects the user's live geolocation (e.g., Cebu City: 10.3157° N, 123.8854° E)
{
  const geoCtx = await browser.newContext({
    viewport: { width: 1440, height: 860 },
    permissions: ['geolocation'],
    geolocation: { latitude: 10.3157, longitude: 123.8854 },
  });
  const geoPage = await geoCtx.newPage();
  await geoPage.goto(baseUrl, { waitUntil: 'networkidle' });
  await expect(geoPage.locator('.details-panel h2')).toHaveText('City of Cebu', { timeout: 10000 });
  assert.match(await geoPage.locator('.region-tag').innerText(), /Cebu/);
  await geoCtx.close();
}

const page = await browser.newPage({ viewport: { width: 1536, height: 740 } });
const errors = [];
page.on('pageerror', e => errors.push(e.message));

await page.goto(baseUrl, { waitUntil: 'networkidle' });
await page.getByLabel('Data view').selectOption('stored');
await page.waitForFunction(() => document.querySelector('.measurement-value')?.textContent?.includes('…') === false);
assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
assert.equal(await page.getByLabel('Data view').inputValue(), 'stored');
await page.locator('.map-canvas[data-rendered="true"]').waitFor({ timeout: 30000 });

// 2. Verify custom logo (/logo.jpg) in browser tab icon and .brand-mark
assert.equal(await page.locator('link[rel="icon"]').getAttribute('href'), '/logo.jpg');
assert.equal(await page.locator('.brand-mark img').getAttribute('src'), '/logo.jpg');
assert.equal(await page.locator('.brand-mark img').evaluate(img => /** @type {HTMLImageElement} */ (img).naturalWidth > 0), true);

// 3. Verify full-screen edge-to-edge map, dark blue sea, balanced top bar search width, and non-overlapping zoom controls
const mapBox740 = await page.locator('.map-wrap').boundingBox();
assert.ok(mapBox740 && mapBox740.x === 0 && mapBox740.y === 0 && mapBox740.width === 1536 && mapBox740.height === 740);
assert.equal(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight && document.documentElement.scrollWidth <= innerWidth), true);
assert.equal(await page.locator('.map-wrap').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(198, 215, 213)');

const collectionBox = await page.locator('.collection-bar').boundingBox();
const searchBox = await page.locator('.search').boundingBox();
assert.ok(collectionBox && searchBox);
assert.ok(searchBox.x - (collectionBox.x + collectionBox.width) <= 16, 'No wide empty gap between collection bar and search');
assert.ok(searchBox.width >= 260, `Expected widened search bar, got ${searchBox.width}px`);

const zoomBox = await page.locator('.map-tools').boundingBox();
const attribBox = await page.locator('.maplibregl-ctrl-attrib').boundingBox();
assert.ok(zoomBox && attribBox);
assert.ok(zoomBox.y + zoomBox.height < attribBox.y, `Zoom capsule (${zoomBox.y + zoomBox.height}) must sit above attribution (${attribBox.y})`);

// Layer opacity control is removed and fill is locked at 90% static across all 1,642 areas
assert.equal(await page.locator('#overlay-opacity').count(), 0);
assert.match(await page.locator('.layer-source').innerText(), /90% fill/i);
assert.match(await page.locator('.map-demo-label').innerText(), /1,642 areas/);
await page.screenshot({ path: 'test-results/desktop-compact-viewport.png', fullPage: false });

// 4. Verify non-land ocean click animates above the date slider, supports manual X dismiss, and auto-fades after ~3s
await page.mouse.click(420, 320); // West Philippine Sea (non-land water pixel)
await page.locator('.coverage-notice').waitFor({ state: 'visible' });
await page.waitForTimeout(350); // Allow 280ms slide-up entry transition from the date slider to settle
const noticeBox = await page.locator('.coverage-notice').boundingBox();
const timelineBox = await page.locator('.timeline').boundingBox();
assert.ok(noticeBox && timelineBox && noticeBox.y + noticeBox.height <= timelineBox.y, `Notice bottom (${noticeBox?.y + noticeBox?.height}) should rest above timeline (${timelineBox?.y})`);
await page.getByRole('button', { name: 'Dismiss coverage notice' }).click();
await page.locator('.coverage-notice').waitFor({ state: 'hidden', timeout: 2000 });

// Click non-land again and verify automatic fade-out after ~3 seconds
await page.mouse.click(420, 320);
await page.locator('.coverage-notice').waitFor({ state: 'visible' });
await page.locator('.coverage-notice').waitFor({ state: 'hidden', timeout: 4500 });

// Also check standard 1440x860 desktop viewport
await page.setViewportSize({ width: 1440, height: 860 });
await page.waitForTimeout(200);
const mapBox860 = await page.locator('.map-wrap').boundingBox();
assert.ok(mapBox860 && mapBox860.width === 1440 && mapBox860.height === 860);
assert.equal(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight), true);

// Verify dates updated through latest day (Nov 1–2, 2026) on Cabanatuan
assert.match(await page.locator('.timeline-heading').innerText(), /Nov [12], 2026/);
await page.screenshot({ path: 'test-results/nationwide-collected.png', fullPage: false });

// 5. Verify smoothed Himawari-9 satellite cloud raster over dark blue sea on Nov 1, 2026
await page.getByRole('button', { name: 'Cloud cover Satellite view' }).click();
await expect(page.locator('.layer-source')).toContainText('Himawari-9');
await page.locator('.map-canvas[data-rendered="true"]').waitFor({ timeout: 30000 });
assert.match(await page.locator('.map-demo-label').innerText(), /1,642 areas/);
await page.screenshot({ path: 'test-results/nationwide-himawari-raster.png', fullPage: false });

await page.getByRole('button', { name: 'Evapotranspiration Crop water demand' }).click();
await expect(page.locator('.measurement-label')).toContainText('Evapotranspiration');
assert.doesNotMatch(await page.locator('.measurement-value').innerText(), /—/);
assert.match(await page.locator('.map-demo-label').innerText(), /1,642 areas/);

// 6. Verify smart search suggestions ("cebu city" -> #1 suggestion is Cebu City / City of Cebu)
await page.getByRole('button', { name: 'Rainfall Rainfall amount' }).click();
await page.getByLabel('Find a place in the Philippines').fill('cebu city');
const firstSuggestion = await page.locator('.search-results button').first().innerText();
assert.match(firstSuggestion, /Cebu City/);
assert.match(firstSuggestion, /City of Cebu/);
await page.locator('.search-results button').first().click();
assert.equal(await page.locator('.details-panel h2').innerText(), 'City of Cebu');

for (const name of ['City of Davao', 'City of Puerto Princesa', 'Basco', 'City of Manila', 'Talavera']) {
  const place = areas.find(f => f.properties.name === name)?.properties;
  assert.ok(place, name);
  const query = name.replace('City of ', '');
  await page.getByLabel('Find a place in the Philippines').fill(query);
  await page.locator('.search-results button').filter({ hasText: place.name }).first().click();
  assert.equal(await page.locator('.details-panel h2').innerText(), name);
  assert.match(await page.locator('.region-tag').innerText(), new RegExp(place.province.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  await page.waitForLoadState('networkidle');
  assert.doesNotMatch(await page.locator('.measurement-value').innerText(), /—/);
}

// Scrubbing to Oct 31 on Talavera shows real GPM IMERG satellite rainfall + georeferenced raster
await page.getByRole('button', { name: 'Show Oct 31, 2026' }).click();
await page.waitForLoadState('networkidle');
assert.doesNotMatch(await page.locator('.measurement-value').innerText(), /—/);
assert.match(await page.locator('.layer-source').innerText(), /IMERG/);

// Toggle between Municipal fill and Raster grid, and test collapsible floating HUD docks
await page.getByRole('button', { name: 'Municipal fill' }).click();
await page.getByRole('button', { name: 'Raster grid' }).click();
await page.getByRole('button', { name: 'Close details' }).click();
assert.equal(await page.locator('.details-panel').isVisible(), false);
await page.getByRole('button', { name: 'Location details' }).click();
assert.equal(await page.locator('.details-panel').isVisible(), true);

// 7. Test floating PAGASA advisories sheet: single-row chips & descending issued_at order (latest on top)
await page.getByRole('tab', { name: /PAGASA advisories/ }).click();
await page.locator('section[aria-label="Official PAGASA and DOST advisories"]').waitFor();
assert.ok((await page.locator('tbody tr').count()) >= 5);
const chipHeights = await page.locator('.sources-page .status-pill').evaluateAll(els => els.map(el => el.getBoundingClientRect().height));
assert.ok(chipHeights.every(h => h > 0 && h <= 26), `Expected single-row status chips, got heights: ${chipHeights.join(', ')}`);
const issuedCells = await page.locator('tbody tr td.mono-dates').allInnerTexts();
const sortedIssued = [...issuedCells].sort((a, b) => b.localeCompare(a));
assert.deepEqual(issuedCells, sortedIssued, 'PAGASA advisories must be ordered with latest issued date at the top');
await page.screenshot({ path: 'test-results/nationwide-advisories.png', fullPage: false });
await page.getByRole('button', { name: 'Show advisory zones on map' }).click();
assert.match(await page.locator('.map-legend').innerText(), /PAGASA outlook/);

// Test floating Crop calendar & PSA farm statistics sheet & provincial choropleth
await page.getByRole('tab', { name: /Crops & stats/ }).click();
await page.locator('section[aria-label="Crop calendars and farm statistics"]').waitFor();
assert.match(await page.locator('.crop-province-hero').innerText(), /Nueva Ecija/);
assert.match(await page.locator('.crop-province-hero').innerText(), /MT/);
await page.screenshot({ path: 'test-results/nationwide-crops.png', fullPage: false });
await page.getByRole('button', { name: 'View provincial choropleth' }).click();
assert.match(await page.locator('.map-legend').innerText(), /Provincial palay/);
await page.getByRole('button', { name: 'Satellite', exact: true }).click();

// Simulated demo separation check
await page.getByLabel('Data view').selectOption('demo');
assert.doesNotMatch(await page.locator('.measurement-value').innerText(), /—/);
await page.getByRole('button', { name: 'Vegetation health Crop greenness' }).click();
await page.getByRole('button', { name: 'Show Oct 29, 2026' }).click();
assert.match(await page.locator('.timeline-heading').innerText(), /Oct 29/);
await page.getByRole('button', { name: 'Show on map' }).click();
assert.match(await page.locator('.map-legend').innerText(), /Overlay hidden/);
await page.getByRole('button', { name: 'Show on map' }).click();
await page.getByRole('button', { name: 'Show all Philippines' }).click();

// 8. Dark mode check: slate-petrol sea + white attribution text
await page.getByRole('button', { name: 'Switch to dark mode' }).click();
await page.waitForLoadState('networkidle');
assert.equal(await page.locator('body').evaluate(el => getComputedStyle(el).color), 'rgb(229, 239, 235)');
assert.equal(await page.locator('.map-wrap').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(11, 27, 34)');
assert.equal(await page.locator('.maplibregl-ctrl-attrib').evaluate(el => getComputedStyle(el).color), 'rgb(255, 255, 255)');
assert.equal(await page.locator('.maplibregl-ctrl-attrib a').first().evaluate(el => getComputedStyle(el).color), 'rgb(255, 255, 255)');
await page.locator('.map-canvas[data-rendered="true"]').waitFor({ timeout: 30000 });
await page.screenshot({ path: 'test-results/nationwide-dark.png', fullPage: false });

// Complete Phase 1 Data Inventory check (6 map layers + 11 upstream feeds, zero Missing pills!)
await page.getByRole('tab', { name: /Data sources/ }).click();
assert.equal(await page.locator('.sources-page table').first().locator('tbody tr').count(), 6);
await page.getByLabel('Data view').selectOption('stored');
await page.getByRole('button', { name: 'Collect nationwide weather' }).waitFor();
await page.waitForLoadState('networkidle');
assert.equal(await page.locator('.sources-page table').nth(1).locator('tbody tr').count(), 11);
const pillTexts = await page.locator('.sources-page .status-pill').allInnerTexts();
assert.equal(pillTexts.filter(t => t.trim() === 'Missing').length, 0, `Expected zero Missing status pills, got: ${pillTexts.join(', ')}`);
assert.ok(pillTexts.some(t => t.trim() === 'Available'));
await page.getByRole('button', { name: 'Switch to light mode' }).click();
await page.waitForTimeout(200);
await page.screenshot({ path: 'test-results/nationwide-sources.png', fullPage: false });

// 9. Collection toast auto-dismiss (~4s) and manual X close verification
await page.getByRole('tab', { name: 'Explore map' }).click();
let mockedDone = false;
await page.route('**/api/jobs', async r => {
  if (r.request().method() === 'POST') {
    mockedDone = true;
    await r.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'mock-job-1', status: 'complete', completed: 3, total: 3, failed: 0, rows: 42, errors: [] }),
    });
  } else {
    await r.continue();
  }
});
await page.getByRole('button', { name: /Collect Talavera/ }).click();
assert.equal(mockedDone, true);
await page.locator('.collection-toast').waitFor({ state: 'visible' });
assert.equal(await page.getByRole('button', { name: 'Dismiss collection notification' }).isVisible(), true);
// Wait for automatic fade-out after ~4 seconds
await page.locator('.collection-toast').waitFor({ state: 'hidden', timeout: 6000 });
await page.unroute('**/api/jobs');

// Mobile responsive verification (390x844) across Data sources, Crops, and Explore map
await page.setViewportSize({ width: 390, height: 844 });
assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
await page.getByRole('tab', { name: /Crops & stats/ }).click();
assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);

await page.getByRole('tab', { name: 'Explore map' }).click();
await page.getByRole('button', { name: 'Map layers', exact: true }).click();
await page.getByRole('button', { name: 'Soil moisture Water in the soil' }).click();
await page.getByRole('button', { name: 'Close layers' }).click();
await page.waitForLoadState('networkidle');
assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
await page.locator('.map-canvas[data-rendered="true"]').waitFor({ timeout: 30000 });
await page.screenshot({ path: 'test-results/nationwide-mobile.png', fullPage: false });

// Loss of backend clears values instead of silently switching to synthetic data, and X manually closes the alert toast
await page.route('**/api/point?**', r => r.fulfill({ status: 503, body: 'Unavailable' }));
await page.getByRole('button', { name: 'Refresh collected data' }).click();
await page.getByRole('alert').filter({ hasText: 'Backend unavailable' }).waitFor();
await page.getByRole('button', { name: 'Location details' }).click();
await expect(page.locator('.measurement-value')).toContainText('—');
await page.getByRole('button', { name: 'Close details' }).click();
await page.getByRole('button', { name: 'Dismiss collection notification' }).click();
await page.locator('.collection-toast').waitFor({ state: 'hidden', timeout: 2000 });

assert.deepEqual(errors, []);
console.log(
  'PASS: Geolocation landing, custom logo.jpg, theme-matched celadon/petrol sea, balanced search bar ("cebu city" -> #1), non-overlapping zoom + white dark-mode attribution, 3s animated non-land popup + X, auto-dismissing collection toast + X, single-row PAGASA chips sorted latest-first, smooth Himawari-9 clouds, zero page errors.',
);
await browser.close();
