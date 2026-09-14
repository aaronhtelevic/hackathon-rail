"""Small equirectangular helpers (Belgium is small; matches the scorer's own approximation)."""
import numpy as np

M_PER_DEG_LAT = 110540.0


def m_per_deg_lon(lat):
    return 111320.0 * np.cos(np.radians(lat))


def to_xy(lon, lat, lat0):
    return np.asarray(lon) * m_per_deg_lon(lat0), np.asarray(lat) * M_PER_DEG_LAT


def dist_m(lon1, lat1, lon2, lat2):
    lat0 = (np.asarray(lat1) + np.asarray(lat2)) / 2
    dx = (np.asarray(lon2) - np.asarray(lon1)) * m_per_deg_lon(lat0)
    dy = (np.asarray(lat2) - np.asarray(lat1)) * M_PER_DEG_LAT
    return np.hypot(dx, dy)
