# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Drive a running example through its hero turns in a headless browser and save screenshots.

Requires the vertical's API and app to be running (scripts/run_demo.py), an updated
Google Chrome Stable installation, and ``pip install playwright==1.64.1`` in a virtualenv:

    python scripts/screenshot_tour.py --vertical travel [--base-url URL] [--out DIR]
    python scripts/screenshot_tour.py --vertical travel --merchant

A storefront tour captures the home, a mid-stream frame, each turn, the vertical's second view
(orders, trips, account, tickets), the shopper's sheet, and the activity panel twice (memory
marked, then the last reply's steps). A portal tour captures the views, the opening turns, the
applied state after approving the staged change, the turns that follow the approval, and the
same two activity-panel frames. Output goes to /tmp/acme-<vertical>-tour or
/tmp/acme-<vertical>-merchant-tour.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

VERTICALS: dict[str, dict] = {
    "retail": {
        "base_url": "http://localhost:3000",
        "view": ("orders", "Orders"),
        "turns": [
            (
                "search-carousel",
                "My niece turns nine next month and she's deep into building sets "
                "and puzzles — what would make a great gift? Somewhere under $45.",
            ),
            (
                "comparison",
                "Compare the top two — which is sturdier and better for a 9-year-old?",
            ),
            (
                "cart-checkout",
                "Add the wooden block set to my cart — that's everything, I'm ready to check out.",
            ),
            ("order-status", "Separate thing: where's my dog bed order? It feels late."),
            (
                "plan",
                "New project: help me set up a home office in a small spare room. "
                "Budget about $800 all-in.",
            ),
            (
                "research-guide",
                "Different question — I want to sort out a proper coffee setup at home but I "
                "don't know where to start. What should I be thinking about?",
            ),
            (
                "research-pick",
                "We get through a lot of it in the mornings and I don't want any fuss — just "
                "tell me what you'd get.",
            ),
        ],
    },
    "travel": {
        "base_url": "http://localhost:3001",
        "view": ("trips", "Trips"),
        "turns": [
            (
                "itinerary",
                "Plan me a long weekend in Lisbon in mid-October — lay it out day by day.",
            ),
            (
                "refundable-comparison",
                "Compare that stay against a refundable alternative — what does the "
                "flexibility actually cost?",
            ),
            (
                # Volunteers a durable preference so the async extractor saves a NEW fact
                # and the memory-panel frame has a marked fact to show.
                "add-refundable",
                "Add the refundable one to my trip — my partner's schedule shifts a "
                "lot, so we always book refundable fares. What does the cancellation "
                "window look like?",
            ),
            (
                "boarding-pass",
                "That's everything for now — stage the checkout for my trip.",
            ),
        ],
    },
    "telecom": {
        "base_url": "http://localhost:3002",
        "view": ("account", "Account"),
        "turns": [
            (
                "plan-matrix",
                "I keep running out of data before the month ends. What plan would "
                "actually fit me? Show the options side by side.",
            ),
            (
                "upgrade-eligibility",
                "Am I eligible to upgrade my ACME Phone 4 yet? Show me what I could "
                "move to and what the trade-in would get me.",
            ),
            (
                "service-facts",
                "Before I change anything — what's the early termination fee on my "
                "plan, really? Show me the official service facts.",
            ),
            (
                "plan-switch",
                "Switch me to the Plus 15GB plan and add it to my order — and confirm "
                "there's no fee for changing plans mid-cycle. For the record, I'd "
                "always rather have a predictable bill than the absolute cheapest "
                "price.",
            ),
            (
                "activation-ticket",
                "That's everything — check out the plan change.",
            ),
        ],
    },
    "entertainment": {
        "base_url": "http://localhost:3003",
        "view": ("tickets", "Tickets"),
        "turns": [
            (
                "venue-map",
                "Two tickets to The Headliner show on the Friday night — somewhere "
                "with actual seats, not the pit. What are my tier options, and where "
                "do they put me in the room?",
            ),
            (
                "tier-comparison",
                "Compare the seated tiers for that night side by side — which is the "
                "better night out for the money?",
            ),
            (
                "fee-breakdown",
                "Before I commit — break down exactly what the Lower Bowl price is "
                "made of. Face value, every fee, the lot.",
            ),
            (
                # Volunteers a durable constraint so the async extractor saves a NEW fact
                # and the memory-panel frame has a marked fact to show.
                "checkout-hold",
                "Hold two Lower Bowl tickets for me while I find my card. Also worth "
                "knowing for next time: weeknights never work for us — weekend shows "
                "only from here on.",
            ),
        ],
    },
}


# The merchant/supplier portals (mounted on each vertical's API, served on their own ports).
MERCHANT_TOURS: dict[str, dict] = {
    "retail": {
        "base_url": "http://localhost:3100",
        "assistant_box_label": "Message the merchant assistant",
        "views": [
            ("catalog", "Catalog"),
            ("orders", "Orders"),
            ("inventory", "Inventory"),
        ],
        "turns": [
            ("morning-digest", "What needs my attention this morning?"),
            (
                "change-preview",
                "Restock the ocean wall decals with enough to cover the next month at the "
                "current pace, and fix that listing's description so it covers what's been "
                "missing. Show me both before anything goes live.",
            ),
        ],
        "after": [
            (
                "trend-check",
                "Kids-room decor feels like it's having a moment. Pull the numbers — is the "
                "under-the-sea line really outperforming the rest of the store this month?",
            ),
            (
                "sales-drivers",
                "Why did sales move over the last two weeks — which category or listings drove "
                "it, and by how much?",
            ),
        ],
    },
    "travel": {
        "base_url": "http://localhost:3101",
        "assistant_box_label": "Message the supplier assistant",
        "views": [("properties", "Properties"), ("bookings", "Bookings")],
        "turns": [
            (
                "october-pacing",
                "Home says two stays are pacing soft. Which two, how does their October look on "
                "the calendar, and where should rates move?",
            ),
            (
                "rate-preview",
                "Ease the midweek rates by about ten percent on the two softest properties, but "
                "only for their soft October weeks. The rest of the calendar stays where it is. "
                "Show me the impact before anything goes live.",
            ),
        ],
        "after": [
            (
                "campaign-draft",
                "Now draft a shoulder-season campaign for those same two properties to go with "
                "the rate move: email to past guests, a modest budget of around $600. Stage it "
                "as a draft so I can read it first.",
            ),
            (
                "listing-check",
                "Which of our listings are losing us bookings on the page itself? If a "
                "description is thin, tell me which one; if the pages are fine, say so and tell "
                "me where the gap really is.",
            ),
        ],
    },
    "telecom": {
        "base_url": "http://localhost:3102",
        "assistant_box_label": "Message the operations assistant",
        "views": [("catalog", "Catalog"), ("base", "Base")],
        "turns": [
            (
                "churn-by-plan",
                "Show me churn by plan across the base. Which plan is bleeding, and since when?",
            ),
            (
                "retention-preview",
                "Put a retention offer in front of the Essential lines that keep buying "
                "top-ups: a month of Plus 15GB at the Essential price if they move up. Stage "
                "it as a campaign to that cohort, app push, $1,500. Show me the draft before "
                "anything goes out.",
            ),
        ],
        "after": [
            (
                "restock-preview",
                "Stock check while I'm here. Whatever device the alerts are flagging, stage a "
                "restock sized from its sell-through so we're covered for the next month. "
                "Preview first.",
            ),
            (
                "net-adds",
                "Where did last week's net adds come from against the week before? Put gross "
                "adds, deacts, port-ins and port-outs on one card and tell me what moved.",
            ),
        ],
    },
    "entertainment": {
        "base_url": "http://localhost:3103",
        "assistant_box_label": "Message the box-office assistant",
        "views": [("events", "Events"), ("holds", "Holds")],
        "turns": [
            (
                "pacing-review",
                "How is the Friday Headliner show pacing against comparable events?",
            ),
            (
                "release-preview",
                "Release sixty of the promoter-hold seats on the Upper Terrace so they're "
                "on sale for the final stretch. Show me the release before it goes live.",
            ),
        ],
        "after": [
            (
                "price-preview",
                "While we're on that tier: tag the Upper Terrace listing 'almost gone' and "
                "take its price up 15% for the final push. Stage both and show me before "
                "anything goes live.",
            ),
            (
                "weekly-sales",
                "Which shows sold the most tickets this week, and how does each compare with "
                "the week before? Put it on a card.",
            ),
        ],
    },
}


def wait_for_turn_to_finish(page, timeout_ms: int = 180_000) -> None:
    """The message box's placeholder reads "Working…" while a reply streams."""
    page.wait_for_timeout(1_200)
    page.wait_for_function(
        "() => { const box = document.querySelector('textarea');"
        " return box && !box.placeholder.toLowerCase().includes('working'); }",
        timeout=timeout_ms,
    )
    page.wait_for_timeout(800)


def capture(page, output_dir: Path, name: str, **kwargs) -> None:
    page.screenshot(path=str(output_dir / f"{name}.png"), **kwargs)
    print(f"captured {name}")


def capture_activity_panel(page, output_dir: Path, name: str, wait_for_memory: bool) -> None:
    """Open the activity panel on the latest reply and capture it. With ``wait_for_memory``
    the capture waits for the memory section to mark a fact saved this session, which the
    app re-reads a few seconds after the reply settles."""
    button = page.get_by_role("button", name="Activity")
    if button.count() == 0:
        print(f"no Activity control found — skipped {name}")
        return
    button.first.click()
    page.wait_for_timeout(700)
    if wait_for_memory:
        try:
            page.get_by_text("new this session").first.wait_for(timeout=12_000)
        except PlaywrightTimeoutError:
            print("no fact was saved this session — capturing the panel unmarked")
    capture(page, output_dir, name)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)


def launch_browser(playwright):
    """Use updated Chrome Stable with its sandbox and a temporary browser profile."""
    return playwright.chromium.launch(channel="chrome", chromium_sandbox=True)


def run_storefront_tour(page, config: dict, output_dir: Path) -> None:
    counter = 0

    def numbered(name: str) -> str:
        nonlocal counter
        counter += 1
        return f"{counter:02d}-{name}"

    capture(page, output_dir, numbered("home"))
    for index, (name, message) in enumerate(config["turns"]):
        box = page.locator("textarea")
        box.fill(message)
        box.press("Enter")
        if index == 0:
            page.wait_for_timeout(4_500)
            capture(page, output_dir, numbered(f"{name}-streaming"))
        wait_for_turn_to_finish(page)
        # Rest the frame on the newest card rather than the end of the closing prose.
        components = page.locator("[data-component]")
        if components.count() > 0:
            components.last.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
        capture(page, output_dir, numbered(name))

    # The vertical's second view, then the shopper's sheet, then back to the conversation.
    views = page.locator('nav[aria-label="Views"]').first
    shot, label = config["view"]
    views.get_by_role("button", name=label).click()
    page.wait_for_timeout(900)
    capture(page, output_dir, numbered(shot))
    page.locator('header button[aria-label$="profile and memory"]').first.click()
    page.wait_for_timeout(700)
    capture(page, output_dir, numbered("account-sheet"))
    page.keyboard.press("Escape")
    views.get_by_role("button", name="Assistant").click()
    page.wait_for_timeout(500)

    capture_activity_panel(page, output_dir, numbered("memory-panel"), wait_for_memory=True)
    capture_activity_panel(page, output_dir, numbered("inspector"), wait_for_memory=False)


def run_merchant_tour(page, config: dict, output_dir: Path) -> None:
    # Frames are numbered in capture order: home, the views, the turns, then the fixed tail.
    frame = iter(range(1, 100))

    def numbered(name: str) -> str:
        return f"{next(frame):02d}-{name}"

    capture(page, output_dir, numbered("home"))
    views = page.locator('nav[aria-label="Portal views"]').first
    for name, label in config["views"]:
        views.get_by_role("button", name=label).click()
        page.wait_for_timeout(900)
        capture(page, output_dir, numbered(name))
    views.get_by_role("button", name="Home").click()
    page.wait_for_timeout(600)

    box = page.locator(f'textarea[aria-label="{config["assistant_box_label"]}"]')

    def send(turns: list[tuple[str, str]]) -> None:
        for name, message in turns:
            box.fill(message)
            box.press("Enter")
            wait_for_turn_to_finish(page)
            capture(page, output_dir, numbered(name), full_page=True)

    send(config["turns"])
    # Approve the first staged change on its card, then carry on with the turns that
    # build on the applied state.
    approve = page.get_by_role("button", name="Approve")
    if approve.count() > 0:
        approve.first.click()
        page.wait_for_timeout(2_500)
        capture(page, output_dir, numbered("applied"), full_page=True)
    else:
        print("no Approve button found — skipped applied capture")
    send(config["after"])

    capture_activity_panel(page, output_dir, numbered("memory-panel"), wait_for_memory=True)
    capture_activity_panel(page, output_dir, numbered("inspector"), wait_for_memory=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vertical", default="retail", choices=sorted(VERTICALS))
    parser.add_argument("--merchant", action="store_true", help="tour the merchant portal")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    config = (MERCHANT_TOURS if args.merchant else VERTICALS)[args.vertical]
    suffix = "merchant-tour" if args.merchant else "tour"
    output_dir = Path(args.out or f"/tmp/acme-{args.vertical}-{suffix}")
    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1480, "height": 940})
        page.goto(args.base_url or config["base_url"], wait_until="networkidle")
        page.wait_for_timeout(900)
        (run_merchant_tour if args.merchant else run_storefront_tour)(page, config, output_dir)
        browser.close()
    print(f"Saved screenshots to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
