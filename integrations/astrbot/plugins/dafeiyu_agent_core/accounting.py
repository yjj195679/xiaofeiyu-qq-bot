"""Model-call budget reservations and truthful usage settlement."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .store import AgentStore


@dataclass(frozen=True)
class BudgetPolicy:
    token_limit: int
    call_limit: int
    cost_limit_microunits: int = 0
    request_token_reserve: int = 8192
    input_price_per_million_microunits: int = 0
    cached_price_per_million_microunits: int = 0
    output_price_per_million_microunits: int = 0

    def __post_init__(self) -> None:
        values = (
            self.token_limit, self.call_limit, self.cost_limit_microunits,
            self.request_token_reserve, self.input_price_per_million_microunits,
            self.cached_price_per_million_microunits,
            self.output_price_per_million_microunits,
        )
        if min(values) < 0:
            raise ValueError("negative budget policy value")
        if not self.request_token_reserve:
            raise ValueError("request token reserve must be positive")

    def reserved_cost(self) -> int:
        if not self.cost_limit_microunits:
            return 0
        highest_rate = max(
            self.input_price_per_million_microunits,
            self.cached_price_per_million_microunits,
            self.output_price_per_million_microunits,
        )
        return math.ceil(self.request_token_reserve * highest_rate / 1_000_000)

    def actual_cost(self, *, input_other: int, input_cached: int, output: int) -> int:
        if not self.cost_limit_microunits:
            return 0
        weighted = (
            input_other * self.input_price_per_million_microunits
            + input_cached * self.cached_price_per_million_microunits
            + output * self.output_price_per_million_microunits
        )
        return math.ceil(weighted / 1_000_000)


class ModelBudgetAccountant:
    def __init__(self, store: AgentStore, policies: dict[str, BudgetPolicy]):
        self.store = store
        self.policies = dict(policies)

    def reserve(self, day: str, bucket: str) -> str:
        policy = self.policies[bucket]
        return self.store.reserve_budget(
            day, bucket, tokens=policy.request_token_reserve, calls=1,
            cost_microunits=policy.reserved_cost(), token_limit=policy.token_limit,
            call_limit=policy.call_limit,
            cost_limit_microunits=policy.cost_limit_microunits,
        )

    def settle(
        self, reservation_id: str, bucket: str, *,
        input_other: int, input_cached: int, output: int,
    ) -> bool:
        values = (input_other, input_cached, output)
        if min(values) < 0:
            raise ValueError("negative provider token usage")
        policy = self.policies[bucket]
        return self.store.settle_budget(
            reservation_id, tokens=sum(values), calls=1,
            cost_microunits=policy.actual_cost(
                input_other=input_other, input_cached=input_cached, output=output,
            ),
        )
