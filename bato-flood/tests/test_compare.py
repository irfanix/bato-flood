"""EMS check works with a GeoPackage whose layer is called observedEventA."""
import geopandas as gpd
import numpy as np
from affine import Affine
from shapely.geometry import box

from bato.compare import compare
from bato.flood import WATER, NONE
from bato.satellite import Grid


def test_gpkg(tmp_path):
    grid = Grid("EPSG:32645", Affine(20, 0, 300000, 0, -20, 3110000), (100, 100))
    cls = np.full((100, 100), NONE, np.uint8)
    cls[10:50, 10:50] = WATER
    ref = gpd.GeoDataFrame(geometry=[box(300000 + 200, 3110000 - 1000, 300000 + 1000, 3110000 - 200)], crs="EPSG:32645")
    aoi = gpd.GeoDataFrame(geometry=[box(300000, 3108000, 302000, 3110000)], crs="EPSG:32645")
    p = tmp_path / "EMSR927.gpkg"
    ref.to_file(p, layer="EMSR927_AOI01_DEL_PRODUCT_observedEventA_v1", driver="GPKG")
    aoi.to_file(p, layer="EMSR927_AOI01_DEL_PRODUCT_areaOfInterestA_v1", driver="GPKG")
    r = compare(cls, grid, [str(p)], None, str(tmp_path / "c.png"))
    assert r["recall"] > 0.95 and r["precision"] > 0.95
    assert any("observedEvent" in n for n in r["ems_layers"])
