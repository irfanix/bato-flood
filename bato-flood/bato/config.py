"""Settings for a Bato run. Every threshold lives here so it can be audited."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date

# Case study: Bhote Koshi - Trishuli corridor, Nepal (glacier collapse 26 Aug 2026).
# From Rasuwagadhi (border) down to Betrawati / Bidur.
TRISHULI_BBOX = (85.10, 27.88, 85.45, 28.30)  # lon_min, lat_min, lon_max, lat_max
TRISHULI_DATE = "2026-08-26"

# Latest pre-event OpenStreetMap snapshot available on ohsome (per the brief).
OHSOME_LATEST = "2026-07-27"

# Average household size, Nepal Census 2021 (used only for a labelled estimate).
HOUSEHOLD_SIZE = 4.37


@dataclass
class Settings:
    bbox: tuple[float, float, float, float]
    event_date: str                      # YYYY-MM-DD
    resolution_m: int = 20               # analysis grid
    # Sentinel-1 search windows (days)
    s1_days_before: int = 36
    s1_days_after: int = 24
    # Sentinel-1 change detection (dB)
    water_db: float = -16.0              # VV below this = open water
    drop_db: float = 3.0                 # VV drop that marks NEW water
    rise_db: float = 3.0                 # VV rise that marks debris / surface change
    speckle_px: int = 5                  # median filter window
    min_blob_px: int = 12                # remove specks smaller than this
    # Terrain masks
    max_slope_deg: float = 30.0          # steeper = radar layover/shadow risk, excluded
    max_rel_elev_m: float = 60.0         # height above local valley floor where a flood can reach
    valley_window_m: int = 1500          # window that defines the "local valley floor"
    # Sentinel-2 optical
    s2_max_cloud: float = 60.0
    s2_days_before: int = 60
    s2_days_after: int = 30
    mndwi_water: float = 0.0
    ndvi_loss_pre: float = 0.35          # vegetated before ...
    ndvi_loss_post: float = 0.15         # ... bare after  -> debris / scour
    # Damage overlay
    hit_buffer_px: int = 1               # 1 px = 20 m around a building / road point
    road_sample_m: float = 10.0
    # Network analysis
    snap_max_m: float = 2000.0           # settlement must be within this of a road
    osm_buffer_km: float = 5.0           # roads/destinations fetched beyond the AOI
    include_tracks: bool = True          # jeep tracks count as roads
    include_paths: bool = False          # foot trails do not
    osm_snapshot: str | None = None      # default: min(event-1day, OHSOME_LATEST)
    # Bonus flow path
    flow_max_km: float = 80.0

    def snapshot(self) -> str:
        if self.osm_snapshot:
            return self.osm_snapshot
        ev = date.fromisoformat(self.event_date)
        latest = date.fromisoformat(OHSOME_LATEST)
        day_before = date.fromordinal(ev.toordinal() - 1)
        return min(day_before, latest).isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["osm_snapshot"] = self.snapshot()
        return d


ATTRIBUTION = [
    "Contains modified Copernicus Sentinel data 2026.",
    "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH "
    "2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.",
    "© OpenStreetMap contributors (ODbL), pre-event snapshot via the ohsome API (HeiGIT).",
    "Sentinel-1 RTC, Sentinel-2 L2A and Copernicus DEM accessed via Microsoft Planetary Computer.",
    "Validation only: European Union, Copernicus Emergency Management Service data (EMSR927).",
]
