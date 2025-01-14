import { isNil, isObject, extend, keys, map, omit, pick, uniq, get } from "lodash";
import React, { useCallback } from "react";
import Modal from "antd/lib/modal";
import { Query } from "@/services/query";
import notification from "@/services/notification";
import useImmutableCallback from "@/lib/hooks/useImmutableCallback";
import { policy } from "@/services/policy";

class SaveQueryError extends Error {
  constructor(message, detailedMessage = null) {
    super(message);
    this.detailedMessage = detailedMessage;
  }
}

class SaveQueryConflictError extends SaveQueryError {
  constructor() {
    super(
      "Changes not saved",
      <React.Fragment>
        <div className="m-b-5">It seems like the query has been modified by another user.</div>
        <div>Please copy/backup your changes and reload this page.</div>
      </React.Fragment>
    );
  }
}

function confirmOverwrite() {
  return new Promise((resolve, reject) => {
    Modal.confirm({
      title: "Overwrite Query",
      content: (
        <React.Fragment>
          <div className="m-b-5">It seems like the query has been modified by another user.</div>
          <div>Are you sure you want to overwrite the query with your version?</div>
        </React.Fragment>
      ),
      okText: "Overwrite",
      okType: "danger",
      onOk: () => {
        resolve();
      },
      onCancel: () => {
        reject();
      },
      maskClosable: true,
      autoFocusButton: null,
    });
  });
}

function doSaveQuery(data, { canOverwrite = false } = {}) {
  // omit parameter properties that don't need to be stored
  if (isObject(data.options) && data.options.parameters) {
    data.options = {
      ...data.options,
      parameters: map(data.options.parameters, p => p.toSaveableObject()),
    };
  }

  return Query.save(data).catch(error => {
    if (get(error, "response.status") === 409) {
      if (canOverwrite) {
        return confirmOverwrite()
          .then(() => Query.save(omit(data, ["version"])))
          .catch(() => Promise.reject(new SaveQueryConflictError()));
      }
      return Promise.reject(new SaveQueryConflictError());
    }
    return Promise.reject(new SaveQueryError("Query could not be saved"));
  });
}

export default function useUpdateQuery(query, onChange) {
  const handleChange = useImmutableCallback(onChange);

  return useCallback(
    (data = null, { successMessage = "Query saved" } = {}) => {
      if (isObject(data)) {
        // Don't save new query with partial data
        if (query.isNew()) {
          handleChange(extend(query.clone(), data));
          return;
        }
        data = { ...data, id: query.id, version: query.version };
      } else {
        data = pick(query, [
          "id",
          "version",
          "schedule",
          "query",
          "description",
          "name",
          "data_source_id",
          "options",
          "latest_query_data_id",
          "is_draft",
          "tags",
          "user"
        ]);
      }

      return doSaveQuery(data, { canOverwrite: policy.canEdit(query) })
        .then(updatedQuery => {
          if (!isNil(successMessage)) {
            notification.success(successMessage);
          }
          handleChange(
            extend(
              query.clone(),
              // if server returned completely new object (currently possible only when saving new query) -
              // update all fields; otherwise pick only changed fields
              updatedQuery.id !== query.id ? updatedQuery : pick(updatedQuery, uniq(["id", "version", ...keys(data)]))
            )
          );
        })
        .catch(error => {
          const notificationOptions = {};
          if (error instanceof SaveQueryConflictError) {
            notificationOptions.duration = null;
          }
          notification.error(error.message, error.detailedMessage, notificationOptions);
        });
    },
    [query, handleChange]
  );
}
