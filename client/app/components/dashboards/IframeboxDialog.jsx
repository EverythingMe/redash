import { toString } from "lodash";
import React, { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import { useDebouncedCallback } from "use-debounce";
import Modal from "antd/lib/modal";
import Input from "antd/lib/input";
import Divider from "antd/lib/divider";
import { wrap as wrapDialog, DialogPropType } from "@/components/DialogWrapper";
import notification from "@/services/notification";

import "./IframeboxDialog.less";

export function toIframe(text) {
  return `<iframe style='height:100%;width:100%;' src=${text}></iframe>`;
}

function IframeboxDialog({ dialog, isNew, ...props }) {
  const [text, setText] = useState(toString(props.text));
  const [title, setTitle] = useState(toString(props.title));
  const [preview, setPreview] = useState(null);

  useEffect(() => {
    setText(props.text);
    setPreview(toIframe(props.text));
  }, [props.text]);

  const [updatePreview] = useDebouncedCallback(() => {
    setPreview(toIframe(text));
  }, 200);

  const handleInputChange = useCallback(
    event => {
      setText(event.target.value);
      updatePreview();
    },
    [updatePreview]
  );

  const saveWidget = useCallback(() => {
    dialog.close(text, title).catch(() => {
      notification.error(isNew ? "Widget could not be added" : "Widget could not be saved");
    });
  }, [dialog, isNew, text, title]);

  return (
    <Modal
      {...dialog.props}
      title={isNew ? "Add Iframebox" : "Edit Iframebox"}
      onOk={saveWidget}
      okText={isNew ? "Add to Dashboard" : "Save"}
      width={500}
      wrapProps={{ "data-test": "IframeboxDialog" }}>
      <div className="iframebox-dialog">
        <Input value={title} onChange={event => setTitle(event.target.value)} placeholder="Title..." />
        <Divider dashed />
        <Input.TextArea
          className="resize-vertical"
          rows="5"
          value={text}
          onChange={handleInputChange}
          autoFocus
          placeholder="Site to be linked..."
        />
        {text && (
          <React.Fragment>
            <Divider dashed />
            <strong className="preview-title">Preview:</strong>
            <div
              className="preview"
              dangerouslySetInnerHTML={{ __html: preview }} // eslint-disable-line react/no-danger
            />
          </React.Fragment>
        )}
      </div>
    </Modal>
  );
}

IframeboxDialog.propTypes = {
  dialog: DialogPropType.isRequired,
  isNew: PropTypes.bool,
  text: PropTypes.string,
  title: PropTypes.string,
};

IframeboxDialog.defaultProps = {
  isNew: false,
  text: "",
  title: "",
};

export default wrapDialog(IframeboxDialog);
