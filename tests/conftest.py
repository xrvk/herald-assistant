"""
Shared pytest fixtures for the Scout Report test suite.
"""
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def no_filter_file_io():
    """Patch _save_filters to a no-op so tests never write to disk."""
    with patch("main._save_filters"):
        yield
