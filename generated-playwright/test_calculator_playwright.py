from playwright.sync_api import sync_playwright, expect, Page

def test_calculator_flow(page: Page):
    print("[STEP 1] Click on Five button")
    page.locator('[name="Five"]').wait_for(timeout=15000).click()
    print("[STEP 2] Type 5 in Display")
    page.locator('[name="Display"]').wait_for(timeout=15000).fill("5")
    print("[STEP 3] Click on Plus button")
    page.locator('[name="Plus"]').wait_for(timeout=15000).click()
    print("[STEP 4] Click on Three button")
    page.locator('[name="Three"]').wait_for(timeout=15000).click()
    print("[STEP 5] Type 3 in Display")
    page.locator('[name="Display"]').wait_for(timeout=15000).fill("3")
    print("[STEP 6] Click on Equals button")
    page.locator('[name="Equals"]').wait_for(timeout=15000).click()
    print("[STEP 7] Double click on Result display")
    page.locator('[name="Result display"]').wait_for(timeout=15000).dblclick()
    print("[STEP 8] Scroll in ApplicationFrameWindow")
    page.mouse.wheel(0, -300)
    print("[STEP 9] Right click on Result display")
    page.locator('[name="Result display"]').wait_for(timeout=15000).click(button="right")
    expect(page.locator('[name="Result display"]')).to_be_visible(timeout=15000)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    test_calculator_flow(page)
    browser.close()