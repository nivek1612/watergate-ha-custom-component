"""The Watergate integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from http import HTTPStatus
import logging

from watergate_local_api import WatergateLocalApiClient
from watergate_local_api.models import WebhookEvent

from homeassistant.components.http import HomeAssistantView
from homeassistant.components.webhook import Request, Response, async_generate_url
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS, CONF_WEBHOOK_ID, Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import WatergateDataCoordinator

_LOGGER = logging.getLogger(__name__)


PLATFORMS: list[Platform] = [
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VALVE,
]

WEBHOOK_ASO_REPORT_TYPE = "auto-shut-off-report"
WEBHOOK_TELEMETRY_TYPE = "telemetry"
WEBHOOK_VALVE_TYPE = "valve"
WEBHOOK_WIFI_CHANGED_TYPE = "wifi-changed"
WEBHOOK_POWER_SUPPLY_CHANGED_TYPE = "power-supply-changed"

type WatergateConfigEntry = ConfigEntry[WatergateDataCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: WatergateConfigEntry) -> bool:
    """Set up Watergate from a config entry."""
    sonic_address = entry.data[CONF_IP_ADDRESS]
    webhook_id = entry.data[CONF_WEBHOOK_ID]

    _LOGGER.debug(
        "Setting up watergate local api integration for device: IP: %s)",
        sonic_address,
    )

    watergate_client = WatergateLocalApiClient(sonic_address)

    coordinator = WatergateDataCoordinator(hass, watergate_client)
    entry.runtime_data = coordinator

    hass.components.webhook.async_unregister(webhook_id)
    hass.components.webhook.async_register(
        DOMAIN, "Watergate", webhook_id, get_webhook_handler(coordinator)
    )

    _LOGGER.debug("Registered webhook: %s", webhook_id)

    await coordinator.async_config_entry_first_refresh()

    await watergate_client.async_set_webhook_url(
        async_generate_url(hass, webhook_id, allow_ip=True)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: WatergateConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def get_webhook_handler(
    coordinator: WatergateDataCoordinator,
) -> Callable[[HomeAssistant, str, Request], Awaitable[Response | None]]:
    """Return webhook handler."""

    async def async_webhook_handler(
        hass: HomeAssistant, webhook_id: str, request: Request
    ) -> Response | None:
        # Handle http post calls to the path.
        if not request.body_exists:
            return HomeAssistantView.json(
                result="No Body", status_code=HTTPStatus.BAD_REQUEST
            )

        body = await request.json()

        _LOGGER.debug("Received webhook: %s", body)

        data = WebhookEvent.parse_webhook_event(body)

        body_type = body.get("type")

        coordinator_data = coordinator.data
        if body_type == Platform.VALVE and coordinator_data:
            coordinator_data.state.valve_state = data.state

        if body_type == WEBHOOK_TELEMETRY_TYPE:
            errors = data.errors or {}
            coordinator_data.telemetry.flow = (
                data.flow if "flow" not in errors else None
            )
            coordinator_data.telemetry.pressure = (
                data.pressure if "pressure" not in errors else None
            )
            coordinator_data.telemetry.water_temperature = (
                data.temperature if "temperature" not in errors else None
            )

        if body_type == WEBHOOK_WIFI_CHANGED_TYPE:
            coordinator_data.networking.ip = data.ip
            coordinator_data.networking.gateway = data.gateway
            coordinator_data.networking.subnet = data.subnet
            coordinator_data.networking.ssid = data.ssid
            coordinator_data.networking.rssi = data.rssi

        if body_type == WEBHOOK_POWER_SUPPLY_CHANGED_TYPE:
            coordinator_data.state.power_supply = data.supply

        if body_type == WEBHOOK_ASO_REPORT_TYPE:
            coordinator_data.auto_shut_off_report = data

        coordinator.async_set_updated_data(coordinator_data)

        return HomeAssistantView.json(result="OK", status_code=HTTPStatus.OK)

    return async_webhook_handler
