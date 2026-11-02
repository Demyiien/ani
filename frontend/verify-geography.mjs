import fs from 'node:fs';
import ts from 'typescript';
import assert from 'node:assert/strict';

const code = ts.transpile(fs.readFileSync('src/geography.ts', 'utf8'), {
  module: ts.ModuleKind.ESNext,
  target: ts.ScriptTarget.ES2022,
});
const { findArea, resolveUserPlace, friendlyPlaceName, searchPlaces } = await import(
  'data:text/javascript;base64,' + Buffer.from(code).toString('base64')
);
const areas = JSON.parse(fs.readFileSync('public/philippines-municipalities.json', 'utf8'));
const places = areas.features.map(f => f.properties);

assert.equal(new Set(places.map(p => p.id)).size, 1642);
for (const name of ['City of Cebu', 'City of Davao', 'City of Puerto Princesa', 'Basco', 'City of Manila']) {
  const p = places.find(pl => pl.name === name);
  assert.equal(findArea(areas, p.lon, p.lat)?.properties.id, p.id);
  assert.ok(searchPlaces(places, name).some(r => r.id === p.id));
}

// Conversational "<X> City", bare city names, and shortcuts must rank the target city at #1
assert.equal(searchPlaces(places, 'cebu city')[0].name, 'City of Cebu');
assert.equal(searchPlaces(places, 'cebu')[0].name, 'City of Cebu');
assert.equal(searchPlaces(places, 'davao city')[0].name, 'City of Davao');
assert.equal(searchPlaces(places, 'manila')[0].name, 'City of Manila');
assert.equal(searchPlaces(places, 'cabanatuan city')[0].name, 'City of Cabanatuan');
assert.equal(searchPlaces(places, 'qc')[0].name, 'Quezon City');
assert.equal(searchPlaces(places, 'sta rosa')[0].name, 'City of Santa Rosa');
assert.equal(friendlyPlaceName('City of Cebu'), 'Cebu City');

// User geolocation resolver snaps coastal/land PH coordinates and rejects open ocean / foreign coords
assert.equal(resolveUserPlace(areas, 123.8854, 10.3157)?.name, 'City of Cebu');
assert.equal(findArea(areas, 118, 15), undefined);
assert.equal(resolveUserPlace(areas, 118, 15), undefined);
assert.equal(findArea(areas, 0, 0), undefined);
assert.equal(resolveUserPlace(areas, 0, 0), undefined);

const duplicates = searchPlaces(places, 'San Jose');
assert.ok(new Set(duplicates.map(p => p.province)).size > 1);
console.log('PASS: nationwide lookup, conversational city search ("cebu city" -> City of Cebu #1), geolocation resolver, ocean rejection.');
