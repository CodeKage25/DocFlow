"""
Playwright E2E Tests for DocFlow UI

"""

import pytest
from playwright.sync_api import Page, expect

# Use dev server by default for testing
BASE_URL = "http://localhost:3000"


@pytest.fixture(autouse=True)
def setup(page: Page):
    """Navigate to the app before each test."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle", timeout=10000)


def test_dashboard_loads(page: Page):
    """Test that main dashboard loads with key elements."""
    # Check for main title in header
    expect(page.locator("text=DocFlow").first).to_be_visible(timeout=10000)
    
    # Check for document processing section
    expect(page.get_by_text("Document Processing")).to_be_visible()


def test_stats_cards_visible(page: Page):
    """Test that stats cards are displayed."""
    expect(page.get_by_text("COMPLETED").first).to_be_visible()
    expect(page.get_by_text("NEED REVIEW").first).to_be_visible()
    expect(page.get_by_text("ERRORS").first).to_be_visible()


def test_file_upload_zone_exists(page: Page):
    """Test that file upload zone is present."""
    expect(page.get_by_text("Drop files here")).to_be_visible()


def test_navigation_buttons(page: Page):
    """Test that navigation buttons are visible."""
    # Use role button to be specific
    expect(page.get_by_role("button").filter(has_text="Upload")).to_be_visible()
    expect(page.get_by_role("button").filter(has_text="Review")).to_be_visible()
    expect(page.get_by_role("button").filter(has_text="Metrics")).to_be_visible()


def test_review_queue_navigation(page: Page):
    """Test navigation to review queue."""
    # Click Review link/button
    page.locator("text=Review").first.click()
    
    # Check that we are in Review Queue view
    expect(page.get_by_text("Review Queue")).to_be_visible(timeout=5000)
    # Check for presence of tabs using role button
    expect(page.get_by_role("button").filter(has_text="Pending").first).to_be_visible()
    expect(page.get_by_role("button").filter(has_text="Approved")).to_be_visible()


def test_metrics_navigation(page: Page):
    """Test navigation to metrics view."""
    # Click Metrics link
    page.locator("text=Metrics").first.click()
    
    # Check for metrics content
    expect(page.get_by_text("System Metrics")).to_be_visible(timeout=5000)


def test_queue_tabs_switch(page: Page):
    """Test switching between Pending and Approved tabs."""
    # Navigate to review queue
    page.locator("text=Review").first.click()
    expect(page.get_by_text("Review Queue")).to_be_visible(timeout=5000)
    
    # Click Approved tab
    page.get_by_role("button").filter(has_text="Approved").click()
    
    # Click back to Pending tab
    page.get_by_role("button").filter(has_text="Pending").first.click()
    
    # Pending stats should be visible - check for the quick stat label specifically or just the tab active state
    expect(page.locator(".queue-tab.active").filter(has_text="Pending")).to_be_visible()
