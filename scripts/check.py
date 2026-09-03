# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Consistency checks over the things the repo keeps in more than one place: skills,
example fixtures, the verification scripts' vertical tables, package pins, and the
Managed Agent material derived by hand from the libraries. Exit 1 on any problem.

    python scripts/check.py
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import re
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# encoding: the marks below cannot be encoded by a Windows console in a non-UTF-8 code
# page (cp950, cp1252), which would end the run with UnicodeEncodeError on the first ok().
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "examples"))

PROBLEMS: list[str] = []
VERTICALS = ("retail", "travel", "telecom", "entertainment")


def problem(message: str) -> None:
    PROBLEMS.append(message)
    print(f"  ✗ {message}")


def ok(message: str) -> None:
    print(f"  ✓ {message}")


def catalog_ids_of(data: Path) -> set[str]:
    """Every id the storefront resolves: listings and their variants."""
    from demo_common.storefront_fixtures import load_catalog

    _, listings, variants = load_catalog(data)
    return set(listings) | set(variants)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_ws(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# The two roles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Role:
    name: str
    tree: str  # shopping-agent | merchant-agent
    skills: frozenset[str]
    server_dir: str
    server_label: str  # the "<N> ... tools" wording the managed READMEs use

    @property
    def root(self) -> Path:
        return REPO_ROOT / self.tree

    @property
    def managed(self) -> Path:
        return self.root / "managed-agents"

    @property
    def agent_dir(self) -> Path:
        return self.managed / self.tree

    def managed_config(self) -> Any:
        """The deployment the hand-derived system.md was written for."""
        if self.tree == "shopping-agent":
            from shopping_agent_sdk import default_config

            return default_config()
        from merchant_agent_sdk import default_config

        # On Managed Agents the approval affordance is the platform's prompt on the preview
        # card, shown by the present_change_preview custom tool: the MCP server's stage
        # events do not reach the operator.
        return default_config().model_copy(
            update={
                "approval_surface": "the preview card's approval prompt",
                "stage_shows_preview": False,
            }
        )

    def static_prompt(self) -> str:
        from commerce_common.skills import SkillRegistry

        skills = SkillRegistry.from_dir(self.root / "skills")
        if self.tree == "shopping-agent":
            from shopping_agent.prompt import build_static_system
        else:
            from merchant_agent.prompt import build_static_system
        return build_static_system(self.managed_config(), skills)

    def registry_descriptions(self) -> dict[str, str]:
        if self.tree == "shopping-agent":
            from shopping_agent.tools.registry import build_tools
        else:
            from merchant_agent.tools.registry import build_tools
        tools = build_tools(self.managed_config(), skill_names=[])
        return {t["name"]: t["description"] for t in tools if "description" in t}


ROLES = (
    Role(
        "shopping",
        "shopping-agent",
        frozenset(
            {
                "search-discovery",
                "planning-goals",
                "purchase-research",
                "memory-personalization",
                "customer-care",
            }
        ),
        "storefront-mcp-server",
        "storefront tools",
    ),
    Role(
        "merchant",
        "merchant-agent",
        frozenset(
            {
                "performance-insights",
                "catalog-listings",
                "inventory-operations",
                "pricing-promotions",
                "marketing-campaigns",
            }
        ),
        "merchant-mcp-server",
        "merchant tools",
    ),
)


def check_skills() -> None:
    from commerce_common.skills import load_skills

    for role in ROLES:
        label = f"{role.tree}/skills/"
        print(label)
        try:
            skills = load_skills(role.root / "skills")
        except Exception as error:
            problem(f"{label} failed to load: {error}")
            continue
        if missing := role.skills - {skill.name for skill in skills}:
            problem(f"{label}: missing expected skills: {sorted(missing)}")
        for skill in skills:
            if len(skill.description) < 40:
                problem(f"skill {skill.name}: description too short to route on")
            if len(skill.body) < 200:
                problem(f"skill {skill.name}: body looks like a stub")
        ok(f"{len(skills)} skills load, frontmatter valid")


# ---------------------------------------------------------------------------
# Example fixtures: every vertical ships the same storefront set and a merchant set
# ---------------------------------------------------------------------------


def check_storefront_fixtures() -> None:
    from commerce_common.types import MemoryFact
    from demo_common.storefront_fixtures import load_catalog
    from shopping_agent import Order, Policy, UserPreferences

    for vertical in VERTICALS:
        print(f"examples/{vertical}/data/ (storefront)")
        data = REPO_ROOT / "examples" / vertical / "data"
        try:
            entries = load_json(data / "catalog.json")["products"]
            # Loaded as the backends load it: a family's variants are filled in from the
            # family and indexed by their own ids, which order lines may reference.
            _, listings, variants = load_catalog(data)
            ids = set(listings) | set(variants)
            if len(listings) != len(entries) or listings.keys() & variants.keys():
                problem(f"{vertical}: catalog has duplicate product_ids")
            authored_variants = sum(len(entry.get("variants", [])) for entry in entries)
            if authored_variants != len(variants):
                problem(f"{vertical}: catalog has duplicate variant ids")
            for record in (*listings.values(), *variants.values()):
                if record.options and record.option_values:
                    problem(
                        f"{vertical}: {record.product_id} carries both options and option_values"
                    )
            for family in listings.values():
                for variant in family.variants:
                    if set(variant.option_values) != set(family.options):
                        problem(
                            f"{vertical}: variant {variant.product_id} does not set every option of {family.product_id}"
                        )
                in_stock = [v.price for v in family.variants if v.in_stock] or [
                    v.price for v in family.variants
                ]
                if in_stock and family.price != min(in_stock):
                    problem(
                        f"{vertical}: {family.product_id} price {family.price} is not its lowest in-stock variant's ({min(in_stock)})"
                    )
            users = load_json(data / "users.json")["users"]
            [UserPreferences.model_validate(u) for u in users]
            orders = load_json(data / "orders.json")["orders"]
            for order in orders:
                Order.model_validate({k: v for k, v in order.items() if k != "user_id"})
                for item in order["items"]:
                    if item["product_id"] not in ids:
                        problem(
                            f"{vertical}: order {order['order_id']} references unknown product {item['product_id']}"
                        )
            policies = load_json(data / "policies.json")["policies"]
            [Policy.model_validate(p) for p in policies]
            seeded = 0
            for user_id, entries in load_json(data / "memory-seed.json").items():
                for entry in entries:
                    fact = MemoryFact.model_validate(entry)
                    # Retention needs a save time; the seeds stand in for earlier sessions.
                    if fact.updated_at is None:
                        problem(f"{vertical}: memory seed {user_id}/{fact.key} has no updated_at")
                    seeded += 1
            ok(
                f"{vertical}: {len(listings)} products ({len(variants)} variants), {len(users)} users, {len(orders)} orders, "
                f"{len(policies)} policies, {seeded} seeded memory facts validate"
            )
        except Exception as error:
            problem(f"{vertical} storefront fixtures: {error}")


def require_keys(rows: list[dict[str, Any]], keys: tuple[str, ...], where: str) -> None:
    for row in rows:
        if not all(key in row for key in keys):
            problem(f"{where}: row missing keys: {row}")
            return


def check_stock_rows(rows: list[dict[str, Any]], catalog_ids: set[str], where: str) -> None:
    for row in rows:
        if row["product_id"] not in catalog_ids:
            problem(f"{where}: unknown product {row['product_id']}")
        if int(row.get("stock", -1)) < 0 or int(row.get("threshold", 0)) < 0:
            problem(f"{where}: missing/negative stock or threshold on {row['product_id']}")


def check_weekly_series(
    entries: list[dict[str, Any]],
    *,
    id_key: str,
    week_keys: tuple[str, ...],
    pct_key: str,
    catalog_ids: set[str],
    where: str,
) -> None:
    """Per-listing weekly histories the backends subscript directly."""
    for entry in entries:
        ident = entry[id_key]
        if ident not in catalog_ids:
            problem(f"{where}: unknown {id_key} {ident}")
        if not entry.get("weeks"):
            problem(f"{where}: {ident} has no weeks")
            continue
        for week in entry["weeks"]:
            if not all(key in week for key in week_keys):
                problem(f"{where}: {ident} week {week.get('week_start')} is missing fields")
                break
            if not 0 <= float(week[pct_key]) <= 100:
                problem(
                    f"{where}: {ident} week {week.get('week_start')} has an out-of-range {pct_key}"
                )
                break


def retail_extra(data: Path, catalog_ids: set[str]) -> str:
    inventory = load_json(data / "merchant_inventory.json")["inventory"]
    check_stock_rows(inventory, catalog_ids, "merchant_inventory.json")
    return f"{len(inventory)} inventory rows"


def travel_extra(data: Path, catalog_ids: set[str]) -> str:
    occupancy = load_json(data / "merchant_occupancy.json")["listings"]
    check_weekly_series(
        occupancy,
        id_key="listing_id",
        week_keys=(
            "week_start",
            "occupancy_pct",
            "midweek_occupancy_pct",
            "weekend_occupancy_pct",
            "on_the_books_pace_pct",
        ),
        pct_key="occupancy_pct",
        catalog_ids=catalog_ids,
        where="merchant_occupancy.json",
    )
    return f"{len(occupancy)} occupancy listings"


def telecom_extra(data: Path, catalog_ids: set[str]) -> str:
    subscribers = load_json(data / "merchant_subscribers.json")
    where = "merchant_subscribers.json"
    check_weekly_series(
        subscribers["plans"],
        id_key="plan_id",
        week_keys=("week_start", "subscribers", "churn_rate_pct", "arpu"),
        pct_key="churn_rate_pct",
        catalog_ids=catalog_ids,
        where=where,
    )
    # The backend quotes margin per line from this figure, so it must follow the rate
    # card the fixture itself declares.
    wholesale = subscribers.get("wholesale", {})
    per_gb = float(wholesale.get("mobile_per_gb_usd", 0))
    core = float(wholesale.get("mobile_core_per_line_usd", 0))
    for plan in subscribers["plans"]:
        if (usage := plan.get("avg_usage_gb")) is None:
            continue
        expected = round(float(usage) * per_gb + core, 2)
        if abs(float(plan["wholesale_cost_per_line_usd"]) - expected) >= 0.005:
            problem(f"{where}: {plan['plan_id']} wholesale cost != rate card {expected}")
    cohorts = subscribers.get("cohorts", [])
    for cohort in cohorts:
        if not all(key in cohort for key in ("cohort_id", "label", "definition", "size")):
            problem(f"{where}: cohort missing fields: {cohort}")
        for plan_id in cohort.get("plan_ids", []):
            if plan_id not in catalog_ids:
                problem(
                    f"{where}: cohort {cohort.get('cohort_id')} references unknown plan {plan_id}"
                )
    inventory = load_json(data / "merchant_inventory.json")
    check_stock_rows(inventory["items"], catalog_ids, "merchant_inventory.json")
    for row in inventory.get("service_content", []):
        if row["product_id"] not in catalog_ids:
            problem(
                f"merchant_inventory.json: service_content references unknown product {row['product_id']}"
            )
    return (
        f"{len(subscribers['plans'])} plan series, {len(cohorts)} cohorts, "
        f"{len(inventory['items'])} inventory rows"
    )


def entertainment_extra(data: Path, catalog_ids: set[str]) -> str:
    where = "merchant_pacing.json"
    sold_by_id = {
        row["product_id"]: int(row["sold"])
        for row in load_json(data / "inventory.json")["inventory"]
    }
    pacing = load_json(data / where)
    baselines = pacing.get("baselines", {})
    for kind, points in baselines.items():
        days, pcts = [p[0] for p in points], [p[1] for p in points]
        if days != sorted(days, reverse=True) or pcts != sorted(pcts):
            problem(f"{where}: baseline '{kind}' checkpoints are not monotonic")
    buckets = ("promoter_hold", "production_hold", "comps", "kills")
    tiers = 0
    for event in pacing["events"]:
        if event.get("baseline_kind") not in baselines:
            problem(f"{where}: {event['event_id']} has unknown baseline kind")
        for tier in event["tiers"]:
            tiers += 1
            pid = tier["product_id"]
            if pid not in catalog_ids or pid not in sold_by_id:
                problem(f"{where}: unknown tier {pid}")
                continue
            allocations = tier.get("allocations", {})
            if not all(key in allocations for key in buckets):
                problem(f"{where}: {pid} allocations missing buckets")
            elif any(int(allocations[key]) < 0 for key in buckets):
                problem(f"{where}: {pid} has a negative allocation")
            values = [int(week["sold_cum"]) for week in tier.get("weekly_sold_cum", [])]
            if not values:
                problem(f"{where}: {pid} has no weekly history")
            elif values != sorted(values):
                problem(f"{where}: {pid} weekly sold_cum is not non-decreasing")
            elif values[-1] != sold_by_id[pid]:
                # The history's last point is what the live engine boots with.
                problem(
                    f"{where}: {pid} history ends at {values[-1]}, but inventory.json sold is {sold_by_id[pid]}"
                )
    return f"{len(pacing['events'])} events / {tiers} tier histories"


@dataclass(frozen=True)
class MerchantFixtures:
    vertical: str
    label: str
    metric_keys: tuple[str, ...]
    extra: Callable[[Path, set[str]], str]


MERCHANT_FIXTURES = (
    MerchantFixtures(
        "retail",
        "merchant",
        ("date", "sales", "orders", "traffic", "kids_room_sales"),
        retail_extra,
    ),
    MerchantFixtures(
        "travel",
        "supplier",
        ("date", "bookings", "room_nights", "revenue", "traffic", "lisbon_revenue"),
        travel_extra,
    ),
    MerchantFixtures(
        "telecom",
        "commercial-ops",
        (
            "date",
            "sales",
            "orders",
            "traffic",
            "revenue",
            "subscribers",
            "gross_adds",
            "deacts",
            "port_ins",
            "port_outs",
            "prepaid_gross_adds",
        ),
        telecom_extra,
    ),
    MerchantFixtures(
        "entertainment",
        "box-office",
        ("date", "sales", "orders", "tickets", "traffic", "amphitheater_sales"),
        entertainment_extra,
    ),
)


def check_merchant_fixtures() -> None:
    from merchant_agent import Campaign, OrderIssue

    for spec in MERCHANT_FIXTURES:
        print(f"examples/{spec.vertical}/data/ ({spec.label})")
        data = REPO_ROOT / "examples" / spec.vertical / "data"
        try:
            catalog_ids = catalog_ids_of(data)
            daily = load_json(data / "merchant_metrics.json")["daily"]
            if len(daily) < 60:
                problem(
                    f"{spec.vertical} merchant_metrics.json: {len(daily)} days (at least 60 required)"
                )
            require_keys(daily, spec.metric_keys, f"{spec.vertical} merchant_metrics.json")
            extra = spec.extra(data, catalog_ids)
            campaigns = load_json(data / "merchant_campaigns.json")["campaigns"]
            [Campaign.model_validate(c) for c in campaigns]
            issues = [
                OrderIssue.model_validate(i)
                for i in load_json(data / "merchant_messages.json")["issues"]
            ]
            for issue in issues:
                if issue.listing_id and issue.listing_id not in catalog_ids:
                    problem(
                        f"{spec.vertical} merchant_messages.json: unknown listing {issue.listing_id}"
                    )
            ok(
                f"{len(daily)} metric days, {extra}, {len(campaigns)} campaigns, {len(issues)} issues validate"
            )
        except Exception as error:
            problem(f"{spec.vertical} {spec.label} fixtures: {error}")


def check_ticketing_fixtures() -> None:
    """Entertainment's storefront-side extras: inventory, venues, wallet tickets, and
    fee rows that sum to the all-in price."""
    print("examples/entertainment/data/ (ticketing)")
    data = REPO_ROOT / "examples" / "entertainment" / "data"
    try:
        catalog = load_json(data / "catalog.json")["products"]
        by_id = {p["product_id"]: p for p in catalog}
        venues = load_json(data / "venues.json")["venues"]
        venue_ids = {v["venue_id"] for v in venues}
        for product in catalog:
            pid, category, attrs = (
                product["product_id"],
                product["category"],
                product.get("attributes", {}),
            )
            if category in ("tickets", "resale"):
                base = "face_price_usd" if category == "tickets" else "seller_price_usd"
                fee_keys = (base, "service_fee_usd", "facility_fee_usd", "processing_fee_usd")
                if missing := [key for key in fee_keys if key not in attrs]:
                    problem(f"entertainment: {pid} missing fee fields {missing}")
                elif abs(sum(float(attrs[k]) for k in fee_keys) - float(product["price"])) >= 0.005:
                    problem(f"entertainment: {pid} fee rows do not sum to the all-in price")
            if category == "resale" and attrs.get("resale_of") not in by_id:
                problem(f"entertainment: {pid} resale_of unknown: {attrs.get('resale_of')}")
            if category == "tickets" and attrs.get("venue_id") not in venue_ids:
                problem(f"entertainment: {pid} references unknown venue")
        inventory = load_json(data / "inventory.json")["inventory"]
        for row in inventory:
            if row["product_id"] not in by_id:
                problem(f"entertainment inventory: unknown product {row['product_id']}")
            if int(row["capacity"]) < 1 or not 0 <= int(row["sold"]) <= int(row["capacity"]):
                problem(f"entertainment inventory: bad capacity/sold on {row['product_id']}")
        if uncounted := set(by_id) - {row["product_id"] for row in inventory}:
            problem(f"entertainment inventory: no row for {sorted(uncounted)}")
        layout_keys = ("section_id", "label", "kind", "x", "y", "w", "h")
        for venue in venues:
            require_keys(venue["sections"], layout_keys, f"venues.json: {venue['venue_id']}")
        tickets = load_json(data / "tickets.json")["tickets"]
        for ticket in tickets:
            if ticket["product_id"] not in by_id:
                problem(f"tickets.json: unknown product {ticket['product_id']}")
        ok(
            f"{len(inventory)} inventory rows, {len(venues)} venues, {len(tickets)} wallet tickets validate"
        )
    except Exception as error:
        problem(f"entertainment ticketing fixtures: {error}")


# ---------------------------------------------------------------------------
# Scripts and packaging
# ---------------------------------------------------------------------------

# The per-vertical tables the verification scripts keep; parsed rather than imported
# because screenshot_tour imports playwright.
SCRIPT_TABLES = (
    ("run_demo.py", "VERTICALS"),
    ("smoke_chat.py", "VERTICAL_TURNS"),
    ("smoke_chat.py", "VERTICAL_APPS"),
    ("smoke_chat.py", "MERCHANT_TURNS"),
    ("screenshot_tour.py", "VERTICALS"),
    ("screenshot_tour.py", "MERCHANT_TOURS"),
)


def top_level_dict_keys(path: Path, name: str) -> set[str] | None:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if isinstance(target, ast.Name) and target.id == name and isinstance(node.value, ast.Dict):
            return {
                k.value
                for k in node.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    return None


def check_verification_wiring() -> None:
    print("scripts/ vertical tables")
    clean = True
    for script, name in SCRIPT_TABLES:
        keys = top_level_dict_keys(REPO_ROOT / "scripts" / script, name)
        if keys is None:
            problem(f"scripts/{script}: no top-level {name} dict found")
            clean = False
        elif missing := set(VERTICALS) - keys:
            problem(f"scripts/{script}: {name} missing verticals: {sorted(missing)}")
            clean = False
    if clean:
        ok(f"all {len(VERTICALS)} verticals in every table")


PACKAGE_DIRS = (
    "commerce-common",
    "shopping-agent/core",
    "shopping-agent/runtime-messages-api",
    "shopping-agent/runtime-agent-sdk",
    "merchant-agent/core",
    "merchant-agent/runtime-messages-api",
    "merchant-agent/runtime-agent-sdk",
)
SIBLING_PACKAGES = {"commerce-common", "shopping-agent-core", "merchant-agent-core"}


def check_package_versions() -> None:
    """One version across the packages, and sibling dependencies pinned to it exactly,
    so a lone install of one package fails instead of resolving a public distribution."""
    print("package versions and cross-package pins")
    versions: dict[str, str] = {}
    edges: list[tuple[str, str, str]] = []
    for package_dir in PACKAGE_DIRS:
        try:
            project = tomllib.loads((REPO_ROOT / package_dir / "pyproject.toml").read_text())[
                "project"
            ]
        except Exception as error:
            problem(f"{package_dir}/pyproject.toml: {error!r}")
            return
        versions[package_dir] = project["version"]
        for dependency in project.get("dependencies", []):
            match = re.match(r"([A-Za-z0-9._-]+)\s*(.*)", dependency.strip())
            if match and match.group(1) in SIBLING_PACKAGES:
                edges.append((package_dir, match.group(1), match.group(2).strip()))
    if len(set(versions.values())) != 1:
        problem(f"package versions disagree: {dict(sorted(versions.items()))}")
        return
    version = next(iter(versions.values()))
    bad = [(d, n, s) for d, n, s in edges if s != f"=={version}"]
    for package_dir, name, specifier in bad:
        problem(
            f"{package_dir}/pyproject.toml: {name} must be pinned =={version}, got {specifier!r}"
        )
    if not bad:
        ok(f"{len(PACKAGE_DIRS)} packages at {version}; {len(edges)} sibling pins exact")


def check_manifests() -> None:
    print("plugin marketplace and Managed Agent manifests")
    marketplace = REPO_ROOT / ".claude-plugin" / "marketplace.json"
    try:
        for plugin in load_json(marketplace).get("plugins", []):
            source = plugin.get("source")
            if source and not (REPO_ROOT / str(source).lstrip("./")).exists():
                problem(f"marketplace.json: source path missing: {source}")
        ok("marketplace.json parses and paths resolve")
    except Exception as error:
        problem(f"marketplace.json: {error}")

    from commerce_common.manifest import ManifestError, resolve

    for role in ROLES:
        manifest = role.agent_dir / "agent.yaml"
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                resolve(manifest)
        except ManifestError as error:
            problem(f"{manifest.relative_to(REPO_ROOT)}: {error}")
        else:
            ok(f"{manifest.relative_to(REPO_ROOT)} resolves to a /v1/agents body")


# ---------------------------------------------------------------------------
# Managed Agent material derived by hand from the libraries
# ---------------------------------------------------------------------------

ANCHOR = re.compile(r"(?:adapted|omitted):\s*\"([^\"]+)\"")


def prompt_rules(prompt: str) -> list[str]:
    """The rule bullets outside the Skills section (skills attach natively when hosted)."""
    rules, in_skills = [], False
    for line in prompt.splitlines():
        if line.startswith("# "):
            in_skills = line.strip() == "# Skills"
        elif not in_skills and line.startswith("- "):
            rules.append(normalize_ws(line[2:]))
    return rules


def check_managed_system_prompts() -> None:
    """Every rule the builder emits appears in system.md, or its divergence is declared
    in the header as ``* adapted: "<anchor>"`` (or ``omitted:``)."""
    print("managed-agents system.md vs the prompt builders")
    for role in ROLES:
        path = role.agent_dir / "system.md"
        document = path.read_text(encoding="utf-8")
        body = normalize_ws(document)
        anchors = [normalize_ws(a) for a in ANCHOR.findall(document)]
        missing = [
            rule
            for rule in prompt_rules(role.static_prompt())
            if rule not in body and not any(anchor in rule for anchor in anchors)
        ]
        for rule in missing:
            problem(
                f"{path.relative_to(REPO_ROOT)}: rule neither present nor declared: {rule[:90]}..."
            )
        if not missing:
            ok(f"{path.relative_to(REPO_ROOT)}: every builder rule present or declared")


def manifest_tools(agent_yaml: Path) -> tuple[set[str], dict[str, str]]:
    """(enabled MCP tool names, custom tool descriptions by name)."""
    mcp_tools: set[str] = set()
    custom: dict[str, str] = {}
    for entry in yaml.safe_load(agent_yaml.read_text(encoding="utf-8")).get("tools", []):
        if entry.get("type") == "mcp_toolset":
            mcp_tools.update(c["name"] for c in entry.get("configs", []) if c.get("enabled"))
        elif entry.get("type") == "custom":
            custom[entry["name"]] = entry.get("description", "")
    return mcp_tools, custom


def backticked_after_preamble(line: str) -> set[str]:
    return set(re.findall(r"`([a-z_]+)`", line.split("):", 1)[-1]))


def readme_line(path: Path, marker: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if marker in line:
            return line
    problem(f"{path.relative_to(REPO_ROOT)}: no '{marker}' line found")
    return ""


def check_managed_readme_tool_lists() -> None:
    """Each managed-agents README lists the deployed tools by hand; its count row and
    two tool bullets, and the server README's table, must match agent.yaml."""
    print("managed-agents READMEs vs agent.yaml")
    for role in ROLES:
        mcp_tools, custom = manifest_tools(role.agent_dir / "agent.yaml")
        before = len(PROBLEMS)
        agent_readme = role.managed / "README.md"
        parent = agent_readme.read_text(encoding="utf-8")
        counts = re.search(rf"(\d+) {role.server_label}, (\d+) presentation tools", parent)
        if counts is None:
            problem(
                f"{role.tree} managed README: no '<N> {role.server_label}, <N> presentation tools' row"
            )
        elif (int(counts.group(1)), int(counts.group(2))) != (len(mcp_tools), len(custom)):
            problem(
                f"{role.tree} managed README counts disagree with agent.yaml ({len(mcp_tools)} / {len(custom)})"
            )
        marker = f"**{role.server_label.split()[0].capitalize()} tools**"
        if (listed := backticked_after_preamble(readme_line(agent_readme, marker))) != mcp_tools:
            problem(
                f"{agent_readme.relative_to(REPO_ROOT)}: {role.server_label} list != agent.yaml "
                f"(missing {sorted(mcp_tools - listed)}, extra {sorted(listed - mcp_tools)})"
            )
        listed = backticked_after_preamble(readme_line(agent_readme, "**Presentation tools**"))
        if listed != set(custom):
            problem(
                f"{agent_readme.relative_to(REPO_ROOT)}: presentation list != agent.yaml "
                f"(missing {sorted(set(custom) - listed)}, extra {sorted(listed - set(custom))})"
            )
        server_readme = (role.managed / role.server_dir / "README.md").read_text(encoding="utf-8")
        rows = set(re.findall(r"^\| `([a-z_]+)` \|", server_readme, re.MULTILINE))
        if rows != mcp_tools:
            problem(
                f"{role.tree} {role.server_dir}/README.md tool table != agent.yaml "
                f"(missing {sorted(mcp_tools - rows)}, extra {sorted(rows - mcp_tools)})"
            )
        if len(PROBLEMS) == before:
            ok(f"{role.tree}: README counts, tool lists, and server table match agent.yaml")


def check_managed_custom_tool_descriptions() -> None:
    """The manifests' custom tool descriptions are the registries', whitespace aside."""
    print("managed-agents custom tool descriptions vs the registries")
    for role in ROLES:
        registry = role.registry_descriptions()
        _, custom = manifest_tools(role.agent_dir / "agent.yaml")
        before = len(PROBLEMS)
        for name, description in custom.items():
            if name not in registry:
                problem(f"{role.tree} agent.yaml: custom tool {name} has no registry contract")
            elif normalize_ws(description) != normalize_ws(registry[name]):
                problem(f"{role.tree} agent.yaml: {name} description drifted from the registry")
        if len(PROBLEMS) == before:
            ok(f"{role.tree}: {len(custom)} custom tool descriptions match the registry")


CHECKS = (
    check_skills,
    check_storefront_fixtures,
    check_ticketing_fixtures,
    check_merchant_fixtures,
    check_verification_wiring,
    check_package_versions,
    check_manifests,
    check_managed_system_prompts,
    check_managed_readme_tool_lists,
    check_managed_custom_tool_descriptions,
)


def main() -> int:
    for check in CHECKS:
        check()
        print()
    if PROBLEMS:
        print(f"check.py: {len(PROBLEMS)} problem(s)")
        return 1
    print("check.py: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
