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
# 1.3.1 (TK, Nov 19: the Lights card said "update to 1.3" again after a Kinekt deploy): a "not found" while Kinekt
# restarts isn't an older Kinekt. Three in a row, NOT_FOUND_RETRY_SECONDS apart, before the 30-second check-ins; and
# from those, the waiting request is tried again every RETRY_WAIT_SECONDS.
NOT_FOUND_RETRY_SECONDS = 20
NOT_FOUND_TIMES = 3
RETRY_WAIT_SECONDS = 600
