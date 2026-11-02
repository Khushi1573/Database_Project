# tests/test_validation.py
from src.config_manager import validate_cell

def test_valid_innodb():
    # Valid setup
    errors = validate_cell("A", {"engine": "InnoDB", "index": "BTREE", "ahi": "off"})
    assert len(errors) == 0

def test_invalid_memory_ahi():
    # Invalid setup: MEMORY cannot have AHI turned on
    errors = validate_cell("E", {"engine": "MEMORY", "index": "BTREE", "ahi": "on"})
    assert len(errors) > 0
    assert "AHI setting not allowed on MEMORY cells" in errors[0]