import React, { useState, useReducer, useCallback } from "react";
import PropTypes from "prop-types";
import cx from "classnames";
import Form from "antd/lib/form";
import Checkbox from "antd/lib/checkbox";
import Button from "antd/lib/button";
import { includes, isFunction, filter, find, difference, isEmpty, mapValues } from "lodash";
import notification from "@/services/notification";
import Collapse from "@/components/Collapse";
import DynamicFormField, { FieldType } from "./DynamicFormField";
import getFieldLabel from "./getFieldLabel";
import helper from "./dynamicFormHelper";

import "./DynamicForm.less";

const ActionType = PropTypes.shape({
  name: PropTypes.string.isRequired,
  callback: PropTypes.func.isRequired,
  type: PropTypes.string,
  pullRight: PropTypes.bool,
  disabledWhenDirty: PropTypes.bool,
});

const AntdFormType = PropTypes.shape({
  validateFieldsAndScroll: PropTypes.func,
});

const fieldRules = ({ type, required, minLength }) => {
  const requiredRule = required;
  const minLengthRule = minLength && includes(["text", "email", "password"], type);
  const emailTypeRule = type === "email";

  return [
    requiredRule && { required, message: "This field is required." },
    minLengthRule && { min: minLength, message: "This field is too short." },
    emailTypeRule && { type: "email", message: "This field must be a valid email." },
  ].filter(rule => rule);
};

function normalizeEmptyValuesToNull(fields, values) {
  return mapValues(values, (value, key) => {
    const { initialValue } = find(fields, { name: key }) || {};
    if ((initialValue === null || initialValue === undefined || initialValue === "") && value === "") {
      return null;
    }
    return value;
  });
}

function DynamicFormFields({ fields, feedbackIcons, form, useCustomHostPort, isOracle }) {
  return fields.map(field => {
    if (isOracle && useCustomHostPort && (field.name === "host" || field.name === "port")) {
      return null;
    }
    const { name, type, initialValue, contentAfter } = field;
    const fieldLabel = getFieldLabel(field);

    const formItemProps = {
      name,
      className: "m-b-10",
      hasFeedback: type !== "checkbox" && type !== "file" && feedbackIcons,
      label: type === "checkbox" ? "" : fieldLabel,
      rules: fieldRules(field),
      valuePropName: type === "checkbox" ? "checked" : "value",
      initialValue,
    };

    if (type === "file") {
      formItemProps.valuePropName = "data-value";
      formItemProps.getValueFromEvent = e => {
        if (e && e.fileList[0]) {
          helper.getBase64(e.file).then(value => {
            form.setFieldsValue({ [name]: value });
          });
        }
        return undefined;
      };
    }

    return (
      <React.Fragment key={name}>
        <Form.Item {...formItemProps}>
          <DynamicFormField field={field} form={form} />
        </Form.Item>
        {isFunction(contentAfter) ? contentAfter(form.getFieldValue(name)) : contentAfter}
      </React.Fragment>
    );
  });
}

DynamicFormFields.propTypes = {
  fields: PropTypes.arrayOf(FieldType),
  feedbackIcons: PropTypes.bool,
  form: AntdFormType.isRequired,
};

DynamicFormFields.defaultProps = {
  fields: [],
  feedbackIcons: false,
};

const reducerForActionSet = (state, action) => {
  if (action.inProgress) {
    state.add(action.actionName);
  } else {
    state.delete(action.actionName);
  }
  return new Set(state);
};

function DynamicFormActions({ actions, isFormDirty }) {
  const [inProgressActions, setActionInProgress] = useReducer(reducerForActionSet, new Set());

  const handleAction = useCallback(action => {
    const actionName = action.name;
    if (isFunction(action.callback)) {
      setActionInProgress({ actionName, inProgress: true });
      action.callback(() => {
        setActionInProgress({ actionName, inProgress: false });
      });
    }
  }, []);
  return actions.map(action => (
    <Button
      key={action.name}
      htmlType="button"
      className={cx("m-t-10", { "pull-right": action.pullRight })}
      type={action.type}
      disabled={isFormDirty && action.disableWhenDirty}
      loading={inProgressActions.has(action.name)}
      onClick={() => handleAction(action)}>
      {action.name}
    </Button>
  ));
}

DynamicFormActions.propTypes = {
  actions: PropTypes.arrayOf(ActionType),
  isFormDirty: PropTypes.bool,
};

DynamicFormActions.defaultProps = {
  actions: [],
  isFormDirty: false,
};

export default function DynamicForm({
  id,
  fields,
  actions,
  feedbackIcons,
  hideSubmitButton,
  defaultShowExtraFields,
  saveText,
  onSubmit,
  selectedType,
}) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isTouched, setIsTouched] = useState(false);
  const [useCustomHostPort, setUseCustomHostPort] = useState(false);
  const [showExtraFields, setShowExtraFields] = useState(defaultShowExtraFields);
  const [form] = Form.useForm();
  const isOracle = selectedType === "oracle";
  const extraFields = filter(fields, { extra: true });
  const regularFields = difference(fields, extraFields);

  const handleFinish = useCallback(
    values => {
      setIsSubmitting(true);
      values = normalizeEmptyValuesToNull(fields, values);
      onSubmit(
        values,
        msg => {
          setIsSubmitting(false);
          setIsTouched(false); // reset form touched state
          notification.success(msg);
        },
        msg => {
          setIsSubmitting(false);
          notification.error(msg);
        }
      );
    },
    [fields, onSubmit]
  );

  const handleFinishFailed = useCallback(
    ({ errorFields }) => {
      form.scrollToField(errorFields[0].name);
    },
    [form]
  );

  const handleCheckboxChange = useCallback(e => {
    setUseCustomHostPort(e.target.checked);
  }, []);

  return (
    <Form
      form={form}
      onFieldsChange={() => {
        setIsTouched(true);
      }}
      id={id}
      className="dynamic-form"
      layout="vertical"
      onFinish={handleFinish}
      onFinishFailed={handleFinishFailed}>
      {isOracle && (
        <Form.Item name="useCustomHostPort" valuePropName="checked">
          <Checkbox checked={useCustomHostPort} onChange={handleCheckboxChange}>
            Use Custom
          </Checkbox>
        </Form.Item>
      )}
      <DynamicFormFields
        fields={regularFields}
        feedbackIcons={feedbackIcons}
        form={form}
        useCustomHostPort={useCustomHostPort}
        isOracle={isOracle}
      />
      {!isEmpty(extraFields) && (
        <div className="extra-options">
          <Button
            type="dashed"
            block
            className="extra-options-button"
            onClick={() => setShowExtraFields(currentShowExtraFields => !currentShowExtraFields)}>
            Additional Settings
            <i
              className={cx("fa m-l-5", { "fa-caret-up": showExtraFields, "fa-caret-down": !showExtraFields })}
              aria-hidden="true"
            />
          </Button>
          <Collapse collapsed={!showExtraFields} className="extra-options-content">
            <DynamicFormFields fields={extraFields} feedbackIcons={feedbackIcons} form={form} />
          </Collapse>
        </div>
      )}
      {!hideSubmitButton && (
        <Button className="w-100 m-t-20" type="primary" htmlType="submit" disabled={isSubmitting}>
          {saveText}
        </Button>
      )}
      <DynamicFormActions actions={actions} isFormDirty={isTouched} />
    </Form>
  );
}

DynamicForm.propTypes = {
  id: PropTypes.string,
  fields: PropTypes.arrayOf(FieldType),
  actions: PropTypes.arrayOf(ActionType),
  feedbackIcons: PropTypes.bool,
  hideSubmitButton: PropTypes.bool,
  defaultShowExtraFields: PropTypes.bool,
  saveText: PropTypes.string,
  onSubmit: PropTypes.func,
};

DynamicForm.defaultProps = {
  id: null,
  fields: [],
  actions: [],
  feedbackIcons: false,
  hideSubmitButton: false,
  defaultShowExtraFields: false,
  saveText: "Save",
  onSubmit: () => {},
};
