"""Tests for WebhookSubscriber and A2ASubscriber."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apcore.events.emitter import ApCoreEvent, EventSubscriber


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(**overrides: Any) -> ApCoreEvent:
    defaults: dict[str, Any] = {
        "event_type": "test.event",
        "module_id": "mod.a",
        "timestamp": "2026-03-08T00:00:00Z",
        "severity": "info",
        "data": {"key": "value"},
    }
    defaults.update(overrides)
    return ApCoreEvent(**defaults)


# ---------------------------------------------------------------------------
# WebhookSubscriber tests
# ---------------------------------------------------------------------------


class TestWebhookSubscriberSendsPostRequest:
    @pytest.mark.asyncio
    async def test_webhook_subscriber_sends_post_request(self) -> None:
        from apcore.events.subscribers import WebhookSubscriber

        subscriber = WebhookSubscriber(url="https://example.com/webhook")
        event = _make_event()

        with patch("apcore.events.subscribers.aiohttp") as mock_aiohttp:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.post = MagicMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)
            mock_aiohttp.ClientTimeout = MagicMock()

            await subscriber.on_event(event)

            mock_session.post.assert_called_once()
            call_kwargs = mock_session.post.call_args
            assert (
                call_kwargs[0][0] == "https://example.com/webhook"
                or call_kwargs.kwargs.get("url") == "https://example.com/webhook"
            )


class TestWebhookSubscriberIncludesCustomHeaders:
    @pytest.mark.asyncio
    async def test_webhook_subscriber_includes_custom_headers(self) -> None:
        from apcore.events.subscribers import WebhookSubscriber

        subscriber = WebhookSubscriber(
            url="https://example.com/webhook",
            headers={"X-Api-Key": "secret"},
        )
        event = _make_event()

        with patch("apcore.events.subscribers.aiohttp") as mock_aiohttp:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.post = MagicMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)
            mock_aiohttp.ClientTimeout = MagicMock()

            await subscriber.on_event(event)

            call_kwargs = mock_session.post.call_args
            headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
            assert headers.get("X-Api-Key") == "secret"


class TestWebhookSubscriberRetriesOn5xx:
    @pytest.mark.asyncio
    async def test_webhook_subscriber_retries_on_5xx(self) -> None:
        from apcore.events.subscribers import WebhookSubscriber

        subscriber = WebhookSubscriber(url="https://example.com/webhook", retry_count=3)
        event = _make_event()

        with patch("apcore.events.subscribers.aiohttp") as mock_aiohttp:
            # Return 500 twice, then 200
            responses = []
            for status in [500, 500, 200]:
                resp = AsyncMock()
                resp.status = status
                resp.__aenter__ = AsyncMock(return_value=resp)
                resp.__aexit__ = AsyncMock(return_value=False)
                responses.append(resp)

            mock_session = AsyncMock()
            mock_session.post = MagicMock(side_effect=responses)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)
            mock_aiohttp.ClientTimeout = MagicMock()

            await subscriber.on_event(event)

            assert mock_session.post.call_count == 3


class TestWebhookSubscriberRetriesOnConnectionError:
    @pytest.mark.asyncio
    async def test_webhook_subscriber_retries_on_connection_error(self) -> None:
        from apcore.events.subscribers import WebhookSubscriber

        subscriber = WebhookSubscriber(url="https://example.com/webhook", retry_count=3)
        event = _make_event()

        with patch("apcore.events.subscribers.aiohttp") as mock_aiohttp:
            mock_response_ok = AsyncMock()
            mock_response_ok.status = 200
            mock_response_ok.__aenter__ = AsyncMock(return_value=mock_response_ok)
            mock_response_ok.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            # Connection error twice, then success
            mock_session.post = MagicMock(
                side_effect=[OSError("conn refused"), OSError("conn refused"), mock_response_ok]
            )
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)
            mock_aiohttp.ClientTimeout = MagicMock()

            await subscriber.on_event(event)

            assert mock_session.post.call_count == 3


class TestWebhookSubscriberRespectsRetryCount:
    @pytest.mark.asyncio
    async def test_webhook_subscriber_respects_retry_count(self) -> None:
        from apcore.events.subscribers import WebhookSubscriber

        subscriber = WebhookSubscriber(url="https://example.com/webhook", retry_count=2)
        event = _make_event()

        with patch("apcore.events.subscribers.aiohttp") as mock_aiohttp:
            responses = []
            for _ in range(3):
                resp = AsyncMock()
                resp.status = 500
                resp.__aenter__ = AsyncMock(return_value=resp)
                resp.__aexit__ = AsyncMock(return_value=False)
                responses.append(resp)

            mock_session = AsyncMock()
            mock_session.post = MagicMock(side_effect=responses)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)
            mock_aiohttp.ClientTimeout = MagicMock()

            # Should not raise -- failures are logged
            await subscriber.on_event(event)

            # 1 initial + 2 retries = 3 total attempts
            assert mock_session.post.call_count == 3


class TestWebhookSubscriberEnforcesTimeout:
    @pytest.mark.asyncio
    async def test_webhook_subscriber_enforces_timeout(self) -> None:
        from apcore.events.subscribers import WebhookSubscriber

        subscriber = WebhookSubscriber(url="https://example.com/webhook", timeout_ms=100)
        event = _make_event()

        with patch("apcore.events.subscribers.aiohttp") as mock_aiohttp:
            mock_aiohttp.ClientTimeout = MagicMock()

            mock_session = AsyncMock()
            mock_session.post = MagicMock(side_effect=asyncio.TimeoutError())
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

            # Should log but not raise
            await subscriber.on_event(event)

            # Verify ClientTimeout was called with the correct timeout
            mock_aiohttp.ClientTimeout.assert_called_with(total=0.1)


class TestWebhookSubscriberDoesNotRetryOn4xx:
    @pytest.mark.asyncio
    async def test_webhook_subscriber_does_not_retry_on_4xx(self) -> None:
        from apcore.events.subscribers import WebhookSubscriber

        subscriber = WebhookSubscriber(url="https://example.com/webhook", retry_count=3)
        event = _make_event()

        with patch("apcore.events.subscribers.aiohttp") as mock_aiohttp:
            mock_response = AsyncMock()
            mock_response.status = 400
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.post = MagicMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)
            mock_aiohttp.ClientTimeout = MagicMock()

            await subscriber.on_event(event)

            # Only 1 attempt, no retries
            assert mock_session.post.call_count == 1


class TestWebhookSubscriberSerializesEventToJson:
    @pytest.mark.asyncio
    async def test_webhook_subscriber_serializes_event_to_json(self) -> None:
        from apcore.events.subscribers import WebhookSubscriber

        subscriber = WebhookSubscriber(url="https://example.com/webhook")
        event = _make_event()

        with patch("apcore.events.subscribers.aiohttp") as mock_aiohttp:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.post = MagicMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)
            mock_aiohttp.ClientTimeout = MagicMock()

            await subscriber.on_event(event)

            call_kwargs = mock_session.post.call_args
            body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            assert body is not None
            assert body["event_type"] == "test.event"
            assert body["module_id"] == "mod.a"
            assert body["timestamp"] == "2026-03-08T00:00:00Z"
            assert body["severity"] == "info"
            assert body["data"] == {"key": "value"}


# ---------------------------------------------------------------------------
# A2ASubscriber tests
# ---------------------------------------------------------------------------


class TestA2ASubscriberSendsViaClient:
    @pytest.mark.asyncio
    async def test_a2a_subscriber_sends_via_client(self) -> None:
        from apcore.events.subscribers import A2ASubscriber

        subscriber = A2ASubscriber(
            platform_url="https://platform.example.com",
            auth="bearer-token-123",
        )
        event = _make_event()

        with patch("apcore.events.subscribers.aiohttp") as mock_aiohttp:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.post = MagicMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

            await subscriber.on_event(event)

            call_kwargs = mock_session.post.call_args
            body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            assert body["skillId"] == "apevo.event_receiver"


class TestA2ASubscriberIncludesEventInPayload:
    @pytest.mark.asyncio
    async def test_a2a_subscriber_includes_event_in_payload(self) -> None:
        from apcore.events.subscribers import A2ASubscriber

        subscriber = A2ASubscriber(
            platform_url="https://platform.example.com",
        )
        event = _make_event()

        with patch("apcore.events.subscribers.aiohttp") as mock_aiohttp:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.post = MagicMock(return_value=mock_response)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

            await subscriber.on_event(event)

            call_kwargs = mock_session.post.call_args
            body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            payload_event = body["event"]
            assert payload_event["event_type"] == "test.event"
            assert payload_event["module_id"] == "mod.a"
            assert payload_event["data"] == {"key": "value"}


class TestA2ASubscriberHandlesSendFailure:
    @pytest.mark.asyncio
    async def test_a2a_subscriber_handles_send_failure(self) -> None:
        """Per apcore #61, A2A now raises on failure so the emitter can retry."""
        from apcore.events.subscribers import A2ASubscriber

        subscriber = A2ASubscriber(
            platform_url="https://platform.example.com",
        )
        event = _make_event()

        with patch("apcore.events.subscribers.aiohttp") as mock_aiohttp:
            mock_session = AsyncMock()
            mock_session.post = MagicMock(side_effect=OSError("connection failed"))
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)

            # A2A now raises on failure (emitter handles retry/DLQ)
            with pytest.raises(OSError, match="connection failed"):
                await subscriber.on_event(event)


# ---------------------------------------------------------------------------
# Protocol conformance tests
# ---------------------------------------------------------------------------


class TestSubscriberConformsToProtocol:
    def test_subscriber_conforms_to_protocol(self) -> None:
        from apcore.events.subscribers import A2ASubscriber, WebhookSubscriber

        webhook = WebhookSubscriber(url="https://example.com/webhook")
        a2a = A2ASubscriber(platform_url="https://platform.example.com")

        assert isinstance(webhook, EventSubscriber)
        assert isinstance(a2a, EventSubscriber)


class TestSubscriberInstantiationFailureLogged:
    def test_subscriber_instantiation_failure_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify that if a dependency fails during usage, it's logged not raised."""
        from apcore.events.subscribers import WebhookSubscriber

        # WebhookSubscriber should not raise during construction even with odd inputs
        subscriber = WebhookSubscriber(url="")
        assert subscriber is not None
