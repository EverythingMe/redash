import React, { useMemo, useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import { react2angular } from "react2angular";
import Divider from "antd/lib/divider";
import Button from "antd/lib/button";
import Tooltip from "antd/lib/tooltip";

import { EditInPlace } from "@/components/EditInPlace";
import { Parameters } from "@/components/Parameters";
import { TimeAgo } from "@/components/TimeAgo";
import { QueryControlDropdown } from "@/components/EditVisualizationButton/QueryControlDropdown";
import { EditVisualizationButton } from "@/components/EditVisualizationButton";

import { DataSource } from "@/services/data-source";
import { KeyboardShortcuts } from "@/services/keyboard-shortcuts";
import { pluralize, durationHumanize } from "@/filters";

import QueryPageHeader from "./components/QueryPageHeader";
import QueryVisualizationTabs from "./components/QueryVisualizationTabs";
import QueryExecutionStatus from "./components/QueryExecutionStatus";
import QueryMetadata from "./components/QueryMetadata";

import useVisualizationTabHandler from "./hooks/useVisualizationTabHandler";
import useQueryExecute from "./hooks/useQueryExecute";
import useUpdateQueryDescription from "./hooks/useUpdateQueryDescription";
import useQueryFlags from "./hooks/useQueryFlags";
import useQueryParameters from "./hooks/useQueryParameters";
import useAddToDashboardDialog from "./hooks/useAddToDashboardDialog";
import useEmbedDialog from "./hooks/useEmbedDialog";
import useEditScheduleDialog from "./hooks/useEditScheduleDialog";
import useEditVisualizationDialog from "./hooks/useEditVisualizationDialog";
import useDeleteVisualization from "./hooks/useDeleteVisualization";

function QueryView(props) {
  const [query, setQuery] = useState(props.query);
  const [dataSource, setDataSource] = useState();
  const queryFlags = useQueryFlags(query, dataSource);
  const [parameters, areParametersDirty, updateParametersDirtyFlag] = useQueryParameters(query);
  const [selectedVisualization, setSelectedVisualization] = useVisualizationTabHandler(query.visualizations);

  const {
    queryResult,
    queryResultData,
    isQueryExecuting,
    isExecutionCancelling,
    executeQuery,
    cancelExecution,
  } = useQueryExecute(query);

  const updateQueryDescription = useUpdateQueryDescription(query, setQuery);
  const openAddToDashboardDialog = useAddToDashboardDialog(query);
  const openEmbedDialog = useEmbedDialog(query);
  const editSchedule = useEditScheduleDialog(query, setQuery);
  const addVisualization = useEditVisualizationDialog(query, queryResult, (newQuery, visualization) => {
    setQuery(newQuery);
    setSelectedVisualization(visualization.id);
  });
  const editVisualization = useEditVisualizationDialog(query, queryResult, newQuery => setQuery(newQuery));
  const deleteVisualization = useDeleteVisualization(query, setQuery);

  const canExecuteQuery = useMemo(() => queryFlags.canExecute && !isQueryExecuting && !areParametersDirty, [
    isQueryExecuting,
    areParametersDirty,
    queryFlags.canExecute,
  ]);

  const doExecuteQuery = useCallback(() => {
    if (!canExecuteQuery) {
      return;
    }
    executeQuery();
  }, [canExecuteQuery, executeQuery]);

  useEffect(() => {
    document.title = query.name;
  }, [query.name]);

  useEffect(() => {
    DataSource.get({ id: query.data_source_id }).$promise.then(setDataSource);
  }, [query.data_source_id]);

  useEffect(() => {
    const shortcuts = {
      "mod+enter": doExecuteQuery,
      "alt+enter": doExecuteQuery,
    };

    KeyboardShortcuts.bind(shortcuts);
    return () => {
      KeyboardShortcuts.unbind(shortcuts);
    };
  }, [doExecuteQuery]);

  return (
    <div className="query-page-wrapper">
      <div className="container">
        <QueryPageHeader query={query} onChange={setQuery} selectedVisualization={selectedVisualization} />
        <div className="query-metadata tiled bg-white p-15">
          <EditInPlace
            className="w-100"
            value={query.description}
            isEditable={queryFlags.canEdit}
            onDone={updateQueryDescription}
            placeholder="Add description"
            ignoreBlanks={false}
            editorProps={{ autosize: { minRows: 2, maxRows: 4 } }}
            multiline
          />
          <Divider />
          <QueryMetadata layout="horizontal" query={query} dataSource={dataSource} onEditSchedule={editSchedule} />
        </div>
        {queryResult && queryResultData.status !== "done" && (
          <div className="query-alerts m-t-15 m-b-15">
            <QueryExecutionStatus
              status={queryResultData.status}
              updatedAt={queryResultData.updatedAt}
              error={queryResultData.error}
              isCancelling={isExecutionCancelling}
              onCancel={cancelExecution}
            />
          </div>
        )}
        <div className="query-content tiled bg-white p-15 m-t-15">
          {query.hasParameters() && (
            <Parameters
              parameters={parameters}
              onValuesChange={() => {
                updateParametersDirtyFlag(false);
                executeQuery();
              }}
              onPendingValuesChange={() => updateParametersDirtyFlag()}
            />
          )}
          {queryResultData.status === "done" && (
            <>
              <QueryVisualizationTabs
                queryResult={queryResult}
                visualizations={query.visualizations}
                showNewVisualizationButton={queryFlags.canEdit}
                canDeleteVisualizations={queryFlags.canEdit}
                selectedTab={selectedVisualization}
                onChangeTab={setSelectedVisualization}
                onAddVisualization={addVisualization}
                onDeleteVisualization={deleteVisualization}
              />
              <Divider />
            </>
          )}
          <div className="d-flex align-items-center">
            {queryResultData.status === "done" && (
              <>
                {queryFlags.canEdit && (
                  <EditVisualizationButton
                    openVisualizationEditor={editVisualization}
                    selectedTab={selectedVisualization}
                  />
                )}
                <QueryControlDropdown
                  query={query}
                  queryResult={queryResult}
                  queryExecuting={isQueryExecuting}
                  showEmbedDialog={openEmbedDialog}
                  embed={false}
                  apiKey={query.api_key}
                  selectedTab={selectedVisualization}
                  openAddToDashboardForm={openAddToDashboardDialog}
                />
                <span className="m-l-10">
                  <strong>{queryResultData.rows.length}</strong> {pluralize("row", queryResultData.rows.length)}
                </span>
                <span className="m-l-10">
                  <strong>{durationHumanize(queryResult.getRuntime())}</strong>
                  <span className="hidden-xs"> runtime</span>
                </span>
              </>
            )}
            <span className="flex-fill" />
            {queryResultData.status === "done" && (
              <span className="m-r-10 hidden-xs">
                Updated <TimeAgo date={queryResult.query_result.retrieved_at} />
              </span>
            )}
            <Tooltip placement="top" title={`${KeyboardShortcuts.modKey} + Enter`}>
              <Button
                type="primary"
                loading={isQueryExecuting}
                disabled={!isQueryExecuting && !canExecuteQuery}
                onClick={executeQuery}>
                Execute
              </Button>
            </Tooltip>
          </div>
        </div>
      </div>
    </div>
  );
}

QueryView.propTypes = { query: PropTypes.object.isRequired }; // eslint-disable-line react/forbid-prop-types

export default function init(ngModule) {
  ngModule.component("pageQueryView", react2angular(QueryView));

  return {
    "/queries-react/:queryId": {
      template: '<page-query-view query="$resolve.query"></page-query-view>',
      reloadOnSearch: false,
      resolve: {
        query: (Query, $route) => {
          "ngInject";

          return Query.get({ id: $route.current.params.queryId }).$promise;
        },
      },
    },
  };
}

init.init = true;
