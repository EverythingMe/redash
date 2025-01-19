import logging

import requests
from authlib.integrations.flask_client import OAuth
from flask import Blueprint, flash, redirect, request, session, url_for

from redash import models, settings
from redash.authentication import (
    create_and_login_user,
    get_next_path,
    logout_and_redirect_to_index,
)
from redash.authentication.org_resolving import current_org


def create_oidc_blueprint(app):
    if not settings.OIDC_ENABLED:
        return None

    oauth = OAuth(app)

    logger = logging.getLogger("oidc")
    blueprint = Blueprint("oidc", __name__)

    def get_oidc_config(url):
        resp = requests.get(url=url)
        if resp.status_code != 200:
            logger.warning(
                f"Unable to get configuration details (response code {resp.status_code}). Configuration URL: {url}"
            )
            return None
        return resp.json()

    oidc_config = get_oidc_config(settings.OIDC_ISSUER_URL)
    oauth = OAuth(app)
    oauth.register(
        name="oidc",
        server_metadata_url=settings.OIDC_ISSUER_URL,
        client_kwargs={
            "scope": "openid email profile",
        },
    )

    def get_user_profile(access_token):
        headers = {"Authorization": "Bearer {}".format(access_token)}
        response = requests.get(oidc_config["userinfo_endpoint"], headers=headers)

        if response.status_code == 401:
            logger.warning("Failed getting user profile (response code 401).")
            return None

        return response.json()

    @blueprint.route("/<org_slug>/oidc", endpoint="authorize_org")
    def org_login(org_slug):
        session["org_slug"] = current_org.slug
        return redirect(url_for(".authorize", next=request.args.get("next", None)))

    @blueprint.route("/oidc", endpoint="authorize")
    def login():
        redirect_uri = url_for(".callback", _external=True)

        next_path = request.args.get("next", url_for("redash.index", org_slug=session.get("org_slug")))
        logger.debug("Callback url: %s", redirect_uri)
        logger.debug("Next is: %s", next_path)

        session["next_url"] = next_path

        return oauth.oidc.authorize_redirect(redirect_uri)

    @blueprint.route("/oidc/callback", endpoint="callback")
    def authorized():
        logger.debug("Authorized user inbound")

        resp = oauth.oidc.authorize_access_token()
        user = resp.get("userinfo")
        if user:
            session["user"] = user

        access_token = resp["access_token"]

        if access_token is None:
            logger.warning("Access token missing in call back request.")
            flash("Validation error. Please retry.")
            return redirect(url_for("redash.login"))

        profile = get_user_profile(access_token)
        if profile is None:
            flash("Validation error. Please retry.")
            return redirect(url_for("redash.login"))

        if "org_slug" in session:
            org = models.Organization.get_by_slug(session.pop("org_slug"))
        else:
            org = current_org

        user = create_and_login_user(org, profile["name"], profile["email"])
        if user is None:
            return logout_and_redirect_to_index()

        unsafe_next_path = session.get("next_url") or url_for("redash.index", org_slug=org.slug)
        next_path = get_next_path(unsafe_next_path)

        return redirect(next_path)

    return blueprint
