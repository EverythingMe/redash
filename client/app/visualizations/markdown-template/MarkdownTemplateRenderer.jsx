import React from 'react';
import { markdown } from 'markdown';
import Mustache from 'mustache';

export default function MarkdownTemplateRenderer({ data, options }) {
  const { template } = options;

  if (!template) {
    return null;
  }

  if (!data || !data.rows) {
    return null;
  }

  let html = '';
  try {
    html = markdown.toHTML(Mustache.render(template, { rows: data.rows }));
  } catch (exception) {
    // ignore
  }

  const rendered = {
    __html: html,
  };

  // eslint-disable-next-line react/no-danger
  return <div dangerouslySetInnerHTML={rendered} />;
}
