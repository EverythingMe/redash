import React from "react";
import PropTypes from "prop-types";
import { chain, cloneDeep, find } from "lodash";
import cx from "classnames";
import { Responsive, WidthProvider } from "react-grid-layout";
import {
  VisualizationWidget,
  TextboxWidget,
  IframeboxWidget,
  RestrictedWidget,
} from "@/components/dashboards/dashboard-widget";
import { FiltersType } from "@/components/Filters";
import cfg from "@/config/dashboard-grid-options";
import AutoHeightController from "./AutoHeightController";
import { WidgetTypeEnum } from "@/services/widget";

import "react-grid-layout/css/styles.css";
import "./dashboard-grid.less";

const ResponsiveGridLayout = WidthProvider(Responsive);

const WidgetType = PropTypes.shape({
  id: PropTypes.number.isRequired,
  options: PropTypes.shape({
    position: PropTypes.shape({
      col: PropTypes.number.isRequired,
      row: PropTypes.number.isRequired,
      sizeY: PropTypes.number.isRequired,
      minSizeY: PropTypes.number.isRequired,
      maxSizeY: PropTypes.number.isRequired,
      sizeX: PropTypes.number.isRequired,
      minSizeX: PropTypes.number.isRequired,
      maxSizeX: PropTypes.number.isRequired,
    }).isRequired,
  }).isRequired,
});

const SINGLE = "single-column";
const MULTI = "multi-column";

const DashboardWidget = React.memo(
  function DashboardWidget({
    widget,
    dashboard,
    onLoadWidget,
    onRefreshWidget,
    onRemoveWidget,
    onParameterMappingsChange,
    isEditing,
    canEdit,
    isPublic,
    isLoading,
    filters,
  }) {
    const { type } = widget;
    const onLoad = () => onLoadWidget(widget);
    const onRefresh = () => onRefreshWidget(widget);
    const onDelete = () => onRemoveWidget(widget.id);

    if (type === WidgetTypeEnum.VISUALIZATION) {
      return (
        <VisualizationWidget
          widget={widget}
          dashboard={dashboard}
          filters={filters}
          isEditing={isEditing}
          canEdit={canEdit}
          isPublic={isPublic}
          isLoading={isLoading}
          onLoad={onLoad}
          onRefresh={onRefresh}
          onDelete={onDelete}
          onParameterMappingsChange={onParameterMappingsChange}
        />
      );
    }
    if (type === WidgetTypeEnum.TEXTBOX) {
      return <TextboxWidget widget={widget} canEdit={canEdit} isPublic={isPublic} onDelete={onDelete} />;
    }
    if (type === WidgetTypeEnum.IFRAMEBOX) {
      return <IframeboxWidget widget={widget} canEdit={canEdit} isPublic={isPublic} onDelete={onDelete} />;
    }
    return <RestrictedWidget widget={widget} />;
  },
  (prevProps, nextProps) =>
    prevProps.widget === nextProps.widget &&
    prevProps.canEdit === nextProps.canEdit &&
    prevProps.isPublic === nextProps.isPublic &&
    prevProps.isLoading === nextProps.isLoading &&
    prevProps.filters === nextProps.filters &&
    prevProps.isEditing === nextProps.isEditing
);

class DashboardGrid extends React.Component {
  static propTypes = {
    isEditing: PropTypes.bool.isRequired,
    isPublic: PropTypes.bool,
    dashboard: PropTypes.object.isRequired, // eslint-disable-line react/forbid-prop-types
    widgets: PropTypes.arrayOf(WidgetType).isRequired,
    filters: FiltersType,
    onBreakpointChange: PropTypes.func,
    onLoadWidget: PropTypes.func,
    onRefreshWidget: PropTypes.func,
    onRemoveWidget: PropTypes.func,
    onLayoutChange: PropTypes.func,
    onParameterMappingsChange: PropTypes.func,
  };

  static defaultProps = {
    isPublic: false,
    filters: [],
    onLoadWidget: () => {},
    onRefreshWidget: () => {},
    onRemoveWidget: () => {},
    onLayoutChange: () => {},
    onBreakpointChange: () => {},
    onParameterMappingsChange: () => {},
  };

  static normalizeFrom(widget) {
    const {
      id,
      options: { position: pos },
    } = widget;

    return {
      i: id.toString(),
      x: pos.col,
      y: pos.row,
      w: pos.sizeX,
      h: pos.sizeY,
      minW: pos.minSizeX,
      maxW: pos.maxSizeX,
      minH: pos.minSizeY,
      maxH: pos.maxSizeY,
    };
  }

  mode = null;

  autoHeightCtrl = null;

  constructor(props) {
    super(props);

    this.state = {
      layouts: {},
      disableAnimations: true,
    };

    // init AutoHeightController
    this.autoHeightCtrl = new AutoHeightController(this.onWidgetHeightUpdated);
    this.autoHeightCtrl.update(this.props.widgets);
  }

  componentDidMount() {
    this.onBreakpointChange(document.body.offsetWidth <= cfg.mobileBreakPoint ? SINGLE : MULTI);
    // Work-around to disable initial animation on widgets; `measureBeforeMount` doesn't work properly:
    // it disables animation, but it cannot detect scrollbars.
    setTimeout(() => {
      this.setState({ disableAnimations: false });
    }, 50);
  }

  componentDidUpdate() {
    // update, in case widgets added or removed
    this.autoHeightCtrl.update(this.props.widgets);
  }

  componentWillUnmount() {
    this.autoHeightCtrl.destroy();
  }

  onLayoutChange = (_, layouts) => {
    // workaround for when dashboard starts at single mode and then multi is empty or carries single col data
    // fixes test dashboard_spec['shows widgets with full width']
    // TODO: open react-grid-layout issue
    if (layouts[MULTI]) {
      this.setState({ layouts });
    }

    // workaround for https://github.com/STRML/react-grid-layout/issues/889
    // remove next line when fix lands
    this.mode = document.body.offsetWidth <= cfg.mobileBreakPoint ? SINGLE : MULTI;
    // end workaround

    // don't save single column mode layout
    if (this.mode === SINGLE) {
      return;
    }

    const normalized = chain(layouts[MULTI])
      .keyBy("i")
      .mapValues(this.normalizeTo)
      .value();

    this.props.onLayoutChange(normalized);
  };

  onBreakpointChange = mode => {
    this.mode = mode;
    this.props.onBreakpointChange(mode === SINGLE);
  };

  // height updated by auto-height
  onWidgetHeightUpdated = (widgetId, newHeight) => {
    this.setState(({ layouts }) => {
      const layout = cloneDeep(layouts[MULTI]); // must clone to allow react-grid-layout to compare prev/next state
      const item = find(layout, { i: widgetId.toString() });
      if (item) {
        // update widget height
        item.h = Math.ceil((newHeight + cfg.margins) / cfg.rowHeight);
      }

      return { layouts: { [MULTI]: layout } };
    });
  };

  // height updated by manual resize
  onWidgetResize = (layout, oldItem, newItem) => {
    if (oldItem.h !== newItem.h) {
      this.autoHeightCtrl.remove(Number(newItem.i));
    }

    this.autoHeightCtrl.resume();
  };

  normalizeTo = layout => ({
    col: layout.x,
    row: layout.y,
    sizeX: layout.w,
    sizeY: layout.h,
    autoHeight: this.autoHeightCtrl.exists(layout.i),
  });

  render() {
    const {
      onLoadWidget,
      onRefreshWidget,
      onRemoveWidget,
      onParameterMappingsChange,
      filters,
      dashboard,
      isPublic,
      isEditing,
      widgets,
    } = this.props;
    const className = cx("dashboard-wrapper", isEditing ? "editing-mode" : "preview-mode");

    return (
      <div className={className}>
        <ResponsiveGridLayout
          draggableCancel="input,.sortable-container"
          className={cx("layout", { "disable-animations": this.state.disableAnimations })}
          cols={{ [MULTI]: cfg.columns, [SINGLE]: 1 }}
          rowHeight={cfg.rowHeight - cfg.margins}
          margin={[cfg.margins, cfg.margins]}
          isDraggable={isEditing}
          isResizable={isEditing}
          onResizeStart={this.autoHeightCtrl.stop}
          onResizeStop={this.onWidgetResize}
          layouts={this.state.layouts}
          onLayoutChange={this.onLayoutChange}
          onBreakpointChange={this.onBreakpointChange}
          breakpoints={{ [MULTI]: cfg.mobileBreakPoint, [SINGLE]: 0 }}>
          {widgets.map(widget => (
            <div
              key={widget.id}
              data-grid={DashboardGrid.normalizeFrom(widget)}
              data-widgetid={widget.id}
              data-test={`WidgetId${widget.id}`}
              className={cx("dashboard-widget-wrapper", {
                "widget-auto-height-enabled": this.autoHeightCtrl.exists(widget.id),
              })}>
              <DashboardWidget
                dashboard={dashboard}
                widget={widget}
                filters={filters}
                isPublic={isPublic}
                isLoading={widget.loading}
                isEditing={isEditing}
                canEdit={dashboard.canEdit()}
                onLoadWidget={onLoadWidget}
                onRefreshWidget={onRefreshWidget}
                onRemoveWidget={onRemoveWidget}
                onParameterMappingsChange={onParameterMappingsChange}
              />
            </div>
          ))}
        </ResponsiveGridLayout>
      </div>
    );
  }
}

export default DashboardGrid;
