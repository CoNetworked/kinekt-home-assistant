"""Kinekt for Home Assistant: https://github.com/CoNetworked/kinekt-home-assistant"""
DOMAIN = 'kinekt'
DEFAULT_API_URL = 'https://kinektfamily.com'
CONF_API_URL = 'api_url'
CONF_CREDENTIAL = 'credential'
# Since 1.2.0, changes go
# the moment Home Assistant sees them; otherwise a check-in every 10 minutes. Kinekt's news (a mute, a schedule, a
# device-list request) arrives on one request that waits up to 90 seconds. Effects still end on time with no
# connection: a local check every 30 seconds that never leaves the house.
HEARTBEAT_SECONDS = 600
WAIT_SECONDS = 90
LOCAL_SECONDS = 30
DEBOUNCE_SECONDS = 2
# A server without the waiting request: every 30 seconds, as 1.1.0 did.
INTERVAL_SECONDS = 30
