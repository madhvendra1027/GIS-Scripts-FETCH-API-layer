"""example_run.py — exercises the full pipeline with sample data, no
network or gis_fetcher required. Run with: python example_run.py
"""
from datetime import datetime, timezone

from backend.prioritization import VulnerabilityInputs, prioritize
from backend.zone_classifier import classify_zone
from data_pipeline.cleaning import ZoneHistory, clean_reading
from data_pipeline.hazard_reading_store import HazardReadingStore
from data_pipeline.models import DataQuality, HazardReading, HazardType
from ml_service.features.feature_engineering import build_feature_dict
from ml_service.inference.predictor import predict

ZONE_ID = "Z-SAHARANPUR-01"

# Sample raw parameters as if normalize.py had just produced them for FLOOD.
raw_flood_params = {
    "rainfall_mm_24h": 80.0,
    "rainfall_mm_72h": 210.0,
    "river_level_m": 3.8,
    "river_level_change_rate_m_per_hr": 0.3,
    "soil_saturation_pct": 65.0,
    "elevation_m": 12.0,
    "distance_to_river_m": 250.0,
    "historical_flood_count": None,  # simulate a gap cleaning.py must handle
}

history = ZoneHistory()
cleaned = clean_reading(ZONE_ID, raw_flood_params, datetime.now(timezone.utc), history)
print("Cleaned FLOOD parameters:", cleaned.parameters)
print("Imputed fields:", cleaned.imputed_fields)

store = HazardReadingStore(db_path="/tmp/example_hazard_readings.db")
store.save(HazardReading(
    zone_id=ZONE_ID,
    hazard_type=HazardType.FLOOD,
    source="open-meteo+manual",
    recorded_at=datetime.now(timezone.utc),
    parameters=cleaned.parameters,
    data_quality=DataQuality.IMPUTED if cleaned.imputed_fields else DataQuality.RAW,
))

# Also write sample readings for the other 3 hazards so zone_status has a
# full picture, same as api.py's zone_status endpoint expects.
store.save(HazardReading(
    zone_id=ZONE_ID, hazard_type=HazardType.LANDSLIDE, source="manual",
    recorded_at=datetime.now(timezone.utc),
    parameters={"slope_deg": 18.0, "rainfall_mm_72h": 210.0, "soil_moisture_pct": 55.0,
                "soil_type_code": 2, "vegetation_index": 0.5, "historical_landslide_count": 1},
))
store.save(HazardReading(
    zone_id=ZONE_ID, hazard_type=HazardType.EROSION, source="manual",
    recorded_at=datetime.now(timezone.utc),
    parameters={"shoreline_change_rate_m_per_yr": None, "wave_energy_index": None,
                "distance_to_coast_m": None, "sediment_type_code": None,
                "mangrove_cover_pct": None, "historical_erosion_events": None},
))
store.save(HazardReading(
    zone_id=ZONE_ID, hazard_type=HazardType.CLOUDBURST, source="open-meteo",
    recorded_at=datetime.now(timezone.utc),
    parameters={"rainfall_intensity_mm_per_hr": 22.0, "humidity_pct": 78.0,
                "temperature_c": 24.0, "elevation_m": 12.0, "wind_speed_kmph": 14.0,
                "historical_cloudburst_count": 2},
))

features_by_hazard = {
    hazard: build_feature_dict(hazard, store.latest_for_zone(ZONE_ID, hazard).parameters)
    for hazard in HazardType
}
scores = predict(features_by_hazard)
for hazard, result in scores.items():
    print(f"{hazard.value}: score={result.score:.3f} missing={result.missing_fields}")

classification = classify_zone(ZONE_ID, scores)
print("\nZone classification:", classification)

vulnerability = VulnerabilityInputs(
    population_density_score=0.7,
    socioeconomic_vulnerability_score=0.6,
    disaster_history_score=0.4,
)
result = prioritize(classification, vulnerability)
print("\nPrioritization result:", result)
