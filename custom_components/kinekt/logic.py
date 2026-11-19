"""Checks and plans shared with the standalone bridge (apps/home-bridge/bridge.py), without Home Assistant imports.

Kinekt's answers are checked again here; effects are allowlisted light and switch commands whose
original states are restored at a deadline. tests/home-assistant-logic.py runs the same cases
against both copies so they cannot drift.
"""
import datetime
import re
import uuid
import zoneinfo

ENTITY = re.compile(r'[a-z][a-z0-9_]*\.[a-z0-9_]+')
COMMANDS = {'turn_on', 'turn_off'}
CONTROLLABLE = {'light', 'switch'}
CATALOG_DOMAINS = {'sensor', 'binary_sensor', 'light', 'fan', 'switch', 'weather', 'sun'}


class BridgeError(Exception):
    pass


def now_iso(now=None):
    return (now or datetime.datetime.now(datetime.timezone.utc)).isoformat().replace('+00:00', 'Z')


def valid_entity(entity):
    return isinstance(entity, str) and len(entity) <= 160 and bool(ENTITY.fullmatch(entity))


def shareable(entity):
    """Only the kinds of reading the device list offers ever leave Home Assistant, whatever Kinekt asks for
    (no people, locations or anything else)."""
    return valid_entity(entity) and entity.split('.')[0] in CATALOG_DOMAINS


def validate_selection(selection):
    """The selected entity IDs from /v1/home-bridge/config."""
    if not isinstance(selection, dict):
        raise BridgeError('Kinekt returned an invalid home selection.')
    selected = selection.get('entityIds')
    if not isinstance(selected, list) or not 1 <= len(selected) <= 30 or len(set(selected)) != len(selected) or not all(valid_entity(e) for e in selected):
        raise BridgeError('Kinekt returned an invalid home entity selection.')
    return selected


def project_state(entity, state, attributes):
    """Only the state text and a measurement unit leave Home Assistant."""
    if not isinstance(state, str) or len(state) > 255:
        raise BridgeError('Invalid home state.')
    unit = attributes.get('unit_of_measurement') if isinstance(attributes, dict) else None
    if not isinstance(unit, str) or len(unit) > 40:
        unit = None
    return {'entityId': entity, 'state': state, 'unit': unit}


def catalog_entry(entity, attributes):
    """A supported entity's ID and friendly name for the owner's device list, or None."""
    if not valid_entity(entity) or entity.split('.')[0] not in CATALOG_DOMAINS:
        return None
    name = attributes.get('friendly_name') if isinstance(attributes, dict) else None
    if isinstance(name, str):
        name = name.strip()
    if not isinstance(name, str) or not 1 <= len(name) <= 80 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        name = entity[:80]
    return {'entityId': entity, 'name': name}


def catalog_request(pending, now=None):
    """The operation ID of a device list request that hasn't expired."""
    if not isinstance(pending, dict):
        raise BridgeError('Invalid device list request.')
    try:
        operation = str(uuid.UUID(pending['operationId']))
        expires = datetime.datetime.fromisoformat(pending['expiresAt'].replace('Z', '+00:00'))
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise BridgeError('Invalid device list request.') from error
    if expires.tzinfo is None or expires <= (now or datetime.datetime.now(datetime.timezone.utc)):
        raise BridgeError('Device list request expired.')
    return operation


def valid_action(action):
    if not isinstance(action, dict):
        return False
    entity = action.get('entityId')
    return valid_entity(entity) and entity.split('.', 1)[0] in CONTROLLABLE and action.get('command') in COMMANDS


# Up to 40 effects at once (a camera mute is one effect per camera); Kinekt sends 10 unless asked with ?max=.
MAX_EFFECTS = 40


def validate_desired(payload):
    effects = payload.get('effects') if isinstance(payload, dict) else None
    if not isinstance(effects, list) or len(effects) > MAX_EFFECTS:
        raise BridgeError('Kinekt returned an invalid home effect schedule.')
    cleaned = []
    seen = set()
    devices = set()
    for item in effects:
        if not isinstance(item, dict):
            raise BridgeError('Kinekt returned an invalid home effect schedule.')
        binding = item.get('bindingId')
        expires = item.get('expiresAt')
        actions = item.get('actions')
        if not isinstance(binding, str) or binding in seen or not isinstance(expires, str) or not isinstance(actions, list) or not 1 <= len(actions) <= 8 or any(not valid_action(action) for action in actions):
            raise BridgeError('Kinekt returned an invalid home effect schedule.')
        entities = [action['entityId'] for action in actions]
        if len(set(entities)) != len(entities) or any(entity in devices for entity in entities):
            raise BridgeError('Kinekt returned overlapping home effects.')
        seen.add(binding)
        devices.update(entities)
        try:
            deadline = datetime.datetime.fromisoformat(expires.replace('Z', '+00:00'))
        except ValueError as error:
            raise BridgeError('Kinekt returned an invalid home effect schedule.') from error
        if deadline.tzinfo is None:
            raise BridgeError('Kinekt returned an invalid home effect schedule.')
        cleaned.append({'bindingId': binding, 'expiresAt': now_iso(deadline.astimezone(datetime.timezone.utc)), 'actions': [{'entityId': action['entityId'], 'command': action['command']} for action in actions]})
    return cleaned


def restore_actions(prior):
    if not isinstance(prior, list) or not 1 <= len(prior) <= 8:
        raise BridgeError('Stored home effect restore is invalid.')
    actions = []
    for item in prior:
        if not isinstance(item, dict) or item.get('state') not in ('on', 'off') or not valid_action({'entityId': item.get('entityId'), 'command': 'turn_off'}):
            raise BridgeError('Stored home effect restore is invalid.')
        actions.append({'entityId': item['entityId'], 'command': 'turn_on' if item['state'] == 'on' else 'turn_off'})
    return actions


def plan_effect_steps(ledger, desired, now):
    """Decide apply and restore steps. desired is None when Kinekt cannot be reached; that only restores deadlines already stored."""
    applied = dict(ledger.get('applied') or {})
    steps = []
    wanted = None if desired is None else {item['bindingId']: item for item in desired}
    if wanted is not None:
        for binding, item in wanted.items():
            current = applied.get(binding)
            if current and not current.get('restored'):
                current = dict(current)
                current['expiresAt'] = item['expiresAt']
                applied[binding] = current
    for binding, current in list(applied.items()):
        if current.get('restored'):
            continue
        deadline = datetime.datetime.fromisoformat(current['expiresAt'].replace('Z', '+00:00'))
        cancelled = wanted is not None and binding not in wanted
        if cancelled or deadline <= now or current.get('applying'):
            steps.append({'phase': 'restore', 'bindingId': binding, 'expiresAt': current['expiresAt'], 'actions': restore_actions(current['prior'])})
    if wanted is not None:
        for binding, item in wanted.items():
            current = applied.get(binding)
            deadline = datetime.datetime.fromisoformat(item['expiresAt'].replace('Z', '+00:00'))
            if deadline <= now or (current and not current.get('restored')) or any(step['bindingId'] == binding for step in steps):
                continue
            steps.append({'phase': 'apply', 'bindingId': binding, 'expiresAt': item['expiresAt'], 'actions': item['actions']})
    return {**ledger, 'applied': applied}, steps


def pending(ledger):
    return any(not item.get('restored') for item in (ledger.get('applied') or {}).values())


# Lights from Kinekt (1.3; TK, Nov 18: "simple controls, schedules… Turn that light off now"). Grown-ups in Kinekt
# turn a shared light or switch on or off now, or on set days at a time or at sunset/sunrise. Schedules are kept here
# so they run with no connection; a light a trip or season is holding is left alone (TK: the trip wins).
MAX_COMMANDS = 20
MAX_ROUTINES = 30
# A schedule runs up to ten minutes late (Home Assistant restarting at 8:31 still turns the kitchen off), never later.
GRACE_MINUTES = 10
CLOCK = re.compile(r'([01]\d|2[0-3]):[0-5]\d')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def _when(value):
    moment = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if moment.tzinfo is None:
        raise ValueError('naive')
    return moment


def validate_lights(payload):
    """What Kinekt says to do with lights. None (a Kinekt before lights): nothing to do."""
    if payload is None:
        return {'timezone': None, 'commands': [], 'routines': []}
    error = BridgeError('Kinekt returned invalid light settings.')
    if not isinstance(payload, dict):
        raise error
    zone = payload.get('timezone')
    try:
        if not isinstance(zone, str) or len(zone) > 64:
            raise ValueError(zone)
        zoneinfo.ZoneInfo(zone)
    except (ValueError, zoneinfo.ZoneInfoNotFoundError) as problem:
        raise error from problem
    commands, routines = payload.get('commands'), payload.get('routines')
    if not isinstance(commands, list) or len(commands) > MAX_COMMANDS or not isinstance(routines, list) or len(routines) > MAX_ROUTINES:
        raise error
    clean_commands = []
    for item in commands:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not UUID.fullmatch(item['id']) or not valid_action(item) or not isinstance(item.get('expiresAt'), str):
            raise error
        try:
            expires = _when(item['expiresAt'])
        except ValueError as problem:
            raise error from problem
        clean_commands.append({'id': item['id'], 'entityId': item['entityId'], 'command': item['command'], 'expiresAt': now_iso(expires.astimezone(datetime.timezone.utc))})
    clean_routines = []
    for item in routines:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not UUID.fullmatch(item['id']) or item.get('command') not in COMMANDS:
            raise error
        entities, days, at = item.get('entityIds'), item.get('days'), item.get('at')
        if not isinstance(entities, list) or not 1 <= len(entities) <= 8 or len(set(entities)) != len(entities) or any(not valid_action({'entityId': e, 'command': item['command']}) for e in entities):
            raise error
        if not isinstance(days, list) or not 1 <= len(days) <= 7 or len(set(days)) != len(days) or any(type(d) is not int or not 1 <= d <= 7 for d in days):
            raise error
        if not isinstance(at, dict):
            raise error
        if at.get('kind') == 'time' and isinstance(at.get('time'), str) and CLOCK.fullmatch(at['time']):
            clean_at = {'kind': 'time', 'time': at['time']}
        elif at.get('kind') in ('sunset', 'sunrise') and type(at.get('offsetMinutes')) is int and -180 <= at['offsetMinutes'] <= 180:
            clean_at = {'kind': at['kind'], 'offsetMinutes': at['offsetMinutes']}
        else:
            raise error
        clean_routines.append({'id': item['id'], 'entityIds': list(entities), 'command': item['command'], 'at': clean_at, 'days': sorted(days)})
    return {'timezone': zone, 'commands': clean_commands, 'routines': clean_routines}


def due_commands(commands, done, now):
    """On/off-now requests not done yet and not expired (an old tap never flips a light much later)."""
    return [c for c in commands if c['id'] not in done and _when(c['expiresAt']) > now]


def routine_time(routine, day, zone, sun):
    """When a schedule runs on `day` (a date in the family's time zone): its clock time there, or that day's sunset or
    sunrise (`sun(kind, day)` gives an aware datetime, or None when Home Assistant doesn't know) with its offset."""
    at = routine['at']
    if at['kind'] == 'time':
        hour, minute = (int(part) for part in at['time'].split(':'))
        return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=zoneinfo.ZoneInfo(zone))
    moment = sun(at['kind'], day)
    return None if moment is None else moment + datetime.timedelta(minutes=at['offsetMinutes'])


def due_routines(routines, ran, now, zone, sun, held):
    """Schedules to run now: today is one of their days, their time came within GRACE_MINUTES, and they haven't run
    today. `ran`: routine ID → the last day it ran. Devices in `held` (a trip or season holding them) are left alone.
    Returns [{'routineId', 'day', 'actions'}]; a schedule whose devices are all held still counts as run."""
    if not zone:
        return []
    today = now.astimezone(zoneinfo.ZoneInfo(zone)).date()
    out = []
    for routine in routines:
        if today.isoweekday() not in routine['days'] or ran.get(routine['id']) == today.isoformat():
            continue
        moment = routine_time(routine, today, zone, sun)
        if moment is None or not moment <= now < moment + datetime.timedelta(minutes=GRACE_MINUTES):
            continue
        out.append({'routineId': routine['id'], 'day': today.isoformat(),
                    'actions': [{'entityId': e, 'command': routine['command']} for e in routine['entityIds'] if e not in held]})
    return out


def held_devices(ledger):
    """Devices a running effect (a trip, a season, a camera mute) is holding."""
    out = set()
    for item in (ledger.get('applied') or {}).values():
        if not item.get('restored'):
            out.update(prior.get('entityId') for prior in item.get('prior') or [] if isinstance(prior, dict))
    return out
