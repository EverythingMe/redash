import debug from "debug";
import moment from "moment";
import { axios } from "@/services/axios";
import { QueryResultError } from "@/services/query";
import { Auth } from "@/services/auth";
import { isString, uniqBy, each, isNumber, includes, extend, forOwn, get } from "lodash";
import JSONbig from "json-bigint";

const { parse: jsonParse } = JSONbig({ storeAsString: true });
const logger = debug("redash:services:QueryResult");
const filterTypes = ["filter", "multi-filter", "multiFilter"];

function defer() {
  const result = { onStatusChange: status => {} };
  result.promise = new Promise((resolve, reject) => {
    result.resolve = resolve;
    result.reject = reject;
  });
  return result;
}

function getColumnNameWithoutType(column) {
  let typeSplit;
  if (column.indexOf("::") !== -1) {
    typeSplit = "::";
  } else if (column.indexOf("__") !== -1) {
    typeSplit = "__";
  } else {
    return column;
  }

  const parts = column.split(typeSplit);
  if (parts[0] === "" && parts.length === 2) {
    return parts[1];
  }

  if (!includes(filterTypes, parts[1])) {
    return column;
  }

  return parts[0];
}

function getColumnFriendlyName(column) {
  return getColumnNameWithoutType(column).replace(/(?:^|\s)\S/g, a => a.toUpperCase());
}

const createOrSaveUrl = data => (data.id ? `api/query_results/${data.id}` : "api/query_results");
const QueryResultResource = {
  get: ({ id }) =>
    axios.get(`api/query_results/${id}`, {
      transformResponse: response => jsonParse(response),
    }),
  post: data => axios.post(createOrSaveUrl(data), data),
};

export const ExecutionStatus = {
  WAITING: "waiting",
  PROCESSING: "processing",
  DONE: "done",
  FAILED: "failed",
  LOADING_RESULT: "loading-result",
};

const statuses = {
  1: ExecutionStatus.WAITING,
  2: ExecutionStatus.PROCESSING,
  3: ExecutionStatus.DONE,
  4: ExecutionStatus.FAILED,
};

function handleErrorResponse(queryResult, error) {
  const status = get(error, "response.status");
  switch (status) {
    case 403:
      queryResult.update(error.response.data);
      return;
    case 400:
      if ("job" in error.response.data) {
        queryResult.update(error.response.data);
        return;
      }
      break;
    case 404:
      queryResult.update({
        job: {
          error: "cached query result unavailable, please execute again.",
          status: 4,
        },
      });
      return;
    // no default
  }

  logger("Unknown error", error);
  queryResult.update({
    job: {
      error: get(error, "response.data.message", "Unknown error occurred. Please try again later."),
      status: 4,
    },
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export function fetchDataFromJob(jobId, interval = 1000) {
  return axios.get(`api/jobs/${jobId}`).then(data => {
    const status = statuses[data.job.status];
    if (status === ExecutionStatus.WAITING || status === ExecutionStatus.PROCESSING) {
      return sleep(interval).then(() => fetchDataFromJob(data.job.id));
    } else if (status === ExecutionStatus.DONE) {
      return data.job.result;
    } else if (status === ExecutionStatus.FAILED) {
      return Promise.reject(data.job.error);
    }
  });
}

export function isDateTime(v) {
  return isString(v) && moment(v, moment.ISO_8601, true).isValid() && /^\d{4}-\d{2}-\d{2}T/.test(v);
}

class QueryResult {
  constructor(props) {
    this.deferred = defer();
    this.job = {};
    this.query_result = {};
    this.status = "waiting";

    this.updatedAt = moment();

    // extended status flags
    this.isLoadingResult = false;

    if (props) {
      this.update(props);
    }
  }

  update(props) {
    extend(this, props);

    if ("query_result" in props) {
      this.status = ExecutionStatus.DONE;
      this.deferred.onStatusChange(ExecutionStatus.DONE);

      const columnTypes = {};

      // TODO: we should stop manipulating incoming data, and switch to relaying
      // on the column type set by the backend. This logic is prone to errors,
      // and better be removed. Kept for now, for backward compatability.
      each(this.query_result.data.rows, row => {
        forOwn(row, (v, k) => {
          let newType = null;
          if (isNumber(v)) {
            newType = "float";
          } else if (isDateTime(v)) {
            row[k] = moment.utc(v);
            newType = "datetime";
          } else if (isString(v) && v.match(/^\d{4}-\d{2}-\d{2}$/)) {
            row[k] = moment.utc(v);
            newType = "date";
          } else if (typeof v === "object" && v !== null) {
            row[k] = JSON.stringify(v);
          } else {
            newType = "string";
          }

          if (newType !== null) {
            if (columnTypes[k] !== undefined && columnTypes[k] !== newType) {
              columnTypes[k] = "string";
            } else {
              columnTypes[k] = newType;
            }
          }
        });
      });

      each(this.query_result.data.columns, column => {
        column.name = "" + column.name;
        if (columnTypes[column.name]) {
          if (column.type == null || column.type === "string") {
            column.type = columnTypes[column.name];
          }
        }
      });

      this.deferred.resolve(this);
    } else if (this.job.status === 3 || this.job.status === 2) {
      this.deferred.onStatusChange(ExecutionStatus.PROCESSING);
      this.status = "processing";
    } else if (this.job.status === 4) {
      this.status = statuses[this.job.status];
      this.deferred.reject(new QueryResultError(this.job.error));
    } else {
      this.deferred.onStatusChange(undefined);
      this.status = undefined;
    }
  }

  getId() {
    let id = null;
    if ("query_result" in this) {
      id = this.query_result.id;
    }
    return id;
  }

  cancelExecution() {
    axios.delete(`api/jobs/${this.job.id}`);
  }

  getStatus() {
    if (this.isLoadingResult) {
      return ExecutionStatus.LOADING_RESULT;
    }
    return this.status || statuses[this.job.status];
  }

  getError() {
    // TODO: move this logic to the server...
    if (this.job.error === "None") {
      return undefined;
    }

    return this.job.error;
  }

  getLog() {
    if (!this.query_result.data || !this.query_result.data.log || this.query_result.data.log.length === 0) {
      return null;
    }

    return this.query_result.data.log;
  }

  getUpdatedAt() {
    return this.query_result.retrieved_at || this.job.updated_at * 1000.0 || this.updatedAt;
  }

  getRuntime() {
    return this.query_result.runtime;
  }

  getRawData() {
    if (!this.query_result.data) {
      return null;
    }

    return this.query_result.data.rows;
  }

  getData() {
    return this.query_result.data ? this.query_result.data.rows : null;
  }

  isEmpty() {
    return this.getData() === null || this.getData().length === 0;
  }

  getColumns() {
    if (this.columns === undefined && this.query_result.data) {
      this.columns = this.query_result.data.columns;
    }

    return this.columns;
  }

  getColumnNames() {
    if (this.columnNames === undefined && this.query_result.data) {
      this.columnNames = this.query_result.data.columns.map(v => v.name);
    }

    return this.columnNames;
  }

  getColumnFriendlyNames() {
    return this.getColumnNames().map(col => getColumnFriendlyName(col));
  }

  getTruncated() {
    return this.query_result.data ? this.query_result.data.truncated : null;
  }

  getFilters() {
    if (!this.getColumns()) {
      return [];
    }

    const filters = [];

    this.getColumns().forEach(col => {
      const name = col.name;
      const type = name.split("::")[1] || name.split("__")[1];
      if (includes(filterTypes, type)) {
        // filter found
        const filter = {
          name,
          friendlyName: getColumnFriendlyName(name),
          column: col,
          values: [],
          multiple: type === "multiFilter" || type === "multi-filter",
        };
        filters.push(filter);
      }
    }, this);

    this.getRawData().forEach(row => {
      filters.forEach(filter => {
        filter.values.push(row[filter.name]);
        if (filter.values.length === 1) {
          if (filter.multiple) {
            filter.current = [row[filter.name]];
          } else {
            filter.current = row[filter.name];
          }
        }
      });
    });

    filters.forEach(filter => {
      filter.values = uniqBy(filter.values, v => {
        if (moment.isMoment(v)) {
          return v.unix();
        }
        return v;
      });
      if (filter.values.length > 1 && filter.multiple) {
        filter.current = filter.values.slice();
      }
    });

    return filters;
  }

  toPromise(statusCallback) {
    if (statusCallback) {
      this.deferred.onStatusChange = statusCallback;
    }
    return this.deferred.promise;
  }

  static getById(queryId, id) {
    const queryResult = new QueryResult();

    queryResult.isLoadingResult = true;
    queryResult.deferred.onStatusChange(ExecutionStatus.LOADING_RESULT);

    axios
      .get(`api/queries/${queryId}/results/${id}.json`, {
        transformResponse: response => jsonParse(response),
      })
      .then(response => {
        // Success handler
        queryResult.isLoadingResult = false;
        queryResult.update(response);
      })
      .catch(error => {
        // Error handler
        queryResult.isLoadingResult = false;
        handleErrorResponse(queryResult, error);
      });

    return queryResult;
  }

  loadLatestCachedResult(queryId, parameters) {
    axios
      .post(`api/queries/${queryId}/results`, { queryId, parameters })
      .then(response => {
        this.update(response);
      })
      .catch(error => {
        handleErrorResponse(this, error);
      });
  }

  loadResult(tryCount) {
    this.isLoadingResult = true;
    this.deferred.onStatusChange(ExecutionStatus.LOADING_RESULT);

    QueryResultResource.get({ id: this.job.query_result_id })
      .then(response => {
        this.update(response);
        this.isLoadingResult = false;
      })
      .catch(error => {
        if (tryCount === undefined) {
          tryCount = 0;
        }

        if (tryCount > 3) {
          logger("Connection error while trying to load result", error);
          this.update({
            job: {
              error: "failed communicating with server. Please check your Internet connection and try again.",
              status: 4,
            },
          });
          this.isLoadingResult = false;
        } else {
          setTimeout(() => {
            this.loadResult(tryCount + 1);
          }, 1000 * Math.pow(2, tryCount));
        }
      });
  }

  refreshStatus(query, parameters, tryNumber = 1) {
    const loadResult = () =>
      Auth.isAuthenticated() ? this.loadResult() : this.loadLatestCachedResult(query, parameters);

    const request = Auth.isAuthenticated()
      ? axios.get(`api/jobs/${this.job.id}`)
      : axios.get(`api/queries/${query}/jobs/${this.job.id}`);

    request
      .then(jobResponse => {
        this.update(jobResponse);

        if (this.getStatus() === "processing" && this.job.query_result_id && this.job.query_result_id !== "None") {
          loadResult();
        } else if (this.getStatus() !== "failed") {
          const waitTime = tryNumber > 10 ? 3000 : 500;
          setTimeout(() => {
            this.refreshStatus(query, parameters, tryNumber + 1);
          }, waitTime);
        }
      })
      .catch(error => {
        logger("Connection error", error);
        // TODO: use QueryResultError, or better yet: exception/reject of promise.
        this.update({
          job: {
            error: "failed communicating with server. Please check your Internet connection and try again.",
            status: 4,
          },
        });
      });
  }

  getLink(queryId, fileType, apiKey) {
    let link = `api/queries/${queryId}/results/${this.getId()}.${fileType}`;
    if (apiKey) {
      link = `${link}?api_key=${apiKey}`;
    }
    return link;
  }

  getName(queryName, fileType) {
    return `${queryName.replace(/ /g, "_") + moment(this.getUpdatedAt()).format("_YYYY_MM_DD")}.${fileType}`;
  }

  static getByQueryId(id, parameters, applyAutoLimit, maxAge) {
    const queryResult = new QueryResult();

    axios
      .post(`api/queries/${id}/results`, { id, parameters, apply_auto_limit: applyAutoLimit, max_age: maxAge })
      .then(response => {
        queryResult.update(response);

        if ("job" in response) {
          queryResult.refreshStatus(id, parameters);
        }
      })
      .catch(error => {
        handleErrorResponse(queryResult, error);
      });

    return queryResult;
  }

  static get(dataSourceId, query, parameters, applyAutoLimit, maxAge, queryId) {
    const queryResult = new QueryResult();

    const params = {
      data_source_id: dataSourceId,
      parameters,
      query,
      apply_auto_limit: applyAutoLimit,
      max_age: maxAge,
    };

    if (queryId !== undefined) {
      params.query_id = queryId;
    }

    QueryResultResource.post(params)
      .then(response => {
        queryResult.update(response);

        if ("job" in response) {
          queryResult.refreshStatus(query, parameters);
        }
      })
      .catch(error => {
        handleErrorResponse(queryResult, error);
      });

    return queryResult;
  }
}

export default QueryResult;
