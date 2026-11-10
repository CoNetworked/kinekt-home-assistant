"""Kinekt: share chosen Home Assistant readings with your family and run scheduled light effects.

Changes go out when Home Assistant sees them, with a check-in every 10 minutes, and Kinekt's news arrives on one
outbound request that waits. Nothing connects into the home.
"""
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .bridge import KinektBridge
from .const import CONF_API_URL, CONF_CREDENTIAL, HEARTBEAT_SECONDS, LOCAL_SECONDS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Set up even when Kinekt is unreachable: saved effects must still be restored on time.
    bridge = KinektBridge(hass, entry.entry_id, entry.data[CONF_API_URL], entry.data[CONF_CREDENTIAL])
    await bridge.async_load()
    bridge.on_revoked = lambda: entry.async_start_reauth(hass)
    entry.runtime_data = bridge
    entry.async_on_unload(async_track_time_interval(hass, bridge.heartbeat, timedelta(seconds=HEARTBEAT_SECONDS), name='Kinekt home bridge check-in'))
    entry.async_on_unload(async_track_time_interval(hass, bridge.local_tick, timedelta(seconds=LOCAL_SECONDS), name='Kinekt home effects deadlines'))
    entry.async_on_unload(bridge.stop_watching)
    entry.async_create_background_task(hass, bridge.run(), 'Kinekt home bridge')
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # A restart unloads too: effects stay on and continue from the saved ledger afterwards.
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Deleting the integration puts every device back the way it was, then forgets the ledger."""
    bridge = KinektBridge(hass, entry.entry_id, entry.data[CONF_API_URL], entry.data[CONF_CREDENTIAL])
    await bridge.async_load()
    if await bridge.drain():
        await bridge.store.async_remove()
