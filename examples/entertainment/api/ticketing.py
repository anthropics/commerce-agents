# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The ticketing state machine: timed holds behind the cart, ``remaining()`` derived from
capacity, sales, holds, and offer reservations, FIFO waitlists whose return offers roll on
expiry, and reversible transfers. Nothing here charges anything. Expiry is swept lazily at
the top of every public method, so tests drive it through the injected clock."""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from shopping_agent.backend import Unavailable

HOLD_TTL_S = 480
OFFER_CLAIM_WINDOW_S = 600
MAX_TICKETS_PER_EVENT = 8  # held tickets per event per session
BARCODE_ROTATION_S = 60
# Once a real fan queues behind them, seeded fans leave the line one per interval.
SIM_FAN_DEPART_INTERVAL_S = 20


class TicketingError(ValueError):
    """A rule violation; the message is safe to show the caller."""


class SoldOutError(TicketingError, Unavailable):
    """Not enough open inventory to hold the requested quantity. ``Unavailable`` so the
    executor relays it to the model with the ids instead of reporting an outage."""


class HoldLimitError(TicketingError):
    """The per-event ticket cap would be exceeded."""


class OwnershipError(TicketingError):
    """The caller does not own the hold/ticket/offer it is acting on."""


class NotFoundError(TicketingError):
    """No such hold/ticket/offer/transfer."""


class StateError(TicketingError):
    """The object exists but is in the wrong state (window closed, not sold out, ...)."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Hold:
    hold_id: str
    session_id: str
    user_id: str
    product_id: str
    quantity: int
    expires_at: datetime


@dataclass
class WaitlistEntry:
    user_id: str
    session_id: str
    product_id: str
    quantity: int
    joined_at: datetime
    # Seeded fans hold a place in line, depart on a schedule, and pass on offers.
    simulated: bool = False
    departs_at: datetime | None = None


@dataclass
class ReturnOffer:
    offer_id: str
    product_id: str
    user_id: str
    quantity: int
    expires_at: datetime
    status: str = "open"  # open | claimed | expired


@dataclass
class Ticket:
    ticket_id: str
    owner_id: str
    product_id: str
    order_id: str
    seat: str
    status: str = "active"  # active | transfer_pending


@dataclass
class Transfer:
    transfer_id: str
    ticket_ids: list[str]
    from_user_id: str
    recipient: str
    initiated_at: datetime
    status: str = "pending"  # pending | cancelled


@dataclass
class _InventoryRow:
    capacity: int
    sold: int


@dataclass
class Notification:
    """Something to tell a user; the host queues it as an app event for their next turn."""

    user_id: str
    text: str


class TicketingEngine:
    """The inventory and reservation state behind ``MockTicketing``."""

    def __init__(
        self,
        inventory: dict[str, dict[str, int]],
        tickets: list[Ticket],
        event_of: Callable[[str], str],
        now: Callable[[], datetime] = _utcnow,
        sold_together_of: Callable[[str], int] = lambda _pid: 1,
        waitlist_seed: dict[str, int] | None = None,
    ) -> None:
        self._now = now
        self._event_of = event_of
        self._sold_together_of = sold_together_of
        self._rows: dict[str, _InventoryRow] = {
            pid: _InventoryRow(capacity=int(row["capacity"]), sold=int(row["sold"]))
            for pid, row in inventory.items()
        }
        self._holds: dict[str, Hold] = {}
        self._waitlists: dict[str, list[WaitlistEntry]] = {}
        for product_id, count in (waitlist_seed or {}).items():
            self._waitlists[product_id] = [
                WaitlistEntry(
                    user_id=f"sim-fan-{product_id}-{index}",
                    session_id="",
                    product_id=product_id,
                    quantity=2,
                    joined_at=self._now(),
                    simulated=True,
                )
                for index in range(1, max(0, count) + 1)
            ]
        self._offers: dict[str, ReturnOffer] = {}
        self._transfers: dict[str, Transfer] = {}
        self._tickets: dict[str, Ticket] = {t.ticket_id: t for t in tickets}
        self._counter = itertools.count(1)
        self._pending_notifications: list[Notification] = []

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._counter):04d}"

    def now(self) -> datetime:
        """The engine's clock, which tests replace; every countdown reads it here."""
        return self._now()

    def seconds_until(self, expires_at: datetime) -> int:
        return max(0, int((expires_at - self._now()).total_seconds()))

    # ------------------------------------------------------------------
    # Expiry sweep (lazy — runs at the top of every public method)
    # ------------------------------------------------------------------

    def sweep(self) -> None:
        now = self._now()
        for hold_id in [h.hold_id for h in self._holds.values() if h.expires_at <= now]:
            del self._holds[hold_id]
        # Scheduled simulated-fan departures: everyone behind them moves up for real.
        for entries in self._waitlists.values():
            entries[:] = [
                e
                for e in entries
                if not (e.simulated and e.departs_at is not None and e.departs_at <= now)
            ]
        for offer in list(self._offers.values()):
            if offer.status == "open" and offer.expires_at <= now:
                offer.status = "expired"
                # The freed quantity rolls to the next fan in line, if any; otherwise it
                # simply stops being reserved and shows up in remaining(). The holder hears
                # first, and hears which of the two happened.
                position = len(self._pending_notifications)
                rolled = self._offer_next(offer.product_id, offer.quantity)
                where = (
                    "the tickets went to the next fan on the waitlist"
                    if rolled is not None
                    else "the tickets are back on sale"
                )
                self._pending_notifications.insert(
                    position,
                    Notification(
                        user_id=offer.user_id,
                        text=f"The return offer for {offer.product_id} expired unclaimed; {where}.",
                    ),
                )

    def collect_notifications(self) -> list[Notification]:
        """Drain proactive notifications (return offers, expirations) for delivery."""
        self.sweep()
        pending, self._pending_notifications = self._pending_notifications, []
        return pending

    # ------------------------------------------------------------------
    # Truthful scarcity
    # ------------------------------------------------------------------

    def remaining(self, product_id: str) -> int:
        """Open inventory right now: capacity − sold − live holds − open offer
        reservations. This is the only source of any 'N left' number."""
        self.sweep()
        row = self._rows.get(product_id)
        if row is None:
            return 0
        held = sum(h.quantity for h in self._holds.values() if h.product_id == product_id)
        reserved = sum(
            o.quantity
            for o in self._offers.values()
            if o.product_id == product_id and o.status == "open"
        )
        return max(0, row.capacity - row.sold - held - reserved)

    def capacity(self, product_id: str) -> int:
        row = self._rows.get(product_id)
        return row.capacity if row else 0

    def sold(self, product_id: str) -> int:
        row = self._rows.get(product_id)
        return row.sold if row else 0

    def waitlist_depth(self, product_id: str) -> int:
        self.sweep()
        return len(self._waitlists.get(product_id, []))

    def add_capacity(self, product_id: str, quantity: int) -> None:
        """Put released allocation on sale; it counts in ``remaining()`` at once."""
        row = self._rows.get(product_id)
        if row is None:
            raise NotFoundError(f"unknown product {product_id}")
        if quantity < 1:
            raise StateError("quantity must be at least 1")
        row.capacity += quantity

    # ------------------------------------------------------------------
    # Holds (the cart's backing store)
    # ------------------------------------------------------------------

    def _session_event_total(self, session_id: str, event_id: str) -> int:
        return sum(
            h.quantity
            for h in self._holds.values()
            if h.session_id == session_id and self._event_of(h.product_id) == event_id
        )

    def create_hold(self, session_id: str, user_id: str, product_id: str, quantity: int) -> Hold:
        """Create or grow the session's hold on a tier; either restarts the timer."""
        self.sweep()
        if product_id not in self._rows:
            raise NotFoundError(f"unknown product {product_id}")
        if quantity < 1:
            raise StateError("quantity must be at least 1")
        self._check_whole_sets(product_id, quantity)
        event_id = self._event_of(product_id)
        if self._session_event_total(session_id, event_id) + quantity > MAX_TICKETS_PER_EVENT:
            raise HoldLimitError(
                f"per-event limit is {MAX_TICKETS_PER_EVENT} tickets; the hold was not changed"
            )
        if self.remaining(product_id) < quantity:
            left = self.remaining(product_id)
            if left == 0:
                raise SoldOutError(
                    "this tier is sold out — the waitlist is the way in, and joining it "
                    "happens on the event page, not in chat; point the customer there, "
                    "or show any fan resale listings for the tier"
                )
            raise SoldOutError(f"only {left} left in this tier — reduce the quantity")
        existing = self._hold_for(session_id, product_id)
        expires = self._now() + timedelta(seconds=HOLD_TTL_S)
        if existing is not None:
            existing.quantity += quantity
            existing.expires_at = expires
            return existing
        hold = Hold(
            hold_id=self._next_id("hold"),
            session_id=session_id,
            user_id=user_id,
            product_id=product_id,
            quantity=quantity,
            expires_at=expires,
        )
        self._holds[hold.hold_id] = hold
        return hold

    def _check_whole_sets(self, product_id: str, quantity: int) -> None:
        """Raise when ``quantity`` would split a sold-together set, which would strand an
        unsellable seat."""
        unit = max(1, self._sold_together_of(product_id))
        if quantity % unit:
            raise StateError(
                f"this listing is sold in sets of {unit} — request a multiple of {unit}"
            )

    def _hold_for(self, session_id: str, product_id: str) -> Hold | None:
        for hold in self._holds.values():
            if hold.session_id == session_id and hold.product_id == product_id:
                return hold
        return None

    def set_hold_quantity(self, session_id: str, product_id: str, quantity: int) -> None:
        """Zero releases the hold; growth is checked against the tier's inventory and the
        per-event cap, as ``create_hold`` checks it; any change restarts the timer."""
        self.sweep()
        hold = self._hold_for(session_id, product_id)
        if hold is None:
            return
        if quantity <= 0:
            del self._holds[hold.hold_id]
            return
        self._check_whole_sets(product_id, quantity)
        grow = quantity - hold.quantity
        event_total = self._session_event_total(session_id, self._event_of(product_id))
        if grow > 0 and event_total + grow > MAX_TICKETS_PER_EVENT:
            raise HoldLimitError(
                f"per-event limit is {MAX_TICKETS_PER_EVENT} tickets; the hold was not changed"
            )
        if grow > 0 and self.remaining(hold.product_id) < grow:
            raise SoldOutError(f"only {self.remaining(hold.product_id)} more left in this tier")
        hold.quantity = quantity
        hold.expires_at = self._now() + timedelta(seconds=HOLD_TTL_S)

    def release_hold(self, session_id: str, product_id: str) -> None:
        hold = self._hold_for(session_id, product_id)
        if hold is not None:
            del self._holds[hold.hold_id]

    def release_hold_by_id(self, hold_id: str, user_id: str) -> None:
        """Release by id; the caller must own the hold."""
        self.sweep()
        hold = self._holds.get(hold_id)
        if hold is None:
            raise NotFoundError("no such hold (it may have expired)")
        if hold.user_id != user_id:
            raise OwnershipError("this hold belongs to a different customer")
        del self._holds[hold_id]

    def release_session(self, session_id: str) -> None:
        for hold_id in [h.hold_id for h in self._holds.values() if h.session_id == session_id]:
            del self._holds[hold_id]

    def holds_for_session(self, session_id: str) -> list[Hold]:
        self.sweep()
        return sorted(
            (h for h in self._holds.values() if h.session_id == session_id),
            key=lambda h: h.hold_id,
        )

    def holds_for_user(self, user_id: str) -> list[Hold]:
        self.sweep()
        return sorted(
            (h for h in self._holds.values() if h.user_id == user_id), key=lambda h: h.hold_id
        )

    # ------------------------------------------------------------------
    # Waitlist + return offers
    # ------------------------------------------------------------------

    def join_waitlist(self, user_id: str, session_id: str, product_id: str, quantity: int) -> int:
        """Join a sold-out tier's waitlist and return the 1-based position; rejoining
        updates the quantity and keeps the place."""
        self.sweep()
        if product_id not in self._rows:
            raise NotFoundError(f"unknown product {product_id}")
        if self.remaining(product_id) > 0:
            raise StateError("this tier still has open tickets — hold them instead")
        quantity = max(1, min(quantity, MAX_TICKETS_PER_EVENT))
        self._check_whole_sets(product_id, quantity)
        entries = self._waitlists.setdefault(product_id, [])
        for position, entry in enumerate(entries, start=1):
            if entry.user_id == user_id:
                entry.quantity = quantity
                return position
        entries.append(
            WaitlistEntry(
                user_id=user_id,
                session_id=session_id,
                product_id=product_id,
                quantity=quantity,
                joined_at=self._now(),
            )
        )
        # A real fan is waiting, so the seeded fans ahead start departing.
        pending = 0
        for entry in entries:
            if entry.simulated and entry.departs_at is None:
                pending += 1
                entry.departs_at = self._now() + timedelta(
                    seconds=SIM_FAN_DEPART_INTERVAL_S * pending
                )
        return len(entries)

    def waitlist_entries_for(self, user_id: str) -> list[tuple[WaitlistEntry, int]]:
        self.sweep()
        found: list[tuple[WaitlistEntry, int]] = []
        for entries in self._waitlists.values():
            for position, entry in enumerate(entries, start=1):
                if entry.user_id == user_id:
                    found.append((entry, position))
        return found

    def record_return(self, product_id: str, quantity: int) -> ReturnOffer | None:
        """Free returned tickets; the first real fan on the waitlist, if any, gets a
        claim-window offer on them, which is returned."""
        self.sweep()
        row = self._rows.get(product_id)
        if row is None:
            raise NotFoundError(f"unknown product {product_id}")
        quantity = min(quantity, row.sold)
        if quantity < 1:
            raise StateError("no sold tickets to return for this tier")
        row.sold -= quantity
        return self._offer_next(product_id, quantity)

    def _offer_next(self, product_id: str, quantity: int) -> ReturnOffer | None:
        entries = self._waitlists.get(product_id, [])
        unit = max(1, self._sold_together_of(product_id))
        while entries:
            if entries[0].simulated:
                entries.pop(0)
                continue
            # An offer is for whole sets, as a hold is; a return smaller than a set goes
            # back to open inventory and the fan keeps their place.
            offered = min(quantity, entries[0].quantity) // unit * unit
            if offered < 1:
                return None
            entry = entries.pop(0)
            offer = ReturnOffer(
                offer_id=self._next_id("offer"),
                product_id=product_id,
                user_id=entry.user_id,
                quantity=offered,
                expires_at=self._now() + timedelta(seconds=OFFER_CLAIM_WINDOW_S),
            )
            self._offers[offer.offer_id] = offer
            self._pending_notifications.append(
                Notification(
                    user_id=entry.user_id,
                    text=(
                        f"Return offer: {offer.quantity} ticket(s) for {product_id} just became "
                        f"available for this customer from the waitlist (offer {offer.offer_id}). "
                        f"The claim window closes in {OFFER_CLAIM_WINDOW_S // 60} minutes."
                    ),
                )
            )
            return offer
        return None  # no waitlist: the freed quantity is open inventory again

    def offers_for(self, user_id: str) -> list[ReturnOffer]:
        self.sweep()
        return [o for o in self._offers.values() if o.user_id == user_id and o.status == "open"]

    def claim_offer(self, offer_id: str, user_id: str, session_id: str) -> Hold:
        """Convert the offer into an ordinary hold; only the fan it was made to may."""
        self.sweep()
        offer = self._offers.get(offer_id)
        if offer is None:
            raise NotFoundError("no such offer")
        if offer.user_id != user_id:
            raise OwnershipError("this offer was made to a different customer")
        if offer.status != "open":
            raise StateError(f"this offer is no longer open (status: {offer.status})")
        # Claimed first, so the reservation counts as open inventory for the hold; a
        # refused hold reopens the offer rather than burning the claim window.
        offer.status = "claimed"
        try:
            return self.create_hold(session_id, user_id, offer.product_id, offer.quantity)
        except TicketingError:
            offer.status = "open"
            raise

    # ------------------------------------------------------------------
    # Tickets + transfers
    # ------------------------------------------------------------------

    def tickets_for(self, user_id: str) -> list[Ticket]:
        return sorted(
            (t for t in self._tickets.values() if t.owner_id == user_id),
            key=lambda t: t.ticket_id,
        )

    def barcode(self, ticket_id: str) -> str:
        """An entry code that changes every rotation window; demo-grade, not a credential."""
        window = int(self._now().timestamp()) // BARCODE_ROTATION_S
        digest = hashlib.sha256(f"{ticket_id}:{window}".encode()).hexdigest()
        return digest[:10].upper()

    def initiate_transfer(self, user_id: str, ticket_ids: list[str], recipient: str) -> Transfer:
        """Stage a transfer of tickets the caller owns; one bad ticket rejects the batch."""
        if not ticket_ids:
            raise StateError("no tickets given to transfer")
        tickets: list[Ticket] = []
        for ticket_id in ticket_ids:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise NotFoundError(f"no such ticket {ticket_id}")
            if ticket.owner_id != user_id:
                raise OwnershipError("that ticket belongs to a different customer")
            if ticket.status != "active":
                raise StateError(
                    f"ticket {ticket_id} is not transferable (status: {ticket.status})"
                )
            tickets.append(ticket)
        transfer = Transfer(
            transfer_id=self._next_id("xfer"),
            ticket_ids=list(ticket_ids),
            from_user_id=user_id,
            recipient=recipient,
            initiated_at=self._now(),
        )
        self._transfers[transfer.transfer_id] = transfer
        for ticket in tickets:
            ticket.status = "transfer_pending"
        return transfer

    def cancel_transfer(self, user_id: str, transfer_id: str) -> Transfer:
        transfer = self._transfers.get(transfer_id)
        if transfer is None:
            raise NotFoundError("no such transfer")
        if transfer.from_user_id != user_id:
            raise OwnershipError("this transfer was started by a different customer")
        if transfer.status != "pending":
            raise StateError(f"this transfer is not pending (status: {transfer.status})")
        transfer.status = "cancelled"
        for ticket_id in transfer.ticket_ids:
            ticket = self._tickets.get(ticket_id)
            if ticket is not None and ticket.status == "transfer_pending":
                ticket.status = "active"
        return transfer

    def transfers_for(self, user_id: str) -> list[Transfer]:
        return sorted(
            (t for t in self._transfers.values() if t.from_user_id == user_id),
            key=lambda t: t.transfer_id,
        )
