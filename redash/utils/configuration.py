import copy

import jsonschema
from jsonschema import ValidationError
from sqlalchemy.ext.mutable import Mutable

from redash.utils import json_dumps, json_loads

SECRET_PLACEHOLDER = "--------"


class ConfigurationContainer(Mutable):
    @classmethod
    def coerce(cls, key, value):
        if not isinstance(value, ConfigurationContainer):
            if isinstance(value, dict):
                return ConfigurationContainer(value)
            # this call will raise ValueError
            return Mutable.coerce(key, value)
        return value

    def __init__(self, config, schema=None):
        self._config = config
        self.set_schema(schema)

    def set_schema(self, schema):
        configuration_schema = copy.deepcopy(schema)
        if isinstance(configuration_schema, dict):
            for prop in configuration_schema.get("properties", {}).values():
                if "extendedEnum" in prop:
                    prop["enum"] = [option["value"] for option in prop["extendedEnum"]]
                    del prop["extendedEnum"]
        self._schema = configuration_schema

    @property
    def schema(self):
        if self._schema is None:
            raise RuntimeError("Schema missing.")

        return self._schema

    def is_valid(self):
        try:
            self.validate()
        except (ValidationError, ValueError):
            return False

        return True

    def validate(self):
        jsonschema.validate(self._config, self._schema)

    def to_json(self):
        return json_dumps(self._config, sort_keys=True)

    def iteritems(self):
        return self._config.items()

    def to_dict(self, mask_secrets=False):
        if mask_secrets is False or "secret" not in self.schema:
            return self._config

        config = self._config.copy()
        for key in config:
            if key in self.schema["secret"]:
                config[key] = SECRET_PLACEHOLDER

        return config

    def update(self, new_config):
        jsonschema.validate(new_config, self.schema)

        config = {}
        for k, v in new_config.items():
            if k in self.schema.get("secret", []) and v == SECRET_PLACEHOLDER:
                config[k] = self[k]
            else:
                config[k] = v

        self._config = config
        self.changed()

    def get(self, *args, **kwargs):
        return self._config.get(*args, **kwargs)

    def __setitem__(self, key, value):
        self._config[key] = value
        self.changed()

    def __getitem__(self, item):
        if item in self._config:
            return self._config[item]

        raise KeyError(item)

    def __contains__(self, item):
        return item in self._config

    @classmethod
    def from_json(cls, config_in_json):
        if config_in_json is None:
            return cls({})
        return cls(json_loads(config_in_json))
