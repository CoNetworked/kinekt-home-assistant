"""Kinekt inside a real Home Assistant core, against a fake Kinekt (synthetic only).

Run: scripts/test-home-assistant.sh
"""
import datetime
import json

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import asyncio

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.kinekt import logic
from custom_components.kinekt.const import CONF_API_URL, CONF_CREDENTIAL, DOMAIN
import zoneinfo

API = 'https://kinekt.test'
CODE = 'a' * 64
NEW_CODE = 'b' * 64
BINDING = '7d2b8f3c-0000-4000-8000-000000000001'


def iso(delta):
    return (datetime.datetime.now(datetime.timezone.utc) + delta).isoformat().replace('+00:00', 'Z')


def kinekt(mock, config=None, effects=None, status=200, exc=None, wait=None):
    """The fake Kinekt's answers; later calls replace earlier ones. `wait`: what /wait answers; without it, a
    Kinekt from before /wait (404), so the plugin checks in every 30 seconds as 1.1.0 did."""
    mock.clear_requests()
    if exc:
        for path in ('config', 'effects', 'wait'):
            mock.get(f'{API}/v1/home-bridge/{path}', exc=exc)
        for path in ('report', 'catalog', 'effects/report'):
            mock.post(f'{API}/v1/home-bridge/{path}', exc=exc)
        return
    mock.get(f'{API}/v1/home-bridge/config', status=status, json=config or {'entityIds': ['sun.sun', 'sensor.kitchen']})
    mock.get(f'{API}/v1/home-bridge/effects', status=status, json={'effects': effects or []})
    if wait is not None:
        mock.get(f'{API}/v1/home-bridge/wait', status=status, json=wait)
    else:
        mock.get(f'{API}/v1/home-bridge/wait', status=404 if status == 200 else status, json={})
    for path in ('report', 'catalog', 'effects/report', 'lights/report'):
        mock.post(f'{API}/v1/home-bridge/{path}', status=status, json={'ok': True})


def sent(mock, path):
    return [call[2] for call in mock.mock_calls if call[0] == 'POST' and str(call[1]).endswith(path)]


def light_service(hass, light):
    """A real light service in this Home Assistant that changes the state it reports."""
    calls = []

    async def handle(call):
        calls.append(call.service)
        hass.states.async_set(light, 'on' if call.service == 'turn_on' else 'off', {'friendly_name': 'Porch light'})
    hass.services.async_register('light', 'turn_on', handle)
    hass.services.async_register('light', 'turn_off', handle)
    return calls


async def setup(hass, mock):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_URL: API, CONF_CREDENTIAL: CODE}, unique_id='synthetic')
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_pastes_the_code_and_checks_it(hass: HomeAssistant, aioclient_mock):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': config_entries.SOURCE_USER})
    assert result['type'] is FlowResultType.FORM
    # A partial code and a non-HTTPS server are caught before anything is sent.
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {CONF_CREDENTIAL: 'abc', CONF_API_URL: API})
    assert result['errors'] == {CONF_CREDENTIAL: 'bad_code'}
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {CONF_CREDENTIAL: CODE, CONF_API_URL: 'http://kinekt.test/path'})
    assert result['errors'] == {CONF_API_URL: 'bad_server'}
    assert aioclient_mock.call_count == 0
    kinekt(aioclient_mock, status=401)
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {CONF_CREDENTIAL: CODE, CONF_API_URL: API})
    assert result['errors'] == {'base': 'invalid_code'}
    kinekt(aioclient_mock, exc=aiohttp.ClientError())
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {CONF_CREDENTIAL: CODE, CONF_API_URL: API})
    assert result['errors'] == {'base': 'cannot_connect'}
    kinekt(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {CONF_CREDENTIAL: ' ' + CODE.upper() + ' ', CONF_API_URL: API + '/'})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    assert result['data'] == {CONF_API_URL: API, CONF_CREDENTIAL: CODE}
    assert aioclient_mock.mock_calls[-1][3]['Authorization'] == 'Bearer ' + CODE
    # The same pairing twice is refused.
    await hass.async_block_till_done()
    again = await hass.config_entries.flow.async_init(DOMAIN, context={'source': config_entries.SOURCE_USER})
    again = await hass.config_entries.flow.async_configure(again['flow_id'], {CONF_CREDENTIAL: CODE, CONF_API_URL: API})
    assert again['type'] is FlowResultType.ABORT and again['reason'] == 'already_configured'


async def test_reports_only_selected_states_and_units(hass: HomeAssistant, aioclient_mock):
    hass.states.async_set('sun.sun', 'above_horizon', {'latitude': 39.7, 'friendly_name': 'Sun'})
    hass.states.async_set('sensor.kitchen', '21', {'unit_of_measurement': '°C', 'friendly_name': 'Kitchen'})
    hass.states.async_set('person.alex', 'home', {'latitude': 39.7})
    kinekt(aioclient_mock)
    await setup(hass, aioclient_mock)
    [report] = sent(aioclient_mock, '/v1/home-bridge/report')
    assert report['status'] == 'connected'
    assert report['states'] == [{'entityId': 'sun.sun', 'state': 'above_horizon', 'unit': None}, {'entityId': 'sensor.kitchen', 'state': '21', 'unit': '°C'}]
    assert 'latitude' not in json.dumps(report) and 'person' not in json.dumps(report)
    # A selected entity that's gone is reported honestly.
    hass.states.async_remove('sensor.kitchen')
    kinekt(aioclient_mock)
    await hass.config_entries.async_entries(DOMAIN)[0].runtime_data.tick()
    [report] = sent(aioclient_mock, '/v1/home-bridge/report')
    assert report['status'] == 'entity_missing' and [s['entityId'] for s in report['states']] == ['sun.sun']


async def test_kinekt_asking_for_a_person_or_location_gets_nothing(hass: HomeAssistant, aioclient_mock):
    """Only the kinds of reading the device list offers are read or reported, whatever Kinekt's selection says."""
    hass.states.async_set('sun.sun', 'above_horizon', {'friendly_name': 'Sun'})
    hass.states.async_set('person.alex', 'home', {'latitude': 39.7})
    hass.states.async_set('device_tracker.phone', 'home', {'latitude': 39.7})
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun', 'person.alex', 'device_tracker.phone']})
    await setup(hass, aioclient_mock)
    [report] = sent(aioclient_mock, '/v1/home-bridge/report')
    assert report['status'] == 'entity_missing'
    assert report['states'] == [{'entityId': 'sun.sun', 'state': 'above_horizon', 'unit': None}]
    # And a change to them is never heard.
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun', 'person.alex', 'device_tracker.phone']})
    hass.states.async_set('person.alex', 'not_home', {'latitude': 40.1})
    await hass.async_block_till_done()
    assert 'person' not in json.dumps(sent(aioclient_mock, '/v1/home-bridge/report')) and '40.1' not in json.dumps(sent(aioclient_mock, '/v1/home-bridge/report'))


async def test_device_list_has_names_and_ids_only(hass: HomeAssistant, aioclient_mock):
    hass.states.async_set('sun.sun', 'above_horizon')
    hass.states.async_set('sensor.kitchen', '21')
    hass.states.async_set('light.porch', 'off', {'friendly_name': ' Porch light ', 'brightness': 3})
    hass.states.async_set('person.alex', 'home', {'friendly_name': 'Alex'})
    hass.states.async_set('camera.door', 'idle', {'friendly_name': 'Door'})
    request = {'operationId': '7d2b8f3c-0000-4000-8000-0000000000aa', 'expiresAt': iso(datetime.timedelta(minutes=10))}
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun'], 'catalogRequest': request})
    await setup(hass, aioclient_mock)
    [catalog] = sent(aioclient_mock, '/v1/home-bridge/catalog')
    assert catalog['operationId'] == request['operationId'] and catalog['status'] == 'ready'
    assert catalog['entities'] == [{'entityId': 'light.porch', 'name': 'Porch light'}, {'entityId': 'sensor.kitchen', 'name': 'sensor.kitchen'}, {'entityId': 'sun.sun', 'name': 'sun.sun'}]


async def test_effect_turns_on_then_restores_at_the_deadline_even_offline(hass: HomeAssistant, aioclient_mock):
    hass.states.async_set('sun.sun', 'above_horizon')
    hass.states.async_set('light.porch', 'off', {'friendly_name': 'Porch light'})
    calls = light_service(hass, 'light.porch')
    effect = {'bindingId': BINDING, 'expiresAt': iso(datetime.timedelta(hours=2)), 'actions': [{'entityId': 'light.porch', 'command': 'turn_on'}]}
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']}, effects=[effect])
    entry = await setup(hass, aioclient_mock)
    bridge = entry.runtime_data
    assert calls == ['turn_on'] and hass.states.get('light.porch').state == 'on'
    [applied] = sent(aioclient_mock, '/v1/home-bridge/effects/report')
    assert applied['phase'] == 'apply' and applied['status'] == 'applied' and applied['prior'] == [{'entityId': 'light.porch', 'state': 'off'}]
    # Next check-in: already on, nothing more happens.
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']}, effects=[effect])
    await bridge.tick()
    assert calls == ['turn_on']
    # Restarting Home Assistant doesn't flip the light; the saved ledger carries on.
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hass.states.get('light.porch').state == 'on'
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert calls == ['turn_on']
    # Kinekt goes offline; the deadline passes; the light goes back off anyway.
    kinekt(aioclient_mock, exc=aiohttp.ClientError())
    await entry.runtime_data.effects_cycle(now=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3))
    assert calls == ['turn_on', 'turn_off'] and hass.states.get('light.porch').state == 'off'
    # Back online: the saved restore report goes through.
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']})
    await entry.runtime_data.tick()
    [restored] = sent(aioclient_mock, '/v1/home-bridge/effects/report')
    assert restored['phase'] == 'restore' and restored['status'] == 'restored'


async def test_an_effect_on_a_light_that_was_already_on_leaves_it_on(hass: HomeAssistant, aioclient_mock):
    hass.states.async_set('sun.sun', 'above_horizon')
    hass.states.async_set('light.porch', 'on')
    calls = light_service(hass, 'light.porch')
    effect = {'bindingId': BINDING, 'expiresAt': iso(datetime.timedelta(hours=2)), 'actions': [{'entityId': 'light.porch', 'command': 'turn_off'}]}
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']}, effects=[effect])
    entry = await setup(hass, aioclient_mock)
    assert hass.states.get('light.porch').state == 'off'
    # Cancelled in Kinekt: restored right away, to on.
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']}, effects=[])
    await entry.runtime_data.tick()
    assert calls == ['turn_off', 'turn_on'] and hass.states.get('light.porch').state == 'on'


async def test_only_lights_and_switches_are_ever_controlled(hass: HomeAssistant, aioclient_mock):
    # A fan reads on/off like a light, so only the allowlist stops it.
    hass.states.async_set('sun.sun', 'above_horizon')
    hass.states.async_set('fan.attic', 'off')
    fan = async_mock_service(hass, 'fan', 'turn_on')
    effect = {'bindingId': BINDING, 'expiresAt': iso(datetime.timedelta(hours=2)), 'actions': [{'entityId': 'fan.attic', 'command': 'turn_on'}]}
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']}, effects=[effect])
    entry = await setup(hass, aioclient_mock)
    assert fan == [] and entry.runtime_data.ledger['applied'] == {}


async def test_disconnected_in_kinekt_restores_then_asks_to_pair_again(hass: HomeAssistant, aioclient_mock):
    hass.states.async_set('sun.sun', 'above_horizon')
    hass.states.async_set('light.porch', 'off')
    calls = light_service(hass, 'light.porch')
    effect = {'bindingId': BINDING, 'expiresAt': iso(datetime.timedelta(hours=2)), 'actions': [{'entityId': 'light.porch', 'command': 'turn_on'}]}
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']}, effects=[effect])
    entry = await setup(hass, aioclient_mock)
    assert hass.states.get('light.porch').state == 'on'
    kinekt(aioclient_mock, status=401)
    await entry.runtime_data.tick()
    await hass.async_block_till_done()
    assert calls == ['turn_on', 'turn_off'] and hass.states.get('light.porch').state == 'off'
    [flow] = hass.config_entries.flow.async_progress()
    assert flow['context']['source'] == config_entries.SOURCE_REAUTH
    # Revoked: it stops calling Kinekt with the old code.
    aioclient_mock.clear_requests()
    kinekt(aioclient_mock, status=401)
    await entry.runtime_data.tick()
    assert aioclient_mock.call_count == 0
    # Paste the new code: the entry reloads with it.
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']})
    result = await hass.config_entries.flow.async_configure(flow['flow_id'], {CONF_CREDENTIAL: NEW_CODE})
    assert result['type'] is FlowResultType.ABORT and result['reason'] == 'reauth_successful'
    await hass.async_block_till_done()
    assert entry.data[CONF_CREDENTIAL] == NEW_CODE
    assert aioclient_mock.mock_calls[-1][3]['Authorization'] == 'Bearer ' + NEW_CODE


async def test_removing_the_integration_puts_devices_back(hass: HomeAssistant, aioclient_mock):
    hass.states.async_set('sun.sun', 'above_horizon')
    hass.states.async_set('light.porch', 'off')
    calls = light_service(hass, 'light.porch')
    effect = {'bindingId': BINDING, 'expiresAt': iso(datetime.timedelta(hours=2)), 'actions': [{'entityId': 'light.porch', 'command': 'turn_on'}]}
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']}, effects=[effect])
    entry = await setup(hass, aioclient_mock)
    assert hass.states.get('light.porch').state == 'on'
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert calls == ['turn_on', 'turn_off'] and hass.states.get('light.porch').state == 'off'


async def test_redirects_are_not_followed(hass: HomeAssistant, aioclient_mock):
    hass.states.async_set('sun.sun', 'above_horizon')
    aioclient_mock.get(f'{API}/v1/home-bridge/config', status=302, headers={'Location': 'https://elsewhere.test/'})
    aioclient_mock.get(f'{API}/v1/home-bridge/effects', status=302, headers={'Location': 'https://elsewhere.test/'})
    aioclient_mock.get(f'{API}/v1/home-bridge/wait', status=302, headers={'Location': 'https://elsewhere.test/'})
    entry = await setup(hass, aioclient_mock)
    assert entry.runtime_data.status is None
    assert all('elsewhere' not in str(call[1]) for call in aioclient_mock.mock_calls)
    assert all(call[0] == 'GET' for call in aioclient_mock.mock_calls), 'no report without a selection'


async def until(check, seconds=3):
    """The bridge works in the background: wait for it, briefly."""
    for _ in range(int(seconds / 0.05)):
        if check():
            return True
        await asyncio.sleep(0.05)
    return check()


async def test_waits_on_kinekt_and_applies_what_changed(hass: HomeAssistant, aioclient_mock):
    """Kinekt's news (here a mute switching a light on) arrives on the waiting request, not a 30-second poll."""
    hass.states.async_set('sun.sun', 'above_horizon')
    hass.states.async_set('light.porch', 'off', {'friendly_name': 'Porch light'})
    calls = light_service(hass, 'light.porch')
    effect = {'bindingId': BINDING, 'expiresAt': iso(datetime.timedelta(hours=2)), 'actions': [{'entityId': 'light.porch', 'command': 'turn_on'}]}
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']}, wait={'etag': 'e1', 'config': {'entityIds': ['sun.sun']}, 'effects': [effect], 'checkInSeconds': 600})
    entry = await setup(hass, aioclient_mock)
    bridge = entry.runtime_data
    assert await until(lambda: calls == ['turn_on'])
    assert bridge.etag == 'e1' and not bridge.legacy
    waits = [str(call[1]) for call in aioclient_mock.mock_calls if '/wait' in str(call[1])]
    assert waits and 'wait=90' in waits[0] and 'max=40' in waits[0]
    assert await until(lambda: sent(aioclient_mock, '/v1/home-bridge/effects/report'))
    [report] = sent(aioclient_mock, '/v1/home-bridge/report')
    assert report['heartbeatSeconds'] == 600, 'it tells Kinekt it checks in every 10 minutes'


async def test_a_change_is_reported_when_home_assistant_sees_it(hass: HomeAssistant, aioclient_mock):
    """A shared device changing goes out within seconds; nothing else does."""
    hass.states.async_set('sun.sun', 'above_horizon')
    hass.states.async_set('sensor.kitchen', '21', {'unit_of_measurement': '°C'})
    hass.states.async_set('sensor.garage', '9')
    answer = {'etag': 'e1', 'config': {'entityIds': ['sun.sun', 'sensor.kitchen']}, 'effects': [], 'checkInSeconds': 600}
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun', 'sensor.kitchen']}, wait=answer)
    entry = await setup(hass, aioclient_mock)
    bridge = entry.runtime_data
    assert await until(lambda: bridge.etag == 'e1')
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun', 'sensor.kitchen']}, wait=answer)
    # A device nobody shared changes: nothing goes to Kinekt.
    hass.states.async_set('sensor.garage', '10')
    async_fire_time_changed(hass, dt_util.utcnow() + datetime.timedelta(seconds=3))
    await hass.async_block_till_done()
    assert sent(aioclient_mock, '/v1/home-bridge/report') == []
    # Two changes to shared devices within two seconds: one report, with both.
    hass.states.async_set('sensor.kitchen', '22', {'unit_of_measurement': '°C'})
    hass.states.async_set('sun.sun', 'below_horizon')
    async_fire_time_changed(hass, dt_util.utcnow() + datetime.timedelta(seconds=3))
    await hass.async_block_till_done()
    assert await until(lambda: sent(aioclient_mock, '/v1/home-bridge/report'))
    [report] = sent(aioclient_mock, '/v1/home-bridge/report')
    assert report['states'] == [{'entityId': 'sun.sun', 'state': 'below_horizon', 'unit': None}, {'entityId': 'sensor.kitchen', 'state': '22', 'unit': '°C'}]
    assert report['heartbeatSeconds'] == 600


async def test_an_older_kinekt_gets_the_30_second_check_ins(hass: HomeAssistant, aioclient_mock):
    """A Kinekt without /wait (404): the plugin checks in every 30 seconds, as 1.1.0 did, and doesn't watch states."""
    hass.states.async_set('sun.sun', 'above_horizon')
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']})
    entry = await setup(hass, aioclient_mock)
    bridge = entry.runtime_data
    assert await until(lambda: bridge.legacy)
    assert bridge.unwatch is None
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']})
    await bridge.tick()
    [report] = sent(aioclient_mock, '/v1/home-bridge/report')
    assert 'heartbeatSeconds' not in report


COMMAND = '7d2b8f3c-0000-4000-8000-0000000000c1'
ROUTINE = '7d2b8f3c-0000-4000-8000-0000000000d1'


def lights(commands=(), routines=(), timezone='America/Denver'):
    return {'timezone': timezone, 'commands': list(commands), 'routines': list(routines)}


async def test_a_light_turned_off_now_from_kinekt(hass: HomeAssistant, aioclient_mock):
    """TK, Nov 18: "I forgot to turn the basement off. Turn that light off now." Once, and Kinekt hears how it went."""
    hass.states.async_set('sun.sun', 'above_horizon')
    hass.states.async_set('light.porch', 'on', {'friendly_name': 'Porch light'})
    calls = light_service(hass, 'light.porch')
    off = {'id': COMMAND, 'entityId': 'light.porch', 'command': 'turn_off', 'expiresAt': iso(datetime.timedelta(minutes=2))}
    answer = {'etag': 'e1', 'config': {'entityIds': ['sun.sun', 'light.porch']}, 'effects': [], 'lights': lights([off]), 'checkInSeconds': 600}
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun', 'light.porch']}, wait=answer)
    entry = await setup(hass, aioclient_mock)
    bridge = entry.runtime_data
    assert await until(lambda: calls == ['turn_off'])
    assert hass.states.get('light.porch').state == 'off'
    assert await until(lambda: sent(aioclient_mock, '/v1/home-bridge/lights/report'))
    assert sent(aioclient_mock, '/v1/home-bridge/lights/report')[0] == {'commandId': COMMAND, 'status': 'done'}
    assert sent(aioclient_mock, '/v1/home-bridge/report')[0]['features'] == ['lights']
    # The same request again (Kinekt hasn't heard yet): it doesn't run twice.
    await bridge.take_lights(logic.validate_lights(lights([off])))
    assert calls == ['turn_off']
    # An old request (the add-on was away): never.
    stale = dict(off, id='7d2b8f3c-0000-4000-8000-0000000000c2', command='turn_on', expiresAt=iso(datetime.timedelta(seconds=-1)))
    await bridge.take_lights(logic.validate_lights(lights([stale])))
    assert calls == ['turn_off']


async def test_a_schedule_runs_once_at_its_time_and_a_trip_wins(hass: HomeAssistant, aioclient_mock):
    """Kitchen off at 8:30 PM every day: once that evening, not again; a light a trip is holding is left alone."""
    hass.states.async_set('sun.sun', 'above_horizon')
    hass.states.async_set('light.porch', 'on', {'friendly_name': 'Porch light'})
    calls = light_service(hass, 'light.porch')
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']}, wait={'etag': 'e1', 'config': {'entityIds': ['sun.sun']}, 'effects': [], 'checkInSeconds': 600})
    entry = await setup(hass, aioclient_mock)
    bridge = entry.runtime_data
    assert await until(lambda: bridge.etag == 'e1')
    bridge.revoked = True  # quiet the waiting loop; schedules run from the ledger regardless
    routine = {'id': ROUTINE, 'entityIds': ['light.porch'], 'command': 'turn_off', 'at': {'kind': 'time', 'time': '20:30'}, 'days': [1, 2, 3, 4, 5, 6, 7]}
    await bridge.take_lights(logic.validate_lights(lights(routines=[routine])))
    zone = zoneinfo.ZoneInfo('America/Denver')
    before = datetime.datetime(2026, 11, 18, 20, 29, tzinfo=zone)
    await bridge.run_routines(before)
    assert calls == []
    await bridge.run_routines(before + datetime.timedelta(minutes=2))
    assert calls == ['turn_off']
    await bridge.run_routines(before + datetime.timedelta(minutes=5))
    assert calls == ['turn_off'], 'once a day'
    # Saved: after a restart, it remembers it ran and still has the schedule with no connection.
    assert bridge.ledger['lights']['routines'][0]['id'] == ROUTINE
    # The next evening a trip holds the porch light: the schedule leaves it be.
    bridge.ledger['applied'] = {BINDING: {'expiresAt': iso(datetime.timedelta(days=3)), 'prior': [{'entityId': 'light.porch', 'state': 'on'}], 'restored': False}}
    hass.states.async_set('light.porch', 'on')
    await bridge.run_routines(before + datetime.timedelta(days=1, minutes=2))
    assert calls == ['turn_off']
    # Ten minutes late is the most: Home Assistant back at 9 PM doesn't turn it off then.
    bridge.ledger['applied'] = {}
    await bridge.run_routines(before + datetime.timedelta(days=2, minutes=31))
    assert calls == ['turn_off']


async def test_a_schedule_at_sunset_uses_home_assistants_sunset(hass: HomeAssistant, aioclient_mock):
    hass.states.async_set('sun.sun', 'above_horizon')
    hass.states.async_set('light.porch', 'off', {'friendly_name': 'Porch light'})
    calls = light_service(hass, 'light.porch')
    kinekt(aioclient_mock, config={'entityIds': ['sun.sun']}, wait={'etag': 'e1', 'config': {'entityIds': ['sun.sun']}, 'effects': [], 'checkInSeconds': 600})
    entry = await setup(hass, aioclient_mock)
    bridge = entry.runtime_data
    assert await until(lambda: bridge.etag == 'e1')
    bridge.revoked = True
    sunset = datetime.datetime(2026, 11, 18, 23, 40, tzinfo=datetime.timezone.utc)
    bridge.sun = lambda kind, day: sunset if kind == 'sunset' else None
    routine = {'id': ROUTINE, 'entityIds': ['light.porch'], 'command': 'turn_on', 'at': {'kind': 'sunset', 'offsetMinutes': 30}, 'days': [3]}
    await bridge.take_lights(logic.validate_lights(lights(routines=[routine])))
    await bridge.run_routines(sunset + datetime.timedelta(minutes=29))
    assert calls == []
    await bridge.run_routines(sunset + datetime.timedelta(minutes=31))
    assert calls == ['turn_on']


def test_kinekt_light_settings_are_checked():
    """Only lights and switches, real IDs and times; anything else is refused whole."""
    good = lights([{'id': COMMAND, 'entityId': 'light.porch', 'command': 'turn_on', 'expiresAt': iso(datetime.timedelta(minutes=2))}],
                  [{'id': ROUTINE, 'entityIds': ['switch.coffee'], 'command': 'turn_off', 'at': {'kind': 'sunrise', 'offsetMinutes': -30}, 'days': [1]}])
    assert logic.validate_lights(good)['routines'][0]['at'] == {'kind': 'sunrise', 'offsetMinutes': -30}
    assert logic.validate_lights(None) == {'timezone': None, 'commands': [], 'routines': []}
    for bad in (
        lights([{'id': COMMAND, 'entityId': 'lock.front_door', 'command': 'turn_off', 'expiresAt': iso(datetime.timedelta(minutes=2))}]),
        lights(routines=[{'id': ROUTINE, 'entityIds': ['light.porch'], 'command': 'toggle', 'at': {'kind': 'time', 'time': '20:30'}, 'days': [1]}]),
        lights(routines=[{'id': ROUTINE, 'entityIds': ['light.porch'], 'command': 'turn_on', 'at': {'kind': 'time', 'time': '25:00'}, 'days': [1]}]),
        lights(routines=[{'id': ROUTINE, 'entityIds': ['light.porch'], 'command': 'turn_on', 'at': {'kind': 'time', 'time': '20:00'}, 'days': [8]}]),
        lights(timezone='Mars/Olympus'),
    ):
        try:
            logic.validate_lights(bad)
        except logic.BridgeError:
            continue
        raise AssertionError(f'accepted {bad}')
