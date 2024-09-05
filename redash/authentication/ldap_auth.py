import logging
import sys

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from redash import settings

try:
    from ldap3 import Connection, Server
    from ldap3.utils.conv import escape_filter_chars
except ImportError:
    if settings.LDAP_LOGIN_ENABLED:
        sys.exit(
            "The ldap3 library was not found. This is required to use LDAP authentication. Rebuild the Docker image installing the `ldap3` poetry dependency group."
        )

from redash.authentication import (
    create_and_login_user,
    get_next_path,
    logout_and_redirect_to_index,
)
from redash.authentication.org_resolving import current_org
from redash.handlers.base import org_scoped_rule

logger = logging.getLogger("ldap_auth")


blueprint = Blueprint("ldap_auth", __name__)


@blueprint.route(org_scoped_rule("/ldap/login"), methods=["GET", "POST"])
def login(org_slug=None):
    index_url = url_for("redash.index", org_slug=org_slug)
    unsafe_next_path = request.args.get("next", index_url)
    next_path = get_next_path(unsafe_next_path)

    if not settings.LDAP_LOGIN_ENABLED:
        logger.error("Cannot use LDAP for login without being enabled in settings")
        return redirect(url_for("redash.index", next=next_path))

    if current_user.is_authenticated:
        return redirect(next_path)

    if request.method == "POST":
        ldap_user = auth_ldap_user(request.form["email"], request.form["password"])

        if ldap_user is not None:
            user = create_and_login_user(
                current_org,
                ldap_user[settings.LDAP_DISPLAY_NAME_KEY][0],
                ldap_user[settings.LDAP_EMAIL_KEY][0],
            )
            if user is None:
                return logout_and_redirect_to_index()
            return redirect(next_path or url_for("redash.index"))
        flash("Incorrect credentials.")
    
    return render_template(
        "login.html",
        org_slug=org_slug,
        next=next_path,
        email=request.form.get("email", ""),
        show_password_login=True,
        username_prompt=settings.LDAP_CUSTOM_USERNAME_PROMPT,
        hide_forgot_password=True,
    )


def auth_ldap_user(username, password):
    clean_username = escape_filter_chars(username)
    server = Server(settings.LDAP_HOST_URL, use_ssl=settings.LDAP_SSL)
    if settings.LDAP_BIND_DN is not None:
        conn = Connection(
            server,
            settings.LDAP_BIND_DN,
            password=settings.LDAP_BIND_DN_PASSWORD,
            authentication=settings.LDAP_AUTH_METHOD,
            auto_bind=True,
        )
    else:
        conn = Connection(server, auto_bind=True)

    conn.search(
        settings.LDAP_SEARCH_DN,
        settings.LDAP_SEARCH_TEMPLATE % {"username": clean_username},
        attributes=[settings.LDAP_DISPLAY_NAME_KEY, settings.LDAP_EMAIL_KEY],
    )

    if len(conn.entries) == 0:
        return None

    user = conn.entries[0]

    if not conn.rebind(user=user.entry_dn, password=password):
        return None

    return user
