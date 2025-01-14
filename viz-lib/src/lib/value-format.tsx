import React from "react";
import ReactDOMServer from "react-dom/server";
import moment from "moment/moment";
// numeral is waiting on v3.0.0 to be available on npm
// https://github.com/adamwdraper/Numeral-js/pull/790
// @ts-ignore
import numeral from "./numeral";
import { isString, isArray, isUndefined, isFinite, isNil, toString } from "lodash";
import { visualizationsSettings } from "@/visualizations/visualizationsSettings";

numeral.options.scalePercentBy100 = false;

// eslint-disable-next-line
const urlPattern = /(^|[\s\n]|<br\/?>)((?:https?|ftp):\/\/[\-A-Z0-9+\u0026\u2019@#\/%?=()~_|!:,.;]*[\-A-Z0-9+\u0026@#\/%=~()_|])/gi;

const hasOwnProperty = Object.prototype.hasOwnProperty;

export function createTextFormatter(highlightLinks: any) {
  if (highlightLinks) {
    return (value: any) => {
      if (isString(value)) {
        const Link = visualizationsSettings.LinkComponent;
        value = value.replace(urlPattern, (unused, prefix, href) => {
          const link = ReactDOMServer.renderToStaticMarkup(
            <Link href={href} target="_blank" rel="noopener noreferrer">
              {href}
            </Link>
          );
          return prefix + link;
        });
      }
      return toString(value);
    };
  }
  return (value: any) => toString(value);
}

function toMoment(value: any) {
  if (moment.isMoment(value)) {
    return value;
  }
  if (isFinite(value)) {
    return moment(value);
  }
  // same as default `moment(value)`, but avoid fallback to `new Date()`
  return moment(toString(value), [moment.ISO_8601, moment.RFC_2822]);
}

export function createDateTimeFormatter(format: any) {
  if (isString(format) && format !== "") {
    return (value: any) => {
      const wrapped = toMoment(value);
      return wrapped.isValid() ? wrapped.format(format) : toString(value);
    };
  }
  return (value: any) => toString(value);
}

export function createBooleanFormatter(values: any) {
  if (isArray(values)) {
    if (values.length >= 2) {
      // Both `true` and `false` specified
      return (value: any) => {
        if (isNil(value)) {
          return "";
        }
        return "" + values[value ? 1 : 0];
      };
    } else if (values.length === 1) {
      // Only `true`
      return (value: any) => (value ? values[0] : "");
    }
  }
  return (value: any) => {
    if (isNil(value)) {
      return "";
    }
    return value ? "true" : "false";
  };
}

export function createNumberFormatter(format: any) {
  if (isString(format) && format !== "") {
    const n = numeral(0); // cache `numeral` instance
    return (value: any) => (value === null || value === "" ? "" : n.set(value).format(format));
  }
  return (value: any) => toString(value);
}

export function formatSimpleTemplate(str: any, data: any) {
  if (!isString(str)) {
    return "";
  }
  return str.replace(/{{\s*([^\s]+?)\s*}}/g, (match, prop) => {
    if (hasOwnProperty.call(data, prop) && !isUndefined(data[prop])) {
      return data[prop];
    }
    return match;
  });
}
