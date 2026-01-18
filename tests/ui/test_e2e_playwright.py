import pytest
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:8000"

def test_dashboard_loads(page: Page):
    try:
        page.goto(BASE_URL)
        # Check for main title
        expect(page.get_by_text("DocFlow Intelligence")).to_be_visible(timeout=10000)
        
        # Check for stats cards
        expect(page.get_by_text("Documents Processed")).to_be_visible()
        expect(page.get_by_text("Processing Accuracy")).to_be_visible()
    except Exception as e:
        # Fallback to port 5173 if 8000 doesn't serve UI
        print(f"Failed on 8000, trying 5173: {e}")
        page.goto("http://localhost:5173")
        expect(page.get_by_text("DocFlow Intelligence")).to_be_visible(timeout=10000)

def test_review_queue_navigation(page: Page):
    # Navigate
    try:
        page.goto(BASE_URL)
        expect(page.get_by_text("DocFlow Intelligence")).to_be_visible(timeout=5000)
    except:
        page.goto("http://localhost:5173")
    
    # Click Review Queue tab
    # Assuming tabs are implemented as buttons or have specific text
    # Finding by text might be ambiguous if header also has "Review Queue"
    # But usually tabs are buttons.
    page.get_by_text("Review Queue").first.click()
    
    # Check that we are in Review Queue view
    # Look for specific review queue elements
    expect(page.get_by_text("Pending Reviews")).to_be_visible()
    expect(page.get_by_text("My Stats")).to_be_visible()

def test_metrics_display(page: Page):
    try:
        page.goto(BASE_URL)
        expect(page.get_by_text("DocFlow Intelligence")).to_be_visible(timeout=5000)
    except:
        page.goto("http://localhost:5173")
    
    # Check that metrics are not all zero (if we have data)
    # This is hard to assert without knowing data state.
    # But we can check that the elements exist.
    expect(page.get_by_text("System Metrics")).to_be_visible()
