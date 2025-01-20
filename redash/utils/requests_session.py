# pylint: disable=missing-module-docstring
from urllib.parse import quote, quote_plus, urlencode

# pylint: disable=unused-import
from advocate.exceptions import UnacceptableAddressException  # noqa: F401

from redash import settings

if settings.ENFORCE_PRIVATE_ADDRESS_BLOCK:
    import advocate as requests_or_advocate
else:
    import requests as requests_or_advocate


class ConfiguredSession(requests_or_advocate.Session):  # noqa: E501, pylint: disable=missing-class-docstring
    def request(self, *args, **kwargs):
        if not settings.REQUESTS_ALLOW_REDIRECTS:
            kwargs.update({"allow_redirects": False})

        if "params" in kwargs:
            quoter = quote_plus if settings.URLENCODE_SPACE_AS_PLUS else quote

            kwargs["params"] = urlencode(kwargs["params"], quote_via=quoter)

        return super().request(*args, **kwargs)


requests_session = ConfiguredSession()
