from pathlib import Path
import importlib

def test_inspect_module_importable():
    mod = importlib.import_module('src.checks.inspect_json_schema')
    assert hasattr(mod, 'inspect_sample_json')

def test_match_module_importable():
    mod = importlib.import_module('src.checks.match_images_labels')
    assert hasattr(mod, 'match_images_labels')
