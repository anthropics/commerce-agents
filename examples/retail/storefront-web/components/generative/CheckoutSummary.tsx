// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useId } from "react";
import { formatMoney, useCatalogIndex, safeHandoffs } from "web-shared";
import { fetchProducts } from "@/lib/api";
import type { CheckoutPayload, Product } from "@/lib/types";
import { STORE_POLICY } from "@/lib/storePolicy";
import { DeliveryPromise, ProductImage } from "../ProductTile";

const CHECKOUT_POLICY_ENABLED = process.env.NEXT_PUBLIC_CHECKOUT_POLICY_ENABLED !== "false";

export default function CheckoutSummary({ payload }: { payload: CheckoutPayload }) {
  const cart = payload.cart;
  const handoffs = safeHandoffs(payload.handoffs);
  // Several summaries can coexist across turns, so the describedby id is per card.
  const handoffNoteId = useId();
  // Summary lines carry only title, price, and quantity; the catalog supplies delivery
  // dates and thumbnails.
  const catalog = useCatalogIndex(fetchProducts);
  // The policy says "over" the threshold, so a cart at exactly the threshold is not free.
  const freeShipping = CHECKOUT_POLICY_ENABLED && cart.subtotal > STORE_POLICY.freeShippingThreshold;
  return (
    <section data-checkout-card className="rounded-2xl border-2 border-(--accent) bg-(--card) p-4 shadow-(--shadow-sm)">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[15px] font-semibold text-(--ink)">
          {CHECKOUT_POLICY_ENABLED ? "Ready to check out" : "Checkout summary"}
        </h3>
        <div className="flex items-center gap-1.5">
          <span className="whitespace-nowrap rounded-full border border-(--line) bg-(--well)/60 px-2.5 py-0.5 text-[11px] font-semibold text-(--ink-soft)">
            Not charged
          </span>
          {payload.fulfillment_method ? (
            <span className="rounded-full bg-(--accent-soft) px-2.5 py-0.5 text-[13px] font-semibold capitalize text-(--ink)">
              {payload.fulfillment_method}
            </span>
          ) : null}
        </div>
      </div>
      {payload.note ? <p className="mt-1 text-[13px] text-(--ink-soft)">{payload.note}</p> : null}
      <div className="mt-3 space-y-2 rounded-lg bg-(--well)/60 p-3 text-sm">
        {cart.items.map((item) => {
          const product: Product =
            catalog[item.product_id] ?? {
              product_id: item.product_id,
              title: item.title,
              price: item.price,
            };
          return (
            <div key={item.product_id} className="flex items-center gap-2.5">
              <ProductImage
                product={product}
                className="h-10 w-10 shrink-0 rounded-lg !text-xl"
              />
              <div className="min-w-0 flex-1">
                <div className="flex justify-between gap-2">
                  <span className="line-clamp-1 text-(--ink)" title={item.title}>
                    {item.title} × {item.quantity}
                  </span>
                  <span className="shrink-0 text-(--ink)">{formatMoney(item.line_total)}</span>
                </div>
                <DeliveryPromise product={product} />
              </div>
            </div>
          );
        })}
        <div className="flex justify-between border-t border-(--line) pt-1.5 text-(--ink)">
          <span>Subtotal</span>
          <span>{formatMoney(cart.subtotal, cart.currency)}</span>
        </div>
        <div className="flex justify-between gap-2 text-(--ink)">
          <span>
            Shipping{" "}
            {CHECKOUT_POLICY_ENABLED ? (
              <span className="text-[13px] text-(--ink-soft)">
                standard · {STORE_POLICY.standardShippingEta}
              </span>
            ) : null}
          </span>
          <span className={freeShipping ? "font-medium text-(--ok)" : "text-(--ink)"}>
            {freeShipping ? "Free" : "Calculated in checkout"}
          </span>
        </div>
        {CHECKOUT_POLICY_ENABLED &&
        !freeShipping &&
        STORE_POLICY.freeShippingThreshold - cart.subtotal > 0 ? (
          <div className="flex justify-between text-[13px] text-(--ink-soft)">
            <span>
              Add {formatMoney(STORE_POLICY.freeShippingThreshold - cart.subtotal)} more to
              unlock free shipping
            </span>
          </div>
        ) : null}
        <div className="flex justify-between text-(--ink)">
          <span>Tax</span>
          <span>Calculated at checkout</span>
        </div>
        <div className="flex justify-between border-t border-(--line) pt-1.5 text-base font-bold text-(--ink)">
          <span>Estimated total</span>
          <span>{formatMoney(cart.subtotal, cart.currency)}</span>
        </div>
        <p className="text-[11px] leading-snug text-(--ink-soft)">
          {freeShipping ? "Before tax" : "Before shipping and tax"}; the final total appears in
          checkout.
        </p>
      </div>
      {CHECKOUT_POLICY_ENABLED ? (
        <p className="mt-2 text-[11px] text-(--ink-soft)">{STORE_POLICY.returnsLine}</p>
      ) : null}
      {handoffs.length ? (
        // The backend named where payment happens (a hosted checkout URL, or one per seller).
        <div className="mt-3 flex flex-col gap-2">
          {handoffs.map((h) => (
            <a
              key={h.url}
              href={h.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-describedby={handoffNoteId}
              className="w-full rounded-xl bg-(--accent) py-2.5 text-center text-sm font-bold text-(--ink)"
            >
              {h.label ?? (h.seller ? `Continue to checkout with ${h.seller}` : "Continue to checkout")}
            </a>
          ))}
        </div>
      ) : (
        // Disabled so assistive tech is not offered a focusable no-op.
        <button
          disabled
          aria-disabled
          aria-describedby={handoffNoteId}
          className="mt-3 w-full cursor-not-allowed rounded-xl bg-(--accent) py-2.5 text-sm font-bold text-(--ink) opacity-90"
          title="Nothing is charged here. Payment happens when you check out."
        >
          {CHECKOUT_POLICY_ENABLED ? "Continue to checkout" : "Checkout handoff not connected"}
        </button>
      )}
      <p id={handoffNoteId} className="mt-2 text-center text-[11px] text-(--ink-soft)/80">
        {CHECKOUT_POLICY_ENABLED
          ? "Nothing is charged here. Payment happens when you check out."
          : "Review only. Payment and order placement are not connected."}
      </p>
    </section>
  );
}
