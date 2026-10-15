# Roadmap and risks

Estimates are for one senior engineer plus design review. They are not a quote.

## Phases

Phase 0, about two weeks. Profiler, mapping file, quarantined columns, theme extract, human review, and a gitignore for `feed/`. Milestone: a quality report and an approved `theme.json`.

Phase 1, about six weeks. Normalized load, lexical search, `StorefrontBackend`, the stock-omission flag, the launcher and panel, cart handoff, and the fifty evals. Milestone: the demo below.

Phase 2, about six weeks. Semantic merge, the merchant zero-result report, a policy corpus if the client supplies one, consented memory, and a store API if one exists.

Do not build yet: payments, image search, voice, proactive pushes, auto-buy, a copied Cedar component, or the Stuart font.

## Work and layout

Add `ingest/` for the client-agnostic loader and profiler, `clients/rei/` for the mapping, theme, and backend, and `widget/` for the Next.js embed. Do not replace `examples/retail`. Update `CLAUDE.md` when those packages exist.

Dependencies already in the repo: Python 3.11, the shopping packages, and the Next.js workspace. New ones, when the packages land: a Postgres driver for the index, and a headless browser for theme extraction. Pin them in the package that uses them.

## Risks

Coupon-price read as the customer price. Mitigation: quarantine the column, and a test that the fence never contains it.

`in_stock` defaulting to true. Mitigation: `catalog_reports_stock=False` omits the field and adds one prompt line.

Conflicting category parents. Mitigation: majority parent, otherwise flag the id in the quality report.

Snapshot staleness. Mitigation: the as-of line, and a banner after 24 hours.

Prompt injection in features. Mitigation: the existing fence, plus stripping control characters at ingest, plus query 49.

Unlicensed fonts and logo. Mitigation: the fallback stacks, and no logo file.

Hotlinked images. Mitigation: listed as a terms question. Do not decide it in code.

No order or stock API. Mitigation: orders switched off, and the card says availability is not in this catalog.

## Demo, ten minutes

1. Drop the zip and the site URL.
2. Show the profile counts and the quarantined price column.
3. Show the theme review, including the fallback fonts.
4. Ask for a car-camping kit under a budget.
5. Open a comparison of two of the picks.
6. Add a sized variant.
7. Show the as-of price and the missing-availability line.
8. Stage checkout and show that no order was created.
9. Show a poisoned description being ignored.
10. End on the zero-result list.

## Least sure

The meaning of `coupon-price`. The ratio to list is stable and it is not `sale_price`, but the name could still be a member price the client expects to show. Hiding it is the safe default, and it is the first decision in the README. Second: whether the public Cedar tokens match the live site, because the homepage HTML download timed out. Third: competitor checkout details, which moved during 2026 and will move again.
