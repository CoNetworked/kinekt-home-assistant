"""The home bridge inside Home Assistant: the same outbound reports and effects as Kinekt's standalone bridge.

Since 1.2.0: a report when a shared device changes (Home Assistant's own state events, gathered for two seconds) and
a check-in every 10 minutes; Kinekt's changes on one request that waits (/v1/home-bridge/wait). Nothing connects into
the house: every request is outbound, as before.

Home Assistant reads its own states and runs its own services here, so there is no Home Assistant
token or local address to configure. Only selected state text and a unit leave the house; logs
name connection status only, never states, devices or credentials.
"""
import asyncio
import datetime
import hashlib
import json
import logging
import uuid

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store

from . import logic
from .logic import BridgeError
from .const import DEBOUNCE_SECONDS, HEARTBEAT_SECONDS, INTERVAL_SECONDS, WAIT_SECONDS

_LOGGER = logging.getLogger(__name__)
LIMIT = 262144


class KinektAuthError(Exception):
    """Kinekt refused the pairing credential: disconnected in the app, or the owner changed."""


class KinektHTTPError(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


class KinektUnreachable(Exception):
    pass


async def kinekt_request(session, api_url, credential, path, payload=None, seconds=8):
    """One bounded request to Kinekt. Redirects are refused so the credential never follows one. A 304 ("nothing new")
    is None."""
    try:
        async with session.request('GET' if payload is None else 'POST', api_url + path, json=payload,
                                   headers={'Authorization': 'Bearer ' + credential}, allow_redirects=False,
                                   timeout=aiohttp.ClientTimeout(total=seconds)) as response:
            if response.status in (401, 403):
                raise KinektAuthError()
            if response.status == 304:
                return None
            if response.status >= 300:
                raise KinektHTTPError(response.status)
            data = await response.content.read(LIMIT + 1)
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        raise KinektUnreachable() from error
    if len(data) > LIMIT:
        raise BridgeError('Kinekt response exceeded the bridge limit.')
    try:
        return json.loads(data)
    except (ValueError, RecursionError, UnicodeError) as error:
        raise BridgeError('Kinekt returned invalid JSON.') from error


class KinektBridge:
    def __init__(self, hass: HomeAssistant, entry_id, api_url, credential):
        self.hass = hass
        self.api_url = api_url
        self.credential = credential
        self.session = async_get_clientsession(hass)
        # Saved atomically in Home Assistant's private storage, like the standalone ledger file.
        self.store = Store(hass, 1, f'kinekt.{entry_id}.effects', private=True, atomic_writes=True)
        self.ledger = {'applied': {}}
        self.lock = asyncio.Lock()
        self.revoked = False
        self.status = None
        self.on_revoked = None
        # 1.2.0: what's shared (and watched), the last answer from /wait, and a report waiting to go.
        self.selected = []
        self.etag = ''
        self.unwatch = None
        self.pending = None
        # True against a server without /wait: every 30 seconds, as 1.1.0 did.
        self.legacy = False

    async def async_load(self):
        stored = await self.store.async_load()
        if isinstance(stored, dict) and isinstance(stored.get('applied'), dict):
            self.ledger = stored

    async def save(self):
        await self.store.async_save(self.ledger)

    def request(self, path, payload=None, seconds=8):
        return kinekt_request(self.session, self.api_url, self.credential, path, payload, seconds)

    async def send(self, path, payload):
        """Retry this exact report once after an ambiguous connection failure."""
        try:
            return await self.request(path, payload)
        except KinektUnreachable:
            return await self.request(path, payload)

    def observe(self, selected):
        states, missing = [], False
        for entity in selected:
            current = self.hass.states.get(entity)
            if current is None:
                missing = True
                continue
            states.append(logic.project_state(entity, current.state, dict(current.attributes)))
        return ('entity_missing' if missing else 'connected'), states

    def watch(self, selected):
        """Hear Home Assistant's own state changes for the shared devices (and nothing else)."""
        if selected == self.selected and (self.unwatch or self.legacy):
            return
        self.selected = selected
        self.stop_watching()
        if not self.legacy:
            self.unwatch = async_track_state_change_event(self.hass, list(selected), self.changed)

    def stop_watching(self):
        if self.unwatch:
            self.unwatch()
            self.unwatch = None
        if self.pending:
            self.pending()
            self.pending = None

    def changed(self, _event):
        """A shared device changed: report it in two seconds, with whatever else changed meanwhile."""
        if self.pending is None and not self.revoked:
            self.pending = async_call_later(self.hass, DEBOUNCE_SECONDS, self.send_changes)

    async def send_changes(self, _now=None):
        self.pending = None
        await self.check_in(fetch=False)

    async def report(self, selected):
        status, states = self.observe(selected)
        report = {'operationId': str(uuid.uuid4()), 'observedAt': logic.now_iso(), 'status': status, 'states': states}
        if not self.legacy:
            report['heartbeatSeconds'] = HEARTBEAT_SECONDS
        await self.send('/v1/home-bridge/report', report)
        return status

    async def cycle(self):
        selection = await self.request('/v1/home-bridge/config')
        selected = logic.validate_selection(selection)
        self.watch(selected)
        status = await self.report(selected)
        if selection.get('catalogRequest'):
            try:
                await self.share_catalog(selection['catalogRequest'])
            except KinektAuthError:
                raise
            except KinektHTTPError:
                _LOGGER.info('Home device list: Kinekt could not accept the result.')
            except (KinektUnreachable, BridgeError):
                _LOGGER.info('Home device list: the request will wait for another connection.')
        return status

    async def share_catalog(self, pending):
        operation = logic.catalog_request(pending)
        entities, seen = [], set()
        for current in self.hass.states.async_all():
            entry = logic.catalog_entry(current.entity_id, dict(current.attributes))
            if entry and entry['entityId'] not in seen:
                seen.add(entry['entityId'])
                entities.append(entry)
        entities.sort(key=lambda item: item['entityId'])
        await self.send('/v1/home-bridge/catalog', {'operationId': operation, 'observedAt': logic.now_iso(), 'status': 'ready', 'entities': entities[:2000]})
        _LOGGER.info('Home device list: ready')

    def switch_state(self, entity):
        current = self.hass.states.get(entity)
        if current is None or current.state not in ('on', 'off'):
            raise BridgeError('Home effect skipped because the device state is not on or off.')
        return current.state

    async def call_service(self, action):
        if not logic.valid_action(action):
            raise BridgeError('Refusing a home effect outside the light and switch allowlist.')
        domain = action['entityId'].split('.', 1)[0]
        await self.hass.services.async_call(domain, action['command'], {'entity_id': action['entityId']}, blocking=True)

    async def effects_cycle(self, now=None, revoked=False, drain=False, desired=None, local=False):
        """Apply and restore effects. Returns True once nothing is left to restore after revoking or draining.
        `desired`: already read (from /wait). `local`: no connection at all, only deadlines (every 30 seconds)."""
        ledger = self.ledger
        scope = hashlib.sha256((self.api_url + '|' + self.credential).encode()).hexdigest()
        changed = ledger.get('scope', scope) != scope
        if revoked or changed or drain:
            desired = []
        reachable = not revoked and not local
        if desired is None and not local:
            try:
                desired = logic.validate_desired(await self.request(f'/v1/home-bridge/effects?max={logic.MAX_EFFECTS}'))
            except KinektAuthError:
                reachable, revoked, desired = False, True, []
            except (KinektHTTPError, KinektUnreachable, BridgeError):
                # Offline: only deadlines already stored are restored.
                reachable = False
        if changed:
            # Old-household reports must never be forwarded with a new credential.
            reachable = False
            ledger['reports'] = []
        else:
            ledger['scope'] = scope
        ledger, steps = logic.plan_effect_steps(ledger, desired, now or datetime.datetime.now(datetime.timezone.utc))
        self.ledger = ledger
        await self.save()
        for step in steps:
            capture = step['phase'] == 'apply'
            try:
                if capture:
                    # Write ahead: a crash or failed service call must leave enough to restore every attempted device.
                    prior = [{'entityId': action['entityId'], 'state': self.switch_state(action['entityId'])} for action in step['actions']]
                    slot = {'expiresAt': step['expiresAt'], 'prior': prior, 'restored': False, 'applying': True}
                    ledger['applied'][step['bindingId']] = slot
                    await self.save()
                else:
                    slot = ledger['applied'][step['bindingId']]
                for action in step['actions']:
                    await self.call_service(action)
            except (HomeAssistantError, BridgeError, ValueError):
                # An applying entry is restored on the next cycle. Never capture a changed state.
                _LOGGER.warning('Home effects: a scheduled change did not complete; recovery is pending.')
                continue
            slot['applying'] = False
            slot['restored'] = not capture
            ledger.setdefault('reports', []).append({'bindingId': step['bindingId'], 'phase': step['phase'], 'operationId': str(uuid.uuid4()), 'expiresAt': step['expiresAt'], 'status': 'applied' if capture else 'restored', 'prior': slot['prior']})
            await self.save()
        if reachable:
            for report in list(ledger.get('reports', [])):
                try:
                    await self.request('/v1/home-bridge/effects/report', report)
                except (KinektAuthError, KinektHTTPError, KinektUnreachable, BridgeError):
                    _LOGGER.info('Home effects: the local change is saved and its report will retry.')
                    break
                ledger['reports'].remove(report)
                await self.save()
        if changed and not logic.pending(ledger):
            self.ledger = {'applied': {}, 'reports': [], 'scope': scope}
            await self.save()
        if steps:
            _LOGGER.info('Home effects: %s change(s).', len(steps))
        return not logic.pending(self.ledger) if revoked or drain else False

    async def revoke(self):
        """Disconnected in Kinekt: put every device back and ask the owner to pair again."""
        self.stop_watching()
        try:
            await self.effects_cycle(revoked=True)
        except (HomeAssistantError, BridgeError, ValueError, KeyError, TypeError):
            _LOGGER.warning('Home effects: the schedule or local recovery is unavailable.')
        if not self.revoked:
            self.revoked = True
            _LOGGER.warning('Kinekt home bridge: this connection was disconnected in Kinekt; pair it again.')
            if self.on_revoked:
                self.on_revoked()

    async def check_in(self, fetch=True):
        """A report (a change, or the 10-minute check-in); with `fetch`, effects' reports go too."""
        if self.revoked or self.legacy:
            return
        async with self.lock:
            try:
                status = await self.report(self.selected) if self.selected else None
                if status and status != self.status:
                    _LOGGER.info('Kinekt home bridge: %s', status)
                    self.status = status
                if fetch:
                    await self.effects_cycle()
            except KinektAuthError:
                await self.revoke()
            except KinektHTTPError as error:
                _LOGGER.warning('Kinekt home bridge: Kinekt rejected the connection (HTTP %s).', error.status)
            except (KinektUnreachable, BridgeError):
                if self.status != 'unreachable':
                    _LOGGER.warning('Kinekt home bridge: Kinekt is unreachable or returned an invalid response.')
                self.status = 'unreachable'
            except (HomeAssistantError, ValueError, KeyError, TypeError):
                _LOGGER.warning('Home effects: the schedule or local recovery is unavailable.')

    async def heartbeat(self, *_):
        await self.check_in(fetch=True)

    async def local_tick(self, *_):
        """Deadlines with no connection: an effect ends on time even when Kinekt can't be reached."""
        if self.legacy or self.revoked or self.lock.locked():
            return
        async with self.lock:
            try:
                await self.effects_cycle(local=True)
            except (HomeAssistantError, BridgeError, ValueError, KeyError, TypeError):
                _LOGGER.warning('Home effects: the schedule or local recovery is unavailable.')

    async def apply(self, answer):
        """What /wait said: what to share, a device-list request, and the effects to have on."""
        config = answer.get('config') if isinstance(answer, dict) else None
        selected = logic.validate_selection(config)
        desired = logic.validate_desired({'effects': answer.get('effects')})
        etag = answer.get('etag')
        if not isinstance(etag, str) or len(etag) > 80:
            raise BridgeError('Kinekt returned an invalid answer.')
        async with self.lock:
            if selected != self.selected:
                self.watch(selected)
                await self.report(selected)
            if config.get('catalogRequest'):
                try:
                    await self.share_catalog(config['catalogRequest'])
                except KinektHTTPError:
                    _LOGGER.info('Home device list: Kinekt could not accept the result.')
            await self.effects_cycle(desired=desired)
            self.etag = etag

    async def listen(self):
        """Wait on Kinekt for changes, one request at a time. A server without /wait: back to every 30 seconds."""
        pause = 5
        loop = asyncio.get_running_loop()
        while not self.revoked:
            try:
                started, before = loop.time(), self.etag
                answer = await self.request(f'/v1/home-bridge/wait?wait={WAIT_SECONDS}&max={logic.MAX_EFFECTS}&etag={self.etag}', seconds=WAIT_SECONDS + 15)
                if answer is not None:
                    await self.apply(answer)
                pause = 5
                # Kinekt answers "nothing new" only after waiting; one that answers at once (a proxy, an older
                # Kinekt) is asked again after a pause, never in a tight loop.
                if self.etag == before and loop.time() - started < 5:
                    await asyncio.sleep(5)
            except KinektAuthError:
                await self.revoke()
                return
            except KinektHTTPError as error:
                if error.status == 404:
                    _LOGGER.info('Kinekt home bridge: this Kinekt checks in every 30 seconds.')
                    self.legacy = True
                    self.stop_watching()
                    return
                await asyncio.sleep(pause)
                pause = min(pause * 2, 300)
            except (KinektUnreachable, BridgeError, HomeAssistantError, ValueError, KeyError, TypeError):
                await asyncio.sleep(pause)
                pause = min(pause * 2, 300)

    async def run(self):
        """The first check-in, then waiting on Kinekt; against an older Kinekt, the 30-second cycle."""
        await self.tick()
        await self.listen()
        while self.legacy and not self.revoked:
            await asyncio.sleep(INTERVAL_SECONDS)
            await self.tick()

    async def tick(self, *_):
        """One full cycle: the first check-in, and every 30 seconds against an older Kinekt. Local restoration always
        runs, even when Kinekt can't be reached."""
        if self.lock.locked():
            return
        async with self.lock:
            revoked = self.revoked
            if not revoked:
                try:
                    status = await self.cycle()
                    if status != self.status:
                        _LOGGER.info('Kinekt home bridge: %s', status)
                    self.status = status
                except KinektAuthError:
                    revoked = True
                except KinektHTTPError as error:
                    _LOGGER.warning('Kinekt home bridge: Kinekt rejected the connection (HTTP %s).', error.status)
                except (KinektUnreachable, BridgeError):
                    if self.status != 'unreachable':
                        _LOGGER.warning('Kinekt home bridge: Kinekt is unreachable or returned an invalid response.')
                    self.status = 'unreachable'
            try:
                await self.effects_cycle(revoked=revoked)
            except (HomeAssistantError, BridgeError, ValueError, KeyError, TypeError):
                _LOGGER.warning('Home effects: the schedule or local recovery is unavailable.')
            if revoked and not self.revoked:
                self.revoked = True
                _LOGGER.warning('Kinekt home bridge: this connection was disconnected in Kinekt; pair it again.')
                if self.on_revoked:
                    self.on_revoked()
            return revoked

    async def drain(self):
        """Removing the integration: put every device back the way it was."""
        async with self.lock:
            try:
                return await self.effects_cycle(drain=True)
            except (HomeAssistantError, BridgeError, ValueError, KeyError, TypeError):
                return False
