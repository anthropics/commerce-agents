# Feed analysis

Inspected locally on 2026-10-15. The file is `feed/catalog_full_rei_2026-10-13-093321.zip` (96 MB). `file` reports a Zip archive, deflate. Five UTF-8 pipe-delimited members, zip timestamp 2026-10-13 12:36, about 617 MB uncompressed. No per-record update timestamp. The filename is the snapshot id. Counts below are from a full local scan. Product names are not quoted.

## Files

| File | Rows | Shape |
|---|---|---|
| `product_full_….txt` | 131,341 | 17 columns, 0 malformed, 0 duplicate ids |
| `category_full_….txt` | 6,581 | 2,475 unique category ids |
| `product_in_category_….txt` | 602,342 | 225,201 unique pairs |
| `product_attribute_….txt` | 131,349 | 47 columns, 0 malformed, 0 duplicate ids |
| `sku_full_….txt` | 6,284,229 | EAV, 0 malformed |

Product columns: `product_id`, `name`, `product_parent_id`, `price`, `recommendable`, `image_url`, `link_url`, `rating`, `num_reviews`, `brand`, `description`, `sale_price`, `start_date`, `sale_price_min`, `sale_price_max`, `list_price_min`, `list_price_max`.

Category columns: `category_id`, `parent_id`, `name`, `category_link_url`, `category_image_url`.

Membership columns: `category_id`, `product_id`.

Attribute file: one wide row per product. Header names are `attribute.<name>`, including several UUID-named columns.

SKU columns: `product_id`, `sku_id`, `attr_name`, `attr_value`. The only attribute names are `link_url`, `image_url`, `color`, `color-family`, and `size`.

## How products and SKUs relate

`product_parent_id` is empty on every product row. The family is the product id. The variant is the SKU id. 1,273,229 distinct SKUs sit on 138,822 product ids. 199 products have no SKU. 7,680 SKU product ids are absent from the product file (109,264 rows). Drop those orphan SKUs.

SKUs per product: minimum 1, median 4, mean 9.17, p99 80, maximum 686. 2,365 products have more than 60 SKUs. A SKU row has no price and no stock. Size and color are the variant axes. Copy the product price onto each variant so the cart can price a variant id. Say, in the domain note, that this feed does not show a different price per size or color.

## Field inventory

Fill rates are of the rows in that file.

- `product_id`, `name`, `price`, `recommendable`, `image_url`, `link_url`: 100%.
- `brand`: 99.31% (2,083 distinct brands; 317 appear once).
- `description`: effectively 100% (4 empty). Median length 190 characters. Almost all are 80–499 characters. One description exceeds 1,500. No HTML tags detected. 17,747 names or descriptions contain non-ASCII.
- `rating`: 72.93%, range 0–5, none outside that range.
- `num_reviews`: 99.37%. Median 2, maximum 55,871, 40,475 zeros. No review text.
- `sale_price`: 5.93% (7,782). Where both prices parse, sale is below list on 6,759 and equal on 1,021. Never above.
- `start_date`: 0%. `sale_price_min` and `sale_price_max`: 0.89%. `list_price_min` and `list_price_max`: 0.93%.
- `product_parent_id`: 0%.
- Images: 131,233 paths start with `/media`, 108 with `/dam`. All relative.
- Links: all relative. 21,441 use a `/rei-garage/product` prefix. Map that as `channel=garage` and do not invent a program name.
- Price: median 85, maximum 8999.99. 19 rows are `-1.0`. 76 are `0`. Drop those 95 from the shoppable index and list them in the quality report. Do not display them as free.
- `recommendable`: `true` on 26,883, `false` on 104,458. Assumption: a merchandising flag, used as a ranking boost, not a hard hide, until the client defines it.
- `attribute.features`: 99.87%. Tilde-separated. Median length 413. No HTML detected.
- `attribute.best-use`: 98.62%. First tokens are led by Casual, Hiking, Multisport, Snowsports, Camping, Running, Backpacking, Cycling.
- `attribute.color`: 90.34%. Often multi-value (`~`).
- `attribute.size`: 79.64% on the product row, also multi-value. Per-SKU size lives in the SKU file.
- `attribute.gender`: 73.37%. Values include Women's, Men's, Unisex, Kids', and infant and toddler splits.
- `attribute.coupon-price`: about 90%. Among positive pairs the median coupon/list ratio is 0.521 (p10 0.293, p90 0.75). It equals list on 51 rows. Where sale and coupon both exist (6,506), they match on 18 and coupon is lower on 6,488. Judgment: this is not the customer price. Quarantine it.
- `attribute.on-sale`: boolean, `true` on 20 rows. Do not use it. It does not match `sale_price`.
- `attribute.clearance`: `true` on 3,033. `attribute.outlet`: `true` on 40,843. Outlet does not equal the garage link count. `attribute.kids`: `true` on 7,864.
- `attribute.value_tier`: 57.79%. Values GOOD, BETTER, BEST, ON RAMP.
- `attribute.BadgeTitle`: 37.92%. Values include “New arrival”, “Top Rated”, “Staff Pick”, “Just for Members”, “REI Outlet”. Show as labels. Do not explain a membership benefit from the badge.
- `attribute.MARGIN` and `attribute.margin-dollars`: 36.57%. Quarantine with `market_id`, `NavCatID`, `TaxcatID`, `Subprogram`, and every UUID-named column.
- `attribute.courses`: `false` on 131,241, `true` on 108. Exclude `true` from product search until verified.
- Category parents: 1,431 ids repeat. 521 repeats are identical. 910 conflict, 904 of them on `parent_id`. Depth of a last-write tree is 1–6, 35 roots, no cycles. 565 categories have no products. Every known product is in at least one category. Median categories per product is 3, maximum 95. 26 membership rows point at 8 unknown product ids.
- Eight attribute rows have product ids absent from the product file. Every product has an attribute row.

No GTIN, MPN, currency column, stock, store, pickup ETA, shipping fee, review text, or delta file.

## What the feed can and cannot support

It can support text search, category filters, brand, gender, activity, color, size, list versus sale price, rating counts, relative PDP links, a color and size matrix, and merchandising labels.

It cannot support live or store-level stock, pickup time, shipping price, review snippets, GTIN, per-variant price, price history, orders, identity, or an incremental file. It is one snapshot.

## Adapter

Normalized tables: `product`, `variant`, `category`, `product_category`, `attribute`, `feed_run`. Family id is `product_id`. Variant id is `sku_id`. Options are the distinct size and color values. Price and sale price live on the product and are copied onto variants. Do not set `in_stock`.

A mapping file per client names the delimiter, encoding, file roles, column map, multi-value separator (`~`), relative URL base, quarantined columns, sentinel prices (`-1` and `0`), and the family key. A second client changes the YAML, not the loader.

The profiler runs on every drop and writes row counts, fill rates, type checks, duplicate keys, orphan keys, price sentinels, an HTML scan, a SKU histogram, and a diff against the previous run. It exits non-zero when keys break or a price column changes meaning.

Refresh is a full-snapshot upsert by id. Products missing from the new snapshot are hidden after one successful load, not deleted. There is no delta to parse. A content hash skips unchanged rows. The search index rebuilds changed ids.

URL base is an assumption: `https://www.rei.com` plus the relative path. Verify before the widget requests images.

Families over 60 SKUs split by color, which is the leading option named in [docs/backends.md](../backends.md). Color is the leading option because size runs are longer on footwear and apparel. Judgment, from the SKU histogram, not from a REI rule.
