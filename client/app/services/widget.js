import moment from "moment";
import { axios } from "@/services/axios";
import {
  each,
  pick,
  extend,
  isObject,
  truncate,
  keys,
  difference,
  filter,
  map,
  merge,
  sortBy,
  indexOf,
  size,
  includes,
} from "lodash";
import location from "@/services/location";
import { cloneParameter } from "@/services/parameters";
import dashboardGridOptions from "@/config/dashboard-grid-options";
import { registeredVisualizations } from "@redash/viz/lib";
import { Query } from "./query";

export const WidgetTypeEnum = {
  TEXTBOX: "textbox",
  IFRAMEBOX: "iframebox",
  VISUALIZATION: "visualization",
  RESTRICTED: "restricted",
};

function calculatePositionOptions(widget) {
  widget.width = 1; // Backward compatibility, user on back-end

  const visualizationOptions = {
    autoHeight: false,
    sizeX: Math.round(dashboardGridOptions.columns / 2),
    sizeY: dashboardGridOptions.defaultSizeY,
    minSizeX: dashboardGridOptions.minSizeX,
    maxSizeX: dashboardGridOptions.maxSizeX,
    minSizeY: dashboardGridOptions.minSizeY,
    maxSizeY: dashboardGridOptions.maxSizeY,
  };

  const config = widget.visualization ? registeredVisualizations[widget.visualization.type] : null;
  if (isObject(config)) {
    if (Object.prototype.hasOwnProperty.call(config, "autoHeight")) {
      visualizationOptions.autoHeight = config.autoHeight;
    }

    // Width constraints
    const minColumns = parseInt(config.minColumns, 10);
    if (isFinite(minColumns) && minColumns >= 0) {
      visualizationOptions.minSizeX = minColumns;
    }
    const maxColumns = parseInt(config.maxColumns, 10);
    if (isFinite(maxColumns) && maxColumns >= 0) {
      visualizationOptions.maxSizeX = Math.min(maxColumns, dashboardGridOptions.columns);
    }

    // Height constraints
    // `minRows` is preferred, but it should be kept for backward compatibility
    const height = parseInt(config.height, 10);
    if (isFinite(height)) {
      visualizationOptions.minSizeY = Math.ceil(height / dashboardGridOptions.rowHeight);
    }
    const minRows = parseInt(config.minRows, 10);
    if (isFinite(minRows)) {
      visualizationOptions.minSizeY = minRows;
    }
    const maxRows = parseInt(config.maxRows, 10);
    if (isFinite(maxRows) && maxRows >= 0) {
      visualizationOptions.maxSizeY = maxRows;
    }

    // Default dimensions
    const defaultWidth = parseInt(config.defaultColumns, 10);
    if (isFinite(defaultWidth) && defaultWidth > 0) {
      visualizationOptions.sizeX = defaultWidth;
    }
    const defaultHeight = parseInt(config.defaultRows, 10);
    if (isFinite(defaultHeight) && defaultHeight > 0) {
      visualizationOptions.sizeY = defaultHeight;
    }
  }

  return visualizationOptions;
}

export const ParameterMappingType = {
  DashboardLevel: "dashboard-level",
  WidgetLevel: "widget-level",
  StaticValue: "static-value",
};

class Widget {
  static MappingType = ParameterMappingType;

  constructor(data) {
    // Copy properties
    extend(this, data);

    const visualizationOptions = calculatePositionOptions(this);

    this.options = this.options || {};
    this.options.position = extend(
      {},
      visualizationOptions,
      pick(this.options.position, ["col", "row", "sizeX", "sizeY", "autoHeight"])
    );

    if (this.options.position.sizeY < 0) {
      this.options.position.autoHeight = true;
    }
  }

  get type() {
    if (this.visualization) {
      return WidgetTypeEnum.VISUALIZATION;
    } else if (this.restricted) {
      return WidgetTypeEnum.RESTRICTED;
    } else if (this.options.isIframe) {
      return WidgetTypeEnum.IFRAMEBOX;
    }
    return WidgetTypeEnum.TEXTBOX;
  }

  getQuery() {
    if (!this.query && this.visualization) {
      this.query = new Query(this.visualization.query);
    }

    return this.query;
  }

  getQueryResult() {
    return this.data;
  }

  getName() {
    if (this.visualization) {
      return `${this.visualization.query.name} (${this.visualization.name})`;
    }
    return truncate(this.text, 20);
  }

  load(force, maxAge) {
    if (!this.visualization) {
      return Promise.resolve();
    }

    // Both `this.data` and `this.queryResult` are query result objects;
    // `this.data` is last loaded query result;
    // `this.queryResult` is currently loading query result;
    // while widget is refreshing, `this.data` !== `this.queryResult`

    if (force || this.queryResult === undefined) {
      this.loading = true;
      this.refreshStartedAt = moment();

      if (maxAge === undefined || force) {
        maxAge = force ? 0 : undefined;
      }

      const queryResult = this.getQuery().getQueryResult(maxAge);
      this.queryResult = queryResult;

      queryResult
        .toPromise()
        .then(result => {
          if (this.queryResult === queryResult) {
            this.loading = false;
            this.data = result;
          }
          return result;
        })
        .catch(error => {
          if (this.queryResult === queryResult) {
            this.loading = false;
            this.data = error;
          }
          return error;
        });
    }

    return this.queryResult.toPromise();
  }

  save(key, value) {
    const data = pick(this, "options", "text", "id", "width", "dashboard_id", "visualization_id");
    if (key && value) {
      data[key] = merge({}, data[key], value); // done like this so `this.options` doesn't get updated by side-effect
    }

    let url = "api/widgets";
    if (this.id) {
      url = `${url}/${this.id}`;
    }

    return axios.post(url, data).then(data => {
      each(data, (v, k) => {
        this[k] = v;
      });

      return this;
    });
  }

  delete() {
    const url = `api/widgets/${this.id}`;
    return axios.delete(url);
  }

  isStaticParam(param) {
    const mappings = this.getParameterMappings();
    const mappingType = mappings[param.name].type;
    return mappingType === Widget.MappingType.StaticValue;
  }

  getParametersDefs() {
    const mappings = this.getParameterMappings();
    // textboxes does not have query
    const params = this.getQuery() ? this.getQuery().getParametersDefs() : [];

    const queryParams = location.search;

    const localTypes = [Widget.MappingType.WidgetLevel, Widget.MappingType.StaticValue];
    const localParameters = map(
      filter(params, param => localTypes.indexOf(mappings[param.name].type) >= 0),
      param => {
        const mapping = mappings[param.name];
        const result = cloneParameter(param);
        result.title = mapping.title || param.title;
        result.locals = [param];
        result.urlPrefix = `p_w${this.id}_`;
        if (mapping.type === Widget.MappingType.StaticValue) {
          result.setValue(mapping.value);
        } else {
          result.fromUrlParams(queryParams);
        }
        return result;
      }
    );

    // order widget params using paramOrder
    return sortBy(localParameters, param =>
      includes(this.options.paramOrder, param.name)
        ? indexOf(this.options.paramOrder, param.name)
        : size(this.options.paramOrder)
    );
  }

  getParameterMappings() {
    if (!isObject(this.options.parameterMappings)) {
      this.options.parameterMappings = {};
    }

    const existingParams = {};
    // textboxes does not have query
    const params = this.getQuery() ? this.getQuery().getParametersDefs(false) : [];
    each(params, param => {
      existingParams[param.name] = true;
      if (!isObject(this.options.parameterMappings[param.name])) {
        // "migration" for old dashboards: parameters with `global` flag
        // should be mapped to a dashboard-level parameter with the same name
        this.options.parameterMappings[param.name] = {
          name: param.name,
          type: param.global ? Widget.MappingType.DashboardLevel : Widget.MappingType.WidgetLevel,
          mapTo: param.name, // map to param with the same name
          value: null, // for StaticValue
          title: "", // Use parameter's title
        };
      }
    });

    // Remove mappings for parameters that do not exists anymore
    const removedParams = difference(keys(this.options.parameterMappings), keys(existingParams));
    each(removedParams, name => {
      delete this.options.parameterMappings[name];
    });

    return this.options.parameterMappings;
  }

  getLocalParameters() {
    return filter(this.getParametersDefs(), param => !this.isStaticParam(param));
  }
}

export default Widget;
