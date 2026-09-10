# Proposal: a backend conformance suite

## The gap

`StorefrontBackend` and `MerchantBackend` are the integration a merchant writes. Their
contract lives in docstrings and `docs/backends.md`, and nothing in the repository checks
an implementation against it. The example mocks pass the examples' tests, yet running the
contract as tests against them found a dozen departures (PRs #8 to #18 fix the ones with
a one-line answer). A merchant's adapter has no such check at all.

## What this adds

`commerce_common.testing.conformance`: pytest mixins (`StorefrontConformance`,
`MerchantConformance`, `ShoppingExecutorConformance`, `MerchantExecutorConformance`)
driven by a small target descriptor: a factory per backend and the fixture facts the
statements need (a plain product, a family, an out-of-stock variant, a customer with
orders, an operator). Each test carries a statement id from a `spec.yaml` of numbered,
one-sentence normative statements distilled from the docstrings and `docs/safety.md`,
so the report reads as spec coverage. A fact the target lacks skips the statement and
says so; a statement a target is known not to meet is declared once and runs as a strict
xfail, so a suite stays green while a gap is open and turns red the day it closes.

A target file is thirty lines. `docs/backends.md` gains a section "Run this against your
backend" showing it.

## Status of the reference implementation

Written and run against the retail, travel, telecom and entertainment mocks and against
a Medusa v2 adapter, offline and live: 130 statements, 33 of them the backend contract
proper. This PR is the design note; the code follows once the shape is agreed.

## Questions for maintainers

1. Package location: `commerce_common.testing` or a separate `commerce-conformance`
   distribution?
2. Should the spec statements live next to `docs/backends.md` so the prose and the
   statements are edited together?
