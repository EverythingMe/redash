import functools

from flask_login import current_user
from flask_restful import abort
from funcy import flatten

view_only = True
not_view_only = False

ACCESS_TYPE_VIEW = "view"
ACCESS_TYPE_MODIFY = "modify"
ACCESS_TYPE_DELETE = "delete"

ACCESS_TYPES = (ACCESS_TYPE_VIEW, ACCESS_TYPE_MODIFY, ACCESS_TYPE_DELETE)


def has_access(obj, user, need_view_only):
    if hasattr(obj, "api_key") and user.is_api_user():
        return has_access_to_object(obj, user.id, need_view_only)
    return has_access_to_groups(obj, user, need_view_only)


def has_access_to_object(obj, api_key, need_view_only):
    if obj.api_key == api_key:
        return need_view_only
    if hasattr(obj, "dashboard_api_keys"):
        # check if api_key belongs to a dashboard containing this query
        return api_key in obj.dashboard_api_keys and need_view_only
    return False


def has_access_to_groups(obj, user, need_view_only):
    groups = obj.groups if hasattr(obj, "groups") else obj

    if "admin" in user.permissions:
        return True

    matching_groups = set(groups.keys()).intersection(user.group_ids)

    if not matching_groups:
        return False

    required_level = 1 if need_view_only else 2

    group_level = 1 if all(flatten([groups[group] for group in matching_groups])) else 2

    return required_level <= group_level


def require_access(obj, user, need_view_only):
    if not has_access(obj, user, need_view_only):
        abort(403)


class require_permissions:
    def __init__(self, permissions, allow_one=False):
        self.permissions = permissions
        self.allow_one = allow_one

    def __call__(self, fn):
            @functools.wraps(fn)
            def decorated(*args, **kwargs):
                if self.allow_one:
                    has_permissions = any([current_user.has_permission(permission) for permission in self.permissions])
                else:
                    has_permissions = current_user.has_permissions(self.permissions)
                if has_permissions:
                    return fn(*args, **kwargs)
                abort(403)

            return decorated


def require_permission(permission):
    return require_permissions((permission,))


def require_any_of_permission(permissions):
    return require_permissions(permissions, True)


def require_admin(fn):
    return require_permission("admin")(fn)


def require_super_admin(fn):
    return require_permission("super_admin")(fn)


def has_permission_or_owner(permission, object_owner_id):
    return int(object_owner_id) == current_user.id or current_user.has_permission(permission)


def is_admin_or_owner(object_owner_id):
    return has_permission_or_owner("admin", object_owner_id)


def require_permission_or_owner(permission, object_owner_id):
    if not has_permission_or_owner(permission, object_owner_id):
        abort(403)


def require_admin_or_owner(object_owner_id):
    if not is_admin_or_owner(object_owner_id):
        abort(403, message="You don't have permission to edit this resource.")


def can_modify(obj, user):
    return is_admin_or_owner(obj.user_id) or user.has_access(obj, ACCESS_TYPE_MODIFY)


def require_object_modify_permission(obj, user):
    if not can_modify(obj, user):
        abort(403)
