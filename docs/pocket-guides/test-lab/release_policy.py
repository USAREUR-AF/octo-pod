# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Teaching contract only, not a production identity or authorization system."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Grant:
    active: bool
    expires_at: int


def may_release(grant: Grant | None, now: int) -> bool:
    """Allow an active grant strictly before expiry; missing grants deny."""
    if grant is None:
        return False
    return grant.active and now < grant.expires_at
