TEST_CASE = {
    "id": "TC_001",
    "name": "Sample Citrix Login",
    "description": "Demonstrates keyboard/mouse-only actions against a Citrix desktop.",
    "show_in_gui": False,
}


def run(ctx):
    ctx.step("Bring the Citrix session into focus")
    ctx.hotkey("alt", "tab")
    ctx.wait_for_citrix()

    ctx.step("Open the application login/search field")
    ctx.hotkey("ctrl", "f")

    ctx.step("Enter sample search text")
    ctx.type_text("sample user")
    ctx.press("enter")
    ctx.wait_for_citrix()

    ctx.step("Verify expected screen manually or extend this script with image checks")
