import os


def fix_assets_path(path):
    fullpath = os.path.join(os.path.dirname(__file__), "../", path)
    return fullpath


def array_from_string(s):
    array = s.split(',')
    if "" in array:
        array.remove("")

    return array


def set_from_string(s):
    return set(array_from_string(s))


def parse_boolean(s):
    """Takes a string and returns the equivalent as a boolean value."""
    s = s.strip().lower()
    if s in ('yes', 'true', 'on', '1'):
        return True
    elif s in ('no', 'false', 'off', '0', 'none'):
        return False
    else:
        raise ValueError('Invalid boolean value %r' % s)


def int_or_none(value):
    if value is None:
        return value

    return int(value)
