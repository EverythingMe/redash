import React, { useState, useEffect, useCallback } from "react";
import { axios } from "@/services/axios";
import PropTypes from "prop-types";
import { each, debounce, get, find } from "lodash";
import Button from "antd/lib/button";
import List from "antd/lib/list";
import Modal from "antd/lib/modal";
import Select from "antd/lib/select";
import Tag from "antd/lib/tag";
import Tooltip from "@/components/Tooltip";
import { wrap as wrapDialog, DialogPropType } from "@/components/DialogWrapper";
import { toHuman } from "@/lib/utils";
import HelpTrigger from "@/components/HelpTrigger";
import { UserPreviewCard } from "@/components/PreviewCard";
import PlainButton from "@/components/PlainButton";
import notification from "@/services/notification";
import User from "@/services/user";

import "./index.less";

const { Option } = Select;
const DEBOUNCE_SEARCH_DURATION = 200;

const searchUsers = searchTerm =>
  User.query({ q: searchTerm })
    .then(({ results }) => results)
    .catch(() => []);

function OwnerEditorDialogHeader({ context }) {
  return (
    <>
      Update Query Owner
      <div className="modal-header-desc">
        {`Updating the ${context} owner is enabled for the author of the query and for admins. `}
      </div>
    </>
  );
}

OwnerEditorDialogHeader.propTypes = { context: PropTypes.oneOf(["query"]) };
OwnerEditorDialogHeader.defaultProps = { context: "query" };

function UserSelect({ onSelect, shouldShowUser }) {
  const [loadingUsers, setLoadingUsers] = useState(true);
  const [users, setUsers] = useState([]);
  const [searchTerm, setSearchTerm] = useState("");

  const debouncedSearchUsers = useCallback(
    debounce(
      search =>
        searchUsers(search)
          .then(setUsers)
          .finally(() => setLoadingUsers(false)),
      DEBOUNCE_SEARCH_DURATION
    ),
    []
  );

  useEffect(() => {
    setLoadingUsers(true);
    debouncedSearchUsers(searchTerm);
  }, [debouncedSearchUsers, searchTerm]);

  return (
    <Select
      className="w-100 m-b-10"
      placeholder="Select new owner..."
      showSearch
      onSearch={setSearchTerm}
      suffixIcon={
        loadingUsers ? (
          <span role="status" aria-live="polite" aria-relevant="additions removals">
            <i className="fa fa-spinner fa-pulse" aria-hidden="true" />
            <span className="sr-only">Loading...</span>
          </span>
        ) : (
          <i className="fa fa-search" aria-hidden="true" />
        )
      }
      filterOption={false}
      notFoundContent={null}
      value={undefined}
      getPopupContainer={trigger => trigger.parentNode}
      onSelect={onSelect}>
      {users.filter(shouldShowUser).map(user => (
        <Option key={user.id} value={user.id}>
          <UserPreviewCard user={user} />
        </Option>
      ))}
    </Select>
  );
}

UserSelect.propTypes = {
  onSelect: PropTypes.func,
  shouldShowUser: PropTypes.func,
};
UserSelect.defaultProps = { onSelect: () => {}, shouldShowUser: () => true };

function OwnerEditorDialog({ dialog, author, context }) {

  const [owner, setOwner] = useState(author);

  const loadOwner = useCallback((userId) => {
    User.get({ id: userId }).then(user => setOwner(user));
  }, []);

  const userIsOwner = useCallback(
    user => user.id === author.id,
    [author.id]
  );

  // useEffect(() => {
  //   loadOwner(author);
  // }, [author, loadOwner]);

  return (
    <Modal
      {...dialog.props}
      className="query-owner-editor-dialog"
      title={<OwnerEditorDialogHeader context={context} />}
      onOk={() => dialog.close(owner)}>
      <UserSelect
        onSelect={userId => loadOwner(userId)}
        shouldShowUser={user => !userIsOwner(user)}
      />
      <div className="d-flex align-items-center m-t-5">
        <h5 className="flex-fill">Query Owner</h5>
      </div>
      <UserPreviewCard key={owner.id} user={owner}>
        {owner.id === author.id ? (
          <Tag className="m-0">Current Owner</Tag>
        ) : ( <Tag className="m-0">New Owner</Tag> )}
      </UserPreviewCard>
    </Modal>
  );
}

OwnerEditorDialog.propTypes = {
  dialog: DialogPropType.isRequired,
  author: PropTypes.object.isRequired, // eslint-disable-line react/forbid-prop-types
  context: PropTypes.oneOf(["query"]),
};

OwnerEditorDialog.defaultProps = { context: "query" };

export default wrapDialog(OwnerEditorDialog);