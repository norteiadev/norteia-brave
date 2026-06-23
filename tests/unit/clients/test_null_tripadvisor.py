"""Offline unit tests for NullTripAdvisorClient (TA-01).

NullTripAdvisorClient must:
- Return empty lists / zero for all methods (no network I/O)
- Satisfy TripAdvisorClientProtocol via structural typing assertion
"""

import pytest


class TestNullTripAdvisorClient:
    """Tests for the no-network TripAdvisor stub."""

    @pytest.mark.asyncio
    async def test_fetch_destinations_returns_empty_list(self):
        from brave.clients.null_tripadvisor import NullTripAdvisorClient

        client = NullTripAdvisorClient()
        result = await client.fetch_destinations(uf="BA")
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_attractions_returns_empty_list(self):
        from brave.clients.null_tripadvisor import NullTripAdvisorClient

        client = NullTripAdvisorClient()
        result = await client.fetch_attractions(geo_id=303513, offset=0)
        assert result == []

    @pytest.mark.asyncio
    async def test_resolve_geo_id_returns_zero(self):
        from brave.clients.null_tripadvisor import NullTripAdvisorClient

        client = NullTripAdvisorClient()
        result = await client.resolve_geo_id(uf="BA")
        assert result == 0

    def test_protocol_compliance(self):
        """Structural typing assertion — must not raise."""
        from brave.clients.null_tripadvisor import _check_protocol_compliance

        _check_protocol_compliance()  # raises TypeError if protocol not satisfied
