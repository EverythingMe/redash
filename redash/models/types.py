from sqlalchemy.ext.indexable import index_property
from sqlalchemy.ext.mutable import Mutable
from sqlalchemy.types import TypeDecorator
from sqlalchemy_utils import EncryptedType

from redash.utils import json_dumps, json_loads
from redash.utils.configuration import ConfigurationContainer

from .base import db


class Configuration(TypeDecorator):
    impl = db.Text

    def process_bind_param(self, value, dialect):
        return value.to_json()

    def process_result_value(self, value, dialect):
        return ConfigurationContainer.from_json(value)


class EncryptedConfiguration(EncryptedType):
    def process_bind_param(self, value, dialect):
        return super(EncryptedConfiguration, self).process_bind_param(value.to_json(), dialect)

    def process_result_value(self, value, dialect):
        return ConfigurationContainer.from_json(
            super(EncryptedConfiguration, self).process_result_value(value, dialect)
        )


# Utilized for cases when JSON size is bigger than JSONB (255MB) or JSON (10MB) limit
class JSONText(TypeDecorator):
    impl = db.Text

    def process_bind_param(self, value, dialect):
        if value is None:
            return value

        return json_dumps(value)

    def process_result_value(self, value, dialect):
        if not value:
            return value
        return json_loads(value)


class MutableDict(Mutable, dict):
    @classmethod
    def coerce(cls, key, value):
        "Convert plain dictionaries to MutableDict."

        if not isinstance(value, MutableDict):
            if isinstance(value, dict):
                return MutableDict(value)
            # this call will raise ValueError
            return Mutable.coerce(key, value)
        return value

    def __setitem__(self, key, value):
        "Detect dictionary set events and emit change events."

        dict.__setitem__(self, key, value)
        self.changed()

    def __delitem__(self, key):
        "Detect dictionary del events and emit change events."

        dict.__delitem__(self, key)
        self.changed()


class MutableList(Mutable, list):
    def append(self, value):
        list.append(self, value)
        self.changed()

    def remove(self, value):
        list.remove(self, value)
        self.changed()

    @classmethod
    def coerce(cls, key, value):
        if not isinstance(value, MutableList):
            if isinstance(value, list):
                return MutableList(value)
            return Mutable.coerce(key, value)
        return value


class json_cast_property(index_property):
    """
    A SQLAlchemy index property that is able to cast the
    entity attribute as the specified cast type. Useful
    for JSON and JSONB colums for easier querying/filtering.
    """

    def __init__(self, cast_type, *args, **kwargs):
        super(json_cast_property, self).__init__(*args, **kwargs)
        self.cast_type = cast_type

    def expr(self, model):
        expr = super(json_cast_property, self).expr(model)
        return expr.astext.cast(self.cast_type)
