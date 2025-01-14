import { find, has } from "lodash";
import React, { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import moment from "moment";
import { markdown } from "markdown";

import Button from "antd/lib/button";
import Dropdown from "antd/lib/dropdown";
import Menu from "antd/lib/menu";
import Tooltip from "@/components/Tooltip";
import Link from "@/components/Link";
import routeWithApiKeySession from "@/components/ApplicationArea/routeWithApiKeySession";
import Parameters from "@/components/Parameters";
import { Moment } from "@/components/proptypes";
import TimeAgo from "@/components/TimeAgo";
import Timer from "@/components/Timer";
import QueryResultsLink from "@/components/EditVisualizationButton/QueryResultsLink";
import VisualizationName from "@/components/visualizations/VisualizationName";
import VisualizationRenderer from "@/components/visualizations/VisualizationRenderer";

import FileOutlinedIcon from "@ant-design/icons/FileOutlined";
import FileExcelOutlinedIcon from "@ant-design/icons/FileExcelOutlined";

import { VisualizationType } from "@redash/viz/lib";
import HtmlContent from "@redash/viz/lib/components/HtmlContent";

import { formatDateTime } from "@/lib/utils";
import useImmutableCallback from "@/lib/hooks/useImmutableCallback";
import { Query } from "@/services/query";
import location from "@/services/location";
import routes from "@/services/routes";

import logoUrl from "@/assets/images/redash_icon_small.png";

function VisualizationEmbedHeader({ queryName, queryDescription, visualization }) {
  return (
    <div className="embed-heading p-b-10 p-r-15 p-l-15">
      <h3>
        <img src={logoUrl} alt="Redash Logo" style={{ height: "24px", verticalAlign: "text-bottom" }} />
        <VisualizationName visualization={visualization} /> {queryName}
        {queryDescription && (
          <small>
            <HtmlContent className="markdown text-muted">{markdown.toHTML(queryDescription || "")}</HtmlContent>
          </small>
        )}
      </h3>
    </div>
  );
}

VisualizationEmbedHeader.propTypes = {
  queryName: PropTypes.string.isRequired,
  queryDescription: PropTypes.string,
  visualization: VisualizationType.isRequired,
};

VisualizationEmbedHeader.defaultProps = { queryDescription: "" };

function VisualizationEmbedFooter({
  query,
  queryResults,
  updatedAt,
  refreshStartedAt,
  queryUrl,
  hideTimestamp,
  apiKey,
}) {
  const downloadMenu = (
    <Menu>
      <Menu.Item>
        <QueryResultsLink
          fileType="csv"
          query={query}
          queryResult={queryResults}
          apiKey={apiKey}
          disabled={!queryResults || !queryResults.getData || !queryResults.getData()}
          embed>
          <FileOutlinedIcon /> Download as CSV File
        </QueryResultsLink>
      </Menu.Item>
      <Menu.Item>
        <QueryResultsLink
          fileType="tsv"
          query={query}
          queryResult={queryResults}
          apiKey={apiKey}
          disabled={!queryResults || !queryResults.getData || !queryResults.getData()}
          embed>
          <FileOutlinedIcon /> Download as TSV File
        </QueryResultsLink>
      </Menu.Item>
      <Menu.Item>
        <QueryResultsLink
          fileType="xlsx"
          query={query}
          queryResult={queryResults}
          apiKey={apiKey}
          disabled={!queryResults || !queryResults.getData || !queryResults.getData()}
          embed>
          <FileExcelOutlinedIcon /> Download as Excel File
        </QueryResultsLink>
      </Menu.Item>
      <Menu.Item>
        <QueryResultsLink
          fileType="json"
          query={query}
          queryResult={queryResults}
          apiKey={apiKey}
          disabled={!queryResults || !queryResults.getData || !queryResults.getData()}
          embed>
          <FileOutlinedIcon /> Download as JSON File
        </QueryResultsLink>
      </Menu.Item>
    </Menu>
  );

  return (
    <div className="tile__bottom-control">
      {!hideTimestamp && (
        <span>
          <span className="small hidden-print">
            <i className="zmdi zmdi-time-restore" aria-hidden="true" />{" "}
            {refreshStartedAt ? <Timer from={refreshStartedAt} /> : <TimeAgo date={updatedAt} />}
          </span>
          <span className="small visible-print">
            <i className="zmdi zmdi-time-restore" aria-hidden="true" /> {formatDateTime(updatedAt)}
          </span>
        </span>
      )}
      {queryUrl && (
        <span className="hidden-print">
          <Tooltip title="Open in Redash">
            <Link.Button className="icon-button" href={queryUrl} target="_blank">
              <i className="fa fa-external-link" aria-hidden="true" />
              <span className="sr-only">Open in Redash</span>
            </Link.Button>
          </Tooltip>
          {!query.hasParameters() && (
            <Dropdown overlay={downloadMenu} disabled={!queryResults} trigger={["click"]} placement="topLeft">
              <Button loading={!queryResults && !!refreshStartedAt} className="m-l-5">
                Download Dataset
                <i className="fa fa-caret-up m-l-5" aria-hidden="true" />
              </Button>
            </Dropdown>
          )}
        </span>
      )}
    </div>
  );
}

VisualizationEmbedFooter.propTypes = {
  query: PropTypes.object.isRequired, // eslint-disable-line react/forbid-prop-types
  queryResults: PropTypes.object, // eslint-disable-line react/forbid-prop-types
  updatedAt: PropTypes.string,
  refreshStartedAt: Moment,
  queryUrl: PropTypes.string,
  hideTimestamp: PropTypes.bool,
  apiKey: PropTypes.string,
};

VisualizationEmbedFooter.defaultProps = {
  queryResults: null,
  updatedAt: null,
  refreshStartedAt: null,
  queryUrl: null,
  hideTimestamp: false,
  apiKey: null,
};

function VisualizationEmbed({ queryId, visualizationId, apiKey, onError }) {
  const [query, setQuery] = useState(null);
  const [error, setError] = useState(null);
  const [refreshStartedAt, setRefreshStartedAt] = useState(null);
  const [queryResults, setQueryResults] = useState(null);

  const handleError = useImmutableCallback(onError);

  useEffect(() => {
    let isCancelled = false;
    Query.get({ id: queryId })
      .then(result => {
        if (!isCancelled) {
          setQuery(result);
        }
      })
      .catch(handleError);

    return () => {
      isCancelled = true;
    };
  }, [queryId, handleError]);

  const refreshQueryResults = useCallback(() => {
    if (query) {
      setError(null);
      setRefreshStartedAt(moment());
      query
        .getQueryResultPromise()
        .then(result => {
          setQueryResults(result);
        })
        .catch(err => {
          setError(err.getError());
        })
        .finally(() => setRefreshStartedAt(null));
    }
  }, [query]);

  useEffect(() => {
    document.querySelector("body").classList.add("headless");
    refreshQueryResults();
  }, [refreshQueryResults]);

  if (!query) {
    return null;
  }

  const hideHeader = has(location.search, "hide_header");
  const hideParametersUI = has(location.search, "hide_parameters");
  const hideQueryLink = has(location.search, "hide_link");
  const hideTimestamp = has(location.search, "hide_timestamp");

  const showQueryDescription = has(location.search, "showDescription");
  visualizationId = parseInt(visualizationId, 10);
  const visualization = find(query.visualizations, vis => vis.id === visualizationId);

  if (!visualization) {
    // call error handler async, otherwise it will destroy the component on render phase
    setTimeout(() => {
      onError(new Error("Visualization does not exist"));
    }, 10);
    return null;
  }

  return (
    <div className="tile m-t-10 m-l-10 m-r-10 p-t-10 embed__vis" data-test="VisualizationEmbed">
      {!hideHeader && (
        <VisualizationEmbedHeader
          queryName={query.name}
          queryDescription={showQueryDescription ? query.description : null}
          visualization={visualization}
        />
      )}
      <div className="col-md-12 query__vis">
        {!hideParametersUI && query.hasParameters() && (
          <div className="p-t-15 p-b-10">
            <Parameters parameters={query.getParametersDefs()} onValuesChange={refreshQueryResults} />
          </div>
        )}
        {error && <div className="alert alert-danger" data-test="ErrorMessage">{`Error: ${error}`}</div>}
        {!error && queryResults && (
          <VisualizationRenderer visualization={visualization} queryResult={queryResults} context="widget" />
        )}
        {!queryResults && refreshStartedAt && (
          <div className="d-flex justify-content-center">
            <div className="spinner">
              <i className="zmdi zmdi-refresh zmdi-hc-spin zmdi-hc-5x" aria-hidden="true" />
              <span className="sr-only">Refreshing...</span>
            </div>
          </div>
        )}
      </div>
      <VisualizationEmbedFooter
        query={query}
        queryResults={queryResults}
        updatedAt={queryResults ? queryResults.getUpdatedAt() : undefined}
        refreshStartedAt={refreshStartedAt}
        queryUrl={!hideQueryLink ? query.getUrl() : null}
        hideTimestamp={hideTimestamp}
        apiKey={apiKey}
      />
    </div>
  );
}

VisualizationEmbed.propTypes = {
  queryId: PropTypes.string.isRequired,
  visualizationId: PropTypes.string,
  apiKey: PropTypes.string.isRequired,
  onError: PropTypes.func,
};

VisualizationEmbed.defaultProps = {
  onError: () => {},
};

routes.register(
  "Visualizations.ViewShared",
  routeWithApiKeySession({
    path: "/embed/query/:queryId/visualization/:visualizationId",
    render: pageProps => <VisualizationEmbed {...pageProps} />,
    getApiKey: () => location.search.api_key,
  })
);
