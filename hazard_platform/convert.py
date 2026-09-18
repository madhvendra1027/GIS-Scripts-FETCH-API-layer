import pandas as pd
import geopandas as gpd

df = pd.read_csv('shoreline_change_rate_m_per_yr.csv')
gdf = gpd.GeoDataFrame(
    df,
    geometry=gpd.points_from_xy(df.longitude, df.latitude),
    crs='EPSG:4326'
)
gdf.to_file('shoreline_points.geojson', driver='GeoJSON')

print("Done: wrote shoreline_points.geojson")
