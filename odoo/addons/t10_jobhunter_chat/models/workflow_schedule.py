"""Deterministic local-time validation. No guessed zones or external calendar claims."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re
from odoo.exceptions import ValidationError

FIELDS = {'start', 'duration_minutes', 'participant_count', 'participants', 'timezone', 'candidate_email'}
EMAIL = re.compile(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+')


def local_time(value, zone):
    if not isinstance(value, str) or len(value) > 64 or not isinstance(zone, str) or len(zone) > 100:
        raise ValueError('Provide an exact local datetime and IANA timezone.')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?', value):
        raise ValueError('Provide an exact ISO local datetime including the clock time.')
    local = datetime.fromisoformat(value)
    if local.tzinfo is not None:
        raise ValueError('Use local datetime without an offset and an explicit IANA timezone.')
    tz = ZoneInfo(zone)
    instants = {local.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc) for fold in (0, 1)
                if local.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == local}
    if len(instants) != 1:
        raise ValueError('The local time is nonexistent or ambiguous due to DST. Provide an unambiguous replacement.')
    return instants.pop()


def validate_schedule(schedule, calendar_check=None):
    if not isinstance(schedule, dict) or set(schedule) - FIELDS:
        raise ValidationError('Invalid scheduling fields.')
    missing = [key for key in ('start', 'duration_minutes', 'participant_count', 'participants', 'timezone', 'candidate_email') if not schedule.get(key)]
    issues, displays = [], []
    start = end = None
    duration, count = schedule.get('duration_minutes'), schedule.get('participant_count')
    if duration is not None and (type(duration) is not int or not 15 <= duration <= 180):
        issues.append('Duration must be between 15 and 180 minutes.')
    if count is not None and (type(count) is not int or not 1 <= count <= 10):
        issues.append('Participant count must be between 1 and 10 and includes the candidate.')
    email = schedule.get('candidate_email')
    if email is not None and (not isinstance(email, str) or len(email) > 254 or not EMAIL.fullmatch(email)):
        issues.append('Provide a valid candidate email.')
    if schedule.get('start') and schedule.get('timezone'):
        try:
            start = local_time(schedule['start'], schedule['timezone'])
            if type(duration) is int and 15 <= duration <= 180:
                end = start + timedelta(minutes=duration)
        except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
            issues.append(str(exc) if isinstance(exc, ValueError) else 'Provide a valid IANA timezone.')
    participants = schedule.get('participants', [])
    if not isinstance(participants, list) or len(participants) > 10:
        raise ValidationError('Invalid participants.')
    if type(count) is int and len(participants) != count:
        issues.append('Participant count must match the named attendee list.')
    seen = set()
    for index, participant in enumerate(participants):
        if not isinstance(participant, dict) or set(participant) != {'name', 'email', 'timezone', 'availability', 'user_id'}:
            raise ValidationError('Invalid participant fields.')
        name, address, zone = participant['name'], participant['email'], participant['timezone']
        if not isinstance(name, str) or not name.strip() or len(name) > 200 or not isinstance(address, str) or len(address) > 254 or not EMAIL.fullmatch(address):
            issues.append(f'Participant {index + 1} needs a name and valid email.')
            continue
        if address.casefold() in seen:
            issues.append('Each attendee must have a distinct email.')
        seen.add(address.casefold())
        windows, internal = participant['availability'], participant['user_id']
        if internal is not None and (type(internal) is not int or internal < 1):
            raise ValidationError('Invalid internal calendar user.')
        if not isinstance(windows, list) or len(windows) > 50:
            raise ValidationError('Invalid availability.')
        try:
            if not isinstance(zone, str) or not zone:
                raise ValueError('Every attendee needs an explicit IANA timezone.')
            tz = ZoneInfo(zone)
            available = False
            for window in windows:
                if not isinstance(window, dict) or set(window) != {'start', 'end'}:
                    raise ValidationError('Invalid availability window.')
                first, last = local_time(window['start'], zone), local_time(window['end'], zone)
                if first >= last:
                    raise ValueError('Availability must end after it starts.')
                if start and end and first <= start and end <= last:
                    available = True
            if start and end:
                displays.append({'name': name, 'email': address, 'timezone': zone,
                                 'start': start.astimezone(tz).isoformat(), 'end': end.astimezone(tz).isoformat()})
                verified = calendar_check(participant, start, end) if internal and calendar_check else None
                if windows and not available:
                    issues.append(f'The requested slot does not fit {name}\u2019s supplied availability.')
                elif not windows and verified is not True:
                    issues.append(f'Provide availability for {name}; their calendar is not verified.')
                if verified is False:
                    issues.append(f'{name} has a calendar conflict or an unreadable calendar.')
        except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
            issues.append(str(exc) if isinstance(exc, ValueError) else f'Provide a valid IANA timezone for {name}.')
    if email and isinstance(email, str) and email.casefold() not in seen and participants:
        issues.append('The candidate email must appear in the attendee list.')
    return {'status': 'needs_input' if missing or issues else 'ready', 'missing': missing, 'issues': issues,
            'schedule': schedule, 'starts_at_utc': start.isoformat().replace('+00:00', 'Z') if start else None,
            'ends_at_utc': end.isoformat().replace('+00:00', 'Z') if end else None,
            'display_times': displays, 'guide_ready': False, 'booking': None,
            'email_preview': {'to': email, 'subject': 'Interview invitation',
                              'body': f'You are invited to a typed interview. Requested start: {schedule.get("start", "pending")} ({schedule.get("timezone", "pending")}). Duration: {duration or "pending"} minutes. A private expiring link will be included after confirmation.'}}
