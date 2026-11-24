# See https://zulip.readthedocs.io/en/latest/translating/internationalization.html

import logging
import os
from functools import lru_cache
from importlib import import_module
from typing import Any

import orjson
from django.conf import settings
from django.http import HttpRequest
from django.utils import translation
from django.utils.translation.trans_real import parse_accept_lang_header

from zerver.lib.request import RequestNotes
from zerver.models import Realm

try:
    _rust_locale_catalog = (
        import_module("_zulip_backend")
        if os.environ.get("ZULIP_RUST_LOCALE_CATALOG") == "1"
        else None
    )
except ImportError:
    _rust_locale_catalog = None

_lookup_catalog_display_name = getattr(_rust_locale_catalog, "lookup_catalog_display_name", None)
_project_catalog_language_codes = getattr(
    _rust_locale_catalog, "project_catalog_language_codes", None
)


@lru_cache(None)
def get_language_list() -> list[dict[str, Any]]:
    path = os.path.join(settings.DEPLOY_ROOT, "locale", "language_name_map.json")
    with open(path, "rb") as reader:
        languages = orjson.loads(reader.read())
        return languages["name_map"]


def get_language_name(code: str) -> str:
    language_list = get_language_list()
    native = (
        _lookup_catalog_display_name(language_list, code)
        if _lookup_catalog_display_name is not None
        else None
    )
    if native is None:
        for lang in language_list:
            if code in (lang["code"], lang["locale"]):
                return lang["name"]
    else:
        (name,) = native
        if name is not None:
            return name
    # Log problem, but still return a name
    logging.error("Unknown language code '%s'", code)
    return "Unknown"


def get_available_language_codes() -> list[str]:
    language_list = get_language_list()
    always_visible = "en"
    min_percent = 5
    if _project_catalog_language_codes is not None:
        native = _project_catalog_language_codes(language_list, always_visible, min_percent)
        if native is not None:
            return native

    codes = [
        language["code"]
        for language in language_list
        if language["code"] == always_visible or language["percent_translated"] >= min_percent
    ]
    return codes


def get_language_translation_data(language: str) -> dict[str, str]:
    if language == "en":
        return {}
    locale = translation.to_locale(language)
    path = os.path.join(settings.DEPLOY_ROOT, "locale", locale, "translations.json")
    try:
        with open(path, "rb") as reader:
            return orjson.loads(reader.read())
    except FileNotFoundError:
        print(f"Translation for {language} not found at {path}")
        return {}


def get_and_set_request_language(
    request: HttpRequest, user_configured_language: str, testing_url_language: str | None = None
) -> str:
    # We pick a language for the user as follows:
    # * First priority is the language in the URL, for debugging.
    # * If not in the URL, we use the language from the user's settings.
    request_language = testing_url_language
    if request_language is None:
        request_language = user_configured_language
    translation.activate(request_language)

    # We also want to save the language to the user's cookies, so that
    # something reasonable will happen in logged-in portico pages.
    # We accomplish that by setting a flag on the request which signals
    # to LocaleMiddleware to set the cookie on the response.
    RequestNotes.get_notes(request).set_language = translation.get_language()

    return request_language


def get_browser_language_code(request: HttpRequest) -> str | None:
    accept_lang_header = request.headers.get("Accept-Language")
    if accept_lang_header is None:
        return None

    available_language_codes = get_available_language_codes()
    for accept_lang, priority in parse_accept_lang_header(accept_lang_header):
        if accept_lang == "*":
            return None
        if accept_lang in available_language_codes:
            return accept_lang
    return None


def get_default_language_for_new_user(realm: Realm, *, request: HttpRequest | None) -> str:
    if request is None:
        # Users created via the API or LDAP will not have a
        # browser/request associated with them, and should just use
        # the realm's default language.
        return realm.default_language

    browser_language_code = get_browser_language_code(request)
    if browser_language_code is not None:
        return browser_language_code
    return realm.default_language


def get_default_language_for_anonymous_user(request: HttpRequest) -> str:
    browser_language_code = get_browser_language_code(request)
    if browser_language_code is not None:
        return browser_language_code
    return settings.LANGUAGE_CODE
