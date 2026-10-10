"""CRS helpers: choosing the projected CRS used for measurement."""
from functools import lru_cache

from pyproj import CRS, Transformer

WGS84 = CRS.from_epsg(4326)


def utm_epsg_for(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone containing (lon, lat) (WGS84).

    North: 326xx, South: 327xx. Beyond 84N/80S UTM is undefined, so the polar
    stereographic UPS codes (32661/32761) are used.
    """
    if lat >= 84:
        return 32661
    if lat <= -80:
        return 32761
    lon = ((lon + 180) % 360) - 180
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + min(zone, 60)


def crs_label(crs: CRS) -> str:
    """Human readable identifier, e.g. 'EPSG:4326'."""
    epsg = crs.to_epsg(min_confidence=70)
    if epsg:
        return f"EPSG:{epsg}"
    auth = crs.to_authority(min_confidence=70)
    return f"{auth[0]}:{auth[1]}" if auth else (crs.name or "UNKNOWN")


@lru_cache(maxsize=256)
def transformer(src_wkt: str, dst_wkt: str) -> Transformer:
    # always_xy keeps (x=lon, y=lat) ordering regardless of the CRS axis definition.
    return Transformer.from_crs(CRS.from_wkt(src_wkt), CRS.from_wkt(dst_wkt), always_xy=True)


def get_transformer(src: CRS, dst: CRS) -> Transformer:
    return transformer(src.to_wkt(), dst.to_wkt())
