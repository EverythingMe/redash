import React from 'react';
import Form from 'antd/lib/form';
import Input from 'antd/lib/input';

export default function MarkdownTemplateEditor({ options, onOptionsChange }) {
  const onChange = (e) => {
    // onOptionsChange(options);
    options.template = e.target.value;
    onOptionsChange(options);
  };

  const template = options.template;

  return (
    <Form layout="vertical">
      <Form.Item label="Template">
        <Input.TextArea rows={8} defaultValue={template} onChange={onChange} />
      </Form.Item>
    </Form>
  );
}
