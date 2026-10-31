# Map asset provenance

`world.json` is a compact, derived copy of Natural Earth vector data retrieved on 31 October 2026:

- [110m country boundaries](https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_admin_0_countries.geojson)
- [50m populated places](https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_50m_populated_places_simple.geojson)

The asset retains outer boundary rings and a small gazetteer of place names, administrative regions, country labels, and representative coordinates. It contains no job data. Country points use Natural Earth's label coordinates, not an employer office or a mathematically computed centroid. City points approximate a place, not a posting's exact address. Administrative boundaries follow the source dataset and are for orientation only.

Natural Earth map data is [public domain](https://www.naturalearthdata.com/about/terms-of-use/). The globe runs locally without tile requests, runtime geocoding calls, or external JavaScript assets. The original Orbit SVG mark is authored for this project.
