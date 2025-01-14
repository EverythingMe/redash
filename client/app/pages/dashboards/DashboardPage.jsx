import { isEmpty, map } from "lodash";
import React, { useState, useEffect } from "react";
import PropTypes from "prop-types";
import cx from "classnames";

import Button from "antd/lib/button";
import Checkbox from "antd/lib/checkbox";
import routeWithUserSession from "@/components/ApplicationArea/routeWithUserSession";
import DynamicComponent from "@/components/DynamicComponent";
import DashboardGrid from "@/components/dashboards/DashboardGrid";
import Parameters from "@/components/Parameters";
import Filters from "@/components/Filters";

import { Dashboard } from "@/services/dashboard";
import recordEvent from "@/services/recordEvent";
import resizeObserver from "@/services/resizeObserver";
import routes from "@/services/routes";
import location from "@/services/location";
import url from "@/services/url";
import useImmutableCallback from "@/lib/hooks/useImmutableCallback";

import useDashboard from "./hooks/useDashboard";
import DashboardHeader from "./components/DashboardHeader";

import "./DashboardPage.less";

function DashboardSettings({ dashboardConfiguration }) {
  const { dashboard, updateDashboard } = dashboardConfiguration;
  return (
    <div className="m-b-10 p-15 bg-white tiled">
      <Checkbox
        checked={!!dashboard.dashboard_filters_enabled}
        onChange={({ target }) => updateDashboard({ dashboard_filters_enabled: target.checked })}
        data-test="DashboardFiltersCheckbox">
        Use Dashboard Level Filters
      </Checkbox>
    </div>
  );
}

DashboardSettings.propTypes = {
  dashboardConfiguration: PropTypes.object.isRequired, // eslint-disable-line react/forbid-prop-types
};

function AddWidgetContainer({ dashboardConfiguration, className, ...props }) {
  const { showAddTextboxDialog, showAddWidgetDialog, showAddIframeboxDialog } = dashboardConfiguration;
  return (
    <div className={cx("add-widget-container", className)} {...props}>
      <h2>
        <i className="zmdi zmdi-widgets" aria-hidden="true" />
        <span className="hidden-xs hidden-sm">
          Widgets are individual query visualizations or text boxes you can place on your dashboard in various
          arrangements.
        </span>
      </h2>
      <div>
        <Button className="m-r-15" onClick={showAddTextboxDialog} data-test="AddTextboxButton">
          Add Textbox
        </Button>
        <Button className="m-r-15" onClick={showAddIframeboxDialog} data-test="AddIframeboxButton">
          Add Iframe
        </Button>
        <Button type="primary" onClick={showAddWidgetDialog} data-test="AddWidgetButton">
          Add Widget
        </Button>
      </div>
    </div>
  );
}

AddWidgetContainer.propTypes = {
  dashboardConfiguration: PropTypes.object.isRequired, // eslint-disable-line react/forbid-prop-types
  className: PropTypes.string,
};

function DashboardComponent(props) {
  const dashboardConfiguration = useDashboard(props.dashboard);
  const {
    dashboard,
    filters,
    setFilters,
    loadDashboard,
    loadWidget,
    removeWidget,
    saveDashboardLayout,
    globalParameters,
    updateDashboard,
    refreshDashboard,
    refreshWidget,
    editingLayout,
    setGridDisabled,
  } = dashboardConfiguration;

  const [pageContainer, setPageContainer] = useState(null);
  const [bottomPanelStyles, setBottomPanelStyles] = useState({});
  const onParametersEdit = parameters => {
    const paramOrder = map(parameters, "name");
    updateDashboard({ options: { globalParamOrder: paramOrder } });
  };

  useEffect(() => {
    if (pageContainer) {
      const unobserve = resizeObserver(pageContainer, () => {
        if (editingLayout) {
          const style = window.getComputedStyle(pageContainer, null);
          const bounds = pageContainer.getBoundingClientRect();
          const paddingLeft = parseFloat(style.paddingLeft) || 0;
          const paddingRight = parseFloat(style.paddingRight) || 0;
          setBottomPanelStyles({
            left: Math.round(bounds.left) + paddingRight,
            width: pageContainer.clientWidth - paddingLeft - paddingRight,
          });
        }

        // reflow grid when container changes its size
        window.dispatchEvent(new Event("resize"));
      });
      return unobserve;
    }
  }, [pageContainer, editingLayout]);

  return (
    <div className="container" ref={setPageContainer} data-test={`DashboardId${dashboard.id}Container`}>
      <DashboardHeader
        dashboardConfiguration={dashboardConfiguration}
        headerExtra={
          <DynamicComponent
            name="Dashboard.HeaderExtra"
            dashboard={dashboard}
            dashboardConfiguration={dashboardConfiguration}
          />
        }
      />
      {!isEmpty(globalParameters) && (
        <div className="dashboard-parameters m-b-10 p-15 bg-white tiled" data-test="DashboardParameters">
          <Parameters
            parameters={globalParameters}
            onValuesChange={refreshDashboard}
            sortable={editingLayout}
            onParametersEdit={onParametersEdit}
          />
        </div>
      )}
      {!isEmpty(filters) && (
        <div className="m-b-10 p-15 bg-white tiled" data-test="DashboardFilters">
          <Filters filters={filters} onChange={setFilters} />
        </div>
      )}
      {editingLayout && <DashboardSettings dashboardConfiguration={dashboardConfiguration} />}
      <div id="dashboard-container">
        <DashboardGrid
          dashboard={dashboard}
          widgets={dashboard.widgets}
          filters={filters}
          isEditing={editingLayout}
          onLayoutChange={editingLayout ? saveDashboardLayout : () => {}}
          onBreakpointChange={setGridDisabled}
          onLoadWidget={loadWidget}
          onRefreshWidget={refreshWidget}
          onRemoveWidget={removeWidget}
          onParameterMappingsChange={loadDashboard}
        />
      </div>
      {editingLayout && (
        <AddWidgetContainer dashboardConfiguration={dashboardConfiguration} style={bottomPanelStyles} />
      )}
    </div>
  );
}

DashboardComponent.propTypes = {
  dashboard: PropTypes.object.isRequired, // eslint-disable-line react/forbid-prop-types
};

function DashboardPage({ dashboardSlug, dashboardId, onError }) {
  const [dashboard, setDashboard] = useState(null);
  const handleError = useImmutableCallback(onError);

  useEffect(() => {
    Dashboard.get({ id: dashboardId, slug: dashboardSlug })
      .then(dashboardData => {
        recordEvent("view", "dashboard", dashboardData.id);
        setDashboard(dashboardData);

        // if loaded by slug, update location url to use the id
        if (!dashboardId) {
          location.setPath(url.parse(dashboardData.url).pathname, true);
        }
      })
      .catch(handleError);
  }, [dashboardId, dashboardSlug, handleError]);

  return <div className="dashboard-page">{dashboard && <DashboardComponent dashboard={dashboard} />}</div>;
}

DashboardPage.propTypes = {
  dashboardSlug: PropTypes.string,
  dashboardId: PropTypes.string,
  onError: PropTypes.func,
};

DashboardPage.defaultProps = {
  dashboardSlug: null,
  dashboardId: null,
  onError: PropTypes.func,
};

// route kept for backward compatibility
routes.register(
  "Dashboards.LegacyViewOrEdit",
  routeWithUserSession({
    path: "/dashboard/:dashboardSlug",
    render: pageProps => <DashboardPage {...pageProps} />,
  })
);

routes.register(
  "Dashboards.ViewOrEdit",
  routeWithUserSession({
    path: "/dashboards/:dashboardId([^-]+)(-.*)?",
    render: pageProps => <DashboardPage {...pageProps} />,
  })
);
