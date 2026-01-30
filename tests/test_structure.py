"""
Tests for Module 1: Project Structure Verification
"""
import os
import pytest


class TestProjectStructure:
    """Test that all required directories and files exist."""
    
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Required directories
    REQUIRED_DIRS = [
        "app",
        "app/database",
        "app/schemas",
        "app/core",
        "app/core/scoring",
        "app/core/channel",
        "app/generators",
        "app/generators/ads",
        "app/generators/seo_geo",
        "app/generators/social",
        "app/compliance",
        "app/exporters",
        "app/api",
        "app/api/v1",
        "app/tasks",
        "migrations",
        "migrations/versions",
        "scripts",
        "tests",
    ]
    
    # Required __init__.py files
    REQUIRED_INIT_FILES = [
        "app/__init__.py",
        "app/database/__init__.py",
        "app/schemas/__init__.py",
        "app/core/__init__.py",
        "app/core/scoring/__init__.py",
        "app/core/channel/__init__.py",
        "app/generators/__init__.py",
        "app/generators/ads/__init__.py",
        "app/generators/seo_geo/__init__.py",
        "app/generators/social/__init__.py",
        "app/compliance/__init__.py",
        "app/exporters/__init__.py",
        "app/api/__init__.py",
        "app/api/v1/__init__.py",
        "app/tasks/__init__.py",
        "tests/__init__.py",
    ]
    
    # Required config files
    REQUIRED_CONFIG_FILES = [
        ".gitignore",
        ".env.example",
        "requirements.txt",
    ]
    
    @pytest.mark.parametrize("dir_path", REQUIRED_DIRS)
    def test_directory_exists(self, dir_path: str):
        """Test that required directories exist."""
        full_path = os.path.join(self.BASE_DIR, dir_path)
        assert os.path.isdir(full_path), f"Directory not found: {dir_path}"
    
    @pytest.mark.parametrize("file_path", REQUIRED_INIT_FILES)
    def test_init_file_exists(self, file_path: str):
        """Test that required __init__.py files exist."""
        full_path = os.path.join(self.BASE_DIR, file_path)
        assert os.path.isfile(full_path), f"File not found: {file_path}"
    
    @pytest.mark.parametrize("file_path", REQUIRED_CONFIG_FILES)
    def test_config_file_exists(self, file_path: str):
        """Test that required config files exist."""
        full_path = os.path.join(self.BASE_DIR, file_path)
        assert os.path.isfile(full_path), f"Config file not found: {file_path}"
    
    def test_requirements_has_fastapi(self):
        """Test that requirements.txt includes FastAPI."""
        req_path = os.path.join(self.BASE_DIR, "requirements.txt")
        with open(req_path, "r") as f:
            content = f.read().lower()
        assert "fastapi" in content, "FastAPI not found in requirements.txt"
    
    def test_requirements_has_sqlalchemy(self):
        """Test that requirements.txt includes SQLAlchemy."""
        req_path = os.path.join(self.BASE_DIR, "requirements.txt")
        with open(req_path, "r") as f:
            content = f.read().lower()
        assert "sqlalchemy" in content, "SQLAlchemy not found in requirements.txt"
    
    def test_gitignore_has_env(self):
        """Test that .gitignore excludes .env file."""
        gitignore_path = os.path.join(self.BASE_DIR, ".gitignore")
        with open(gitignore_path, "r") as f:
            content = f.read()
        assert ".env" in content, ".env not found in .gitignore"
    
    def test_env_example_has_database_url(self):
        """Test that .env.example has DATABASE_URL."""
        env_path = os.path.join(self.BASE_DIR, ".env.example")
        with open(env_path, "r") as f:
            content = f.read()
        assert "DATABASE_URL" in content, "DATABASE_URL not found in .env.example"
