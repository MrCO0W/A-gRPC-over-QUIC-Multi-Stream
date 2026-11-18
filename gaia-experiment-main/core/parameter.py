import logging
import os

_MISSING = object()


class Parameter(object):
    """
    Generic handler for parameters stored in configuration files

    Attributes:
        parameters          dictionary containing the value for configuration parameters
    Base class to handle parameter files with key-value pairs separated by colons.
    Supports comments starting with # and parses values into strings or lists.
    """

    def __init__(self, filename):
        self.filename = filename
        self.parameters = {}
        self.default_parameters = {}
        if self.filename is not None:
            self.load_parameters()

    def load_parameters(self):
        if not os.path.exists(self.filename):
            raise FileNotFoundError(
                "Parameter file not found: {}".format(self.filename))
        with open(self.filename, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = parts[1].strip()
                        if key in self.parameters:
                            if isinstance(self.parameters[key], list):
                                self.parameters[key].append(value)
                            else:
                                self.parameters[key] = [self.parameters[key], value]
                        else:
                            self.parameters[key] = value
                except ValueError as e:
                    logging.warning(
                        "Got error '{}' for line '{}'; ignore it".format(e, line))

    def get(self, key, default=_MISSING):
        """
        Get the value of the parameter with key `key`.
        If not defined by the configuration file, return default if provided,
        or the value from default_parameters.
        Raise Exception if the parameter has no default value and is absent.
        """
        val = self.parameters.get(key)
        if val is None:
            if default is not _MISSING:
                return default
            elif key in self.default_parameters:
                return self.default_parameters[key]
            else:
                raise Exception("Parameter not found " + key)
        else:
            return val

    def get_list(self, key, default=None):
        """
        Always return a list for key. If key is missing, return default or [].
        If key has a single string value, wrap it in a list.
        """
        if default is None:
            default = []
        if key not in self.parameters:
            return default
        val = self.parameters[key]
        if isinstance(val, list):
            return val
        return [val]

    def __str__(self):
        return self.parameters.__str__()

