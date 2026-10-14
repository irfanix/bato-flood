"""ohsome down -> Overpass attic snapshot, converted to the same structure."""
import bato.osm as osm


class Resp:
    def __init__(self, code, js=None):
        self.status_code, self._js = code, js or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} Forbidden")

    def json(self):
        return self._js


def test_overpass_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(osm, "CACHE", str(tmp_path))
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    seen = []

    def fake_post(url, data=None, headers=None, timeout=None):
        if "ohsome" in url:
            return Resp(403)
        q = data["data"]
        seen.append(q)
        assert '[date:"2026-07-27T00:00:00Z"]' in q
        if "highway" in q:
            return Resp(200, {"elements": [{"type": "way", "id": 1, "tags": {"highway": "primary", "bridge": "yes", "name": "B"},
                                            "geometry": [{"lat": 28.0, "lon": 85.0}, {"lat": 28.01, "lon": 85.01}]}]})
        if "waterway" in q:
            return Resp(200, {"elements": [{"type": "way", "id": 2, "tags": {"waterway": "river"},
                                            "geometry": [{"lat": 28.0, "lon": 85.0}, {"lat": 28.1, "lon": 85.1}]}]})
        if "place" in q:
            return Resp(200, {"elements": [{"type": "node", "id": 3, "lat": 28.02, "lon": 85.02, "tags": {"place": "village", "name": "Gaun", "population": "1,200"}}]})
        if "building" in q:
            return Resp(200, {"elements": [{"type": "way", "id": 4, "center": {"lat": 28.02, "lon": 85.02}, "tags": {"building": "house"}}]})
        return Resp(200, {"elements": [{"type": "way", "id": 5, "center": {"lat": 28.03, "lon": 85.03}, "tags": {"amenity": "hospital", "name": "H"}}]})

    monkeypatch.setattr(osm.requests, "post", fake_post)
    osm.SOURCES_USED.clear()
    d = osm.fetch((85.0, 28.0, 85.1, 28.1), "2026-07-27", 1.0)
    assert d["roads"][0]["bridge"] and d["roads"][0]["name"] == "B"
    assert d["places"][0]["population"] == 1200
    assert d["health"][0]["kind"] == "hospital"
    assert len(d["buildings"]) == 1 and len(d["rivers"]) == 1
    assert any("Overpass" in s for s in d["source"])
    assert "(27.99099,84.99099,28.10901,85.10901)" in seen[0]
