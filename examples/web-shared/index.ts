// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/**
 * Every app imports `base.css`; a storefront defines `.chip`, `.user-bubble`, and `.btn-primary`
 * in its globals.css, a merchant portal imports `portal/portal.css`.
 */

export { AgentApi } from "./api";
export { useCatalogIndex } from "./catalog";
export { type Prefill } from "./Composer";
export * from "./format";
export { type GenerativeBlockProps, UnknownBlock } from "./generative";
export { Icon, type IconName } from "./icons";
export { Inspector } from "./Inspector";
export { AssistantPanel } from "./portal/AssistantPanel";
export { AssistantRail } from "./portal/AssistantRail";
export * from "./portal/cards";
export * from "./portal/home";
export { type ChangeAction, type MerchantChat, useMerchantChat } from "./portal/merchant";
export { type PortalNavItem, PortalShell } from "./portal/Shell";
export type * from "./protocol";
export { QuotedAsData } from "./QuotedAsData";
export { useResource } from "./resource";
export { type Session, useSession } from "./session";
export { AskLink, BagPanel, CheckoutButton, RemoveLink, Stepper, TotalRow } from "./storefront/bag";
export { Chat } from "./storefront/Chat";
export { useStoreFrame } from "./storefront/frame";
export { Greeting, HomeSection, MoreLink, type Profile, type Starter, Starters } from "./storefront/home";
export { ArrivingPanel, estimateOf, isOpen, ORDER_NOUNS, type OrderNouns, orderStatusLabel, OrdersView, OrderStatusPill, upcoming } from "./storefront/orders";
export { StorePage, StoreShell, type StoreView } from "./storefront/Shell";
export { Suggestions } from "./Suggestions";
export { ActivityLine } from "./Transcript";
export { type AgentTurn, useAgentTurn } from "./turn";
export * from "./ui";
export * from "./webmcp";
