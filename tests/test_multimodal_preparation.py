from datetime import date

from scripts.build_multimodal_master import apply_satellite, weather_window


def test_weather_window_excludes_capture_day():
    weather = {
        'tamsat': {("farmer", "site", "2024-01-01", "rainfall"): 2.0,
                   ("farmer", "site", "2024-01-02", "rainfall"): 3.0},
        'era5': {("farmer", "site", "2024-01-01", "mean_2m_air_temperature"): 280.0,
                 ("farmer", "site", "2024-01-02", "mean_2m_air_temperature"): 281.0}
    }
    result = weather_window(weather, ("farmer", "site"), date(2024, 1, 3), 2)
    assert result['rain'] == [2.0, 3.0]
    assert result['mean_temp'] == [280.0, 281.0]


def test_satellite_requires_same_day_pair_and_strictly_earlier():
    row = {'date': '2024-01-10', 'farmer_unique_id': 'farmer', 'site_id': 'site'}
    complete_dates = {
        ('farmer', 'site', '2024-01-09'): {'VV': -10.0, 'VH': -16.0},
        ('farmer', 'site', '2024-01-10'): {'VV': -11.0, 'VH': -17.0},
        ('farmer', 'site', '2024-01-08'): {'VV': -12.0}
    }
    apply_satellite(row, {}, complete_dates)
    assert row['sentinel_date'] == '2024-01-09'
    assert row['sentinel_age_days'] == 1
    assert row['satellite_available_12d'] is True
    assert row['satellite_available_24d'] is True
