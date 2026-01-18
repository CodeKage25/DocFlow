"""
UI Tests for DocFlow Review Dashboard

Tests for component rendering and user interactions.
"""

import pytest
from pathlib import Path
import subprocess
import json


class TestUIComponents:
    """Tests for UI component structure."""
    
    def test_tsx_files_exist(self):
        """All required TSX files should exist."""
        ui_src = Path(__file__).parent.parent.parent / "ui" / "src"
        
        required_files = [
            "App.tsx",
            "main.tsx",
            "types/index.ts",
            "api/reviewApi.ts"
        ]
        
        for file_path in required_files:
            full_path = ui_src / file_path
            assert full_path.exists(), f"Missing file: {file_path}"
    
    def test_css_files_exist(self):
        """CSS files should exist."""
        ui_src = Path(__file__).parent.parent.parent / "ui" / "src"
        
        required_css = ["index.css", "App.css"]
        
        for css_file in required_css:
            full_path = ui_src / css_file
            assert full_path.exists(), f"Missing CSS file: {css_file}"
    
    def test_package_json_valid(self):
        """package.json should be valid JSON."""
        ui_dir = Path(__file__).parent.parent.parent / "ui"
        package_json = ui_dir / "package.json"
        
        assert package_json.exists()
        
        with open(package_json) as f:
            data = json.load(f)
        
        assert "name" in data
        assert "dependencies" in data
        assert "react" in data["dependencies"]


class TestTypeDefinitions:
    """Tests for TypeScript type definitions."""
    
    def test_types_defined(self):
        """Required types should be defined."""
        types_file = Path(__file__).parent.parent.parent / "ui" / "src" / "types" / "index.ts"
        
        assert types_file.exists()
        
        content = types_file.read_text()
        
        required_types = [
            "ReviewItem",
            "ReviewResult",
            "FieldCorrection",
            "QueueStats",
            "ReviewerStats"
        ]
        
        for type_name in required_types:
            assert type_name in content, f"Missing type: {type_name}"


class TestAPIClient:
    """Tests for API client structure."""
    
    def test_api_client_exists(self):
        """API client file should exist."""
        api_file = Path(__file__).parent.parent.parent / "ui" / "src" / "api" / "reviewApi.ts"
        assert api_file.exists()
    
    def test_api_methods_defined(self):
        """Required API methods should be defined."""
        api_file = Path(__file__).parent.parent.parent / "ui" / "src" / "api" / "reviewApi.ts"
        content = api_file.read_text()
        
        required_methods = [
            "getQueue",
            "getQueueStats",
            "claimItem",
            "releaseItem",
            "submitReview"
        ]
        
        for method in required_methods:
            assert method in content, f"Missing API method: {method}"


class TestKeyboardShortcuts:
    """Tests for keyboard shortcut definitions."""
    
    def test_shortcuts_documented(self):
        """Keyboard shortcuts should be in the code."""
        app_file = Path(__file__).parent.parent.parent / "ui" / "src" / "App.tsx"
        content = app_file.read_text()
        
        # Check for keyboard shortcut handling
        assert "keydown" in content.lower() or "onkeydown" in content.lower() or "handleKeyDown" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
