#!/usr/bin/env python3
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

import requests
import simplejson

github_token = os.environ["GITHUB_TOKEN"]
auth = (github_token, "x-oauth-basic")
repo = "getredash/redash"


def _github_request(method, path, params=None, headers={}):
    if urlparse(path).hostname != "api.github.com":
        url = "https://api.github.com/{}".format(path)
    else:
        url = path

    if params is not None:
        params = simplejson.dumps(params)

    response = requests.request(method, url, data=params, auth=auth)
    return response


def exception_from_error(message, response):
    return Exception("({}) {}: {}".format(response.status_code, message, response.json().get("message", "?")))


def rc_tag_name(version):
    return "v{}-rc".format(version)


def get_rc_release(version):
    tag = rc_tag_name(version)
    response = _github_request("get", "repos/{}/releases/tags/{}".format(repo, tag))

    if response.status_code == 404:
        return None
    if response.status_code == 200:
        return response.json()

    raise exception_from_error("Unknown error while looking RC release: ", response)


def create_release(version, commit_sha):
    tag = rc_tag_name(version)

    params = {
        "tag_name": tag,
        "name": "{} - RC".format(version),
        "target_commitish": commit_sha,
        "prerelease": True,
    }

    response = _github_request("post", "repos/{}/releases".format(repo), params)

    if response.status_code != 201:
        raise exception_from_error("Failed creating new release", response)

    return response.json()


def upload_asset(release, filepath):
    upload_url = release["upload_url"].replace("{?name,label}", "")
    filename = filepath.split("/")[-1]

    with open(filepath) as file_content:
        headers = {"Content-Type": "application/gzip"}
        response = requests.post(
            upload_url, file_content, params={"name": filename}, headers=headers, auth=auth, verify=False
        )

    if response.status_code != 201:  # not 200/201/...
        raise exception_from_error("Failed uploading asset", response)

    return response


def remove_previous_builds(release):
    for asset in release["assets"]:
        response = _github_request("delete", asset["url"])
        if response.status_code != 204:
            raise exception_from_error("Failed deleting asset", response)


def get_changelog(commit_sha):
    latest_release = _github_request("get", "repos/{}/releases/latest".format(repo))
    if latest_release.status_code != 200:
        raise exception_from_error("Failed getting latest release", latest_release)

    latest_release = latest_release.json()
    previous_sha = latest_release["target_commitish"]

    args = [
        "git",
        "--no-pager",
        "log",
        "--merges",
        "--grep",
        "Merge pull request",
        '--pretty=format:"%h|%s|%b|%p"',
        "{}...{}".format(previous_sha, commit_sha),
    ]
    log = subprocess.check_output(args)
    changes = ["Changes since {}:".format(latest_release["name"])]

    for line in log.split("\n"):
        try:
            sha, subject, body, parents = line[1:-1].split("|")
        except ValueError:
            continue

        try:
            pull_request = re.match(r"Merge pull request #(\d+)", subject).groups()[0]
            pull_request = " #{}".format(pull_request)
        except Exception:
            pull_request = ""

        author = subprocess.check_output(["git", "log", "-1", '--pretty=format:"%an"', parents.split(" ")[-1]])[1:-1]

        changes.append("{}{}: {} ({})".format(sha, pull_request, body.strip(), author))

    return "\n".join(changes)


def update_release_commit_sha(release, commit_sha):
    params = {
        "target_commitish": commit_sha,
    }

    response = _github_request("patch", "repos/{}/releases/{}".format(repo, release["id"]), params)

    if response.status_code != 200:
        raise exception_from_error("Failed updating commit sha for existing release", response)

    return response.json()


def update_release(version, build_filepath, commit_sha):
    try:
        release = get_rc_release(version)
        if release:
            release = update_release_commit_sha(release, commit_sha)
        else:
            release = create_release(version, commit_sha)

        print("Using release id: {}".format(release["id"]))

        remove_previous_builds(release)
        response = upload_asset(release, build_filepath)

        changelog = get_changelog(commit_sha)

        response = _github_request("patch", release["url"], {"body": changelog})
        if response.status_code != 200:
            raise exception_from_error("Failed updating release description", response)

    except Exception as ex:
        print(ex)


if __name__ == "__main__":
    commit_sha = sys.argv[1]
    version = sys.argv[2]
    filepath = sys.argv[3]

    # TODO: make sure running from git directory & remote = repo
    update_release(version, filepath, commit_sha)
