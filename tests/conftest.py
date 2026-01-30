"""
Pytest configuration and fixtures.
"""
import os
import sys
import pytest

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def project_root():
    """Return the project root directory."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session")
def app_dir(project_root):
    """Return the app directory."""
    return os.path.join(project_root, "app")
