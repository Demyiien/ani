import fs from 'node:fs/promises';
const raw=JSON.parse(await fs.readFile('scripts/boundaries-raw.json','utf8'));
function inside(point,ring){let yes=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){const a=ring[i],b=ring[j];if((a[1]>point[1])!==(b[1]>point[1])&&point[0]<(b[0]-a[0])*(point[1]-a[1])/(b[1]-a[1])+a[0])yes=!yes;}return yes;}
function inPolygon(p,poly){return inside(p,poly[0])&&!poly.slice(1).some(r=>inside(p,r));}
const provinceNames=Object.fromEntries(raw.provinces.map(f=>[f.properties.adm2_psgc,f.properties.adm2_en]));
const regionNames=Object.fromEntries(raw.regions.features.map(f=>[f.properties.adm1_psgc,f.properties.adm1_en]));
const features=raw.cities.map(f=>{
 const polygons=f.geometry.type==='Polygon'?[f.geometry.coordinates]:f.geometry.coordinates;
 const positions=polygons.flat(2);
 const bounds=[Math.min(...positions.map(p=>p[0])),Math.min(...positions.map(p=>p[1])),Math.max(...positions.map(p=>p[0])),Math.max(...positions.map(p=>p[1]))];
 const largest=polygons.reduce((a,b)=>{const area=r=>Math.abs(r.reduce((s,p,i)=>{const q=r[(i+1)%r.length];return s+p[0]*q[1]-q[0]*p[1]},0));return area(a[0])>area(b[0])?a:b;});
 const ring=largest[0];const xs=ring.map(p=>p[0]),ys=ring.map(p=>p[1]);
 const west=Math.min(...xs),east=Math.max(...xs),south=Math.min(...ys),north=Math.max(...ys);
 let point=[(west+east)/2,(south+north)/2];
 if(!inPolygon(point,largest)){let found=false;for(let y=1;y<30&&!found;y++)for(let x=1;x<30&&!found;x++){const p=[west+(east-west)*x/30,south+(north-south)*y/30];if(inPolygon(p,largest)){point=p;found=true;}}if(!found)point=ring[0];}
 const p=f.properties;
 return {type:'Feature',id:String(p.adm3_psgc),bbox:bounds,properties:{id:String(p.adm3_psgc),name:p.adm3_en,province:provinceNames[p.adm2_psgc]||'Philippines',region:regionNames[p.adm1_psgc]||'Philippines',lat:+point[1].toFixed(6),lon:+point[0].toFixed(6)},geometry:f.geometry};
});
await fs.writeFile('public/philippines-municipalities.json',JSON.stringify({type:'FeatureCollection',features}));
await fs.writeFile('public/boundaries-attribution.json',JSON.stringify({source:'https://github.com/faeldon/philippines-json-maps',commit:raw.sha,vintage:'PSGC 3Q 2026',resolution:'medium (1% retained vertices)',count:features.length,attribution:'Boundaries: NAMRIA / PSA via OCHA COD-AB (CC BY-IGO 3.0), processed by James Faeldon (MIT). Simplified boundaries for visualization, not surveying.'},null,2));
console.log('Generated',features.length,'municipalities;', (await fs.stat('public/philippines-municipalities.json')).size,'bytes');
