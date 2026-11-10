"""Setup: paste the pairing code from Kinekt (Family → Home Assistant). No Home Assistant token is needed."""
import hashlib
import re
import urllib.parse

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import logic
from .bridge import KinektAuthError, KinektHTTPError, KinektUnreachable, kinekt_request
from .const import CONF_API_URL, CONF_CREDENTIAL, DEFAULT_API_URL, DOMAIN

CODE = re.compile('[a-f0-9]{64}')


def server_origin(value):
    """An HTTPS origin, without credentials, paths or query parameters."""
    url = urllib.parse.urlsplit(value.strip())
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        return None
    return value.strip().rstrip('/')


class KinektConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def check(self, api_url, credential):
        """The error key for this code, or None when Kinekt accepts it."""
        try:
            logic.validate_selection(await kinekt_request(async_get_clientsession(self.hass), api_url, credential, '/v1/home-bridge/config'))
        except KinektAuthError:
            return 'invalid_code'
        except (KinektHTTPError, KinektUnreachable, logic.BridgeError):
            return 'cannot_connect'
        return None

    def schema(self, defaults, with_server=True):
        fields = {vol.Required(CONF_CREDENTIAL): str}
        if with_server:
            fields[vol.Required(CONF_API_URL, default=defaults.get(CONF_API_URL, DEFAULT_API_URL))] = str
        return vol.Schema(fields)

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            credential = user_input[CONF_CREDENTIAL].strip().lower()
            api_url = server_origin(user_input[CONF_API_URL])
            if not CODE.fullmatch(credential):
                errors[CONF_CREDENTIAL] = 'bad_code'
            elif not api_url:
                errors[CONF_API_URL] = 'bad_server'
            elif not (error := await self.check(api_url, credential)):
                # One entry per pairing; the code itself is never the visible ID.
                await self.async_set_unique_id(hashlib.sha256(credential.encode()).hexdigest()[:20])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title='Kinekt', data={CONF_API_URL: api_url, CONF_CREDENTIAL: credential})
            else:
                errors['base'] = error
        return self.async_show_form(step_id='user', data_schema=self.schema(user_input or {}), errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Disconnected in Kinekt: pair again and paste the new code."""
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            credential = user_input[CONF_CREDENTIAL].strip().lower()
            if not CODE.fullmatch(credential):
                errors[CONF_CREDENTIAL] = 'bad_code'
            elif not (error := await self.check(entry.data[CONF_API_URL], credential)):
                return self.async_update_reload_and_abort(entry, data_updates={CONF_CREDENTIAL: credential})
            else:
                errors['base'] = error
        return self.async_show_form(step_id='reauth_confirm', data_schema=self.schema({}, with_server=False), errors=errors)
