import { maxBy } from "lodash";
import React, { useMemo } from "react";
import Table from "antd/lib/table";
import Tooltip from "antd/lib/tooltip";
import { RendererPropTypes } from "@/visualizations/prop-types";
import ColorPalette from "@/visualizations/ColorPalette";
import { createNumberFormatter } from "@/lib/value-format";

import prepareData from "./prepareData";
import FunnelBar from "./FunnelBar";
import "./index.less";

function generateRowKeyPrefix() {
  return Math.trunc(Math.random() * Number.MAX_SAFE_INTEGER).toString(36) + ":";
}

export default function Renderer({ data, options }: any) {
  const funnelData = useMemo(() => prepareData(data.rows, options), [data, options]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const rowKeyPrefix = useMemo(() => generateRowKeyPrefix(), [funnelData]);

  const formatValue = useMemo(() => createNumberFormatter(options.numberFormat), [options.numberFormat]);

  const formatPercentValue = useMemo(() => {
    const format = createNumberFormatter(options.percentFormat);
    return (value: any) => {
      if (value < options.percentValuesRange.min) {
        return `<${format(options.percentValuesRange.min)}`;
      }
      if (value > options.percentValuesRange.max) {
        return `>${format(options.percentValuesRange.max)}`;
      }
      return format(value);
    };
  }, [options.percentFormat, options.percentValuesRange]);

  const columns = useMemo(() => {
    if (funnelData.length === 0) {
      return [];
    }

    // @ts-expect-error ts-migrate(2532) FIXME: Object is possibly 'undefined'.
    const maxToPrevious = maxBy(funnelData, d => (isFinite(d.pctPrevious) ? d.pctPrevious : 0)).pctPrevious;
    return [
      {
        title: options.stepCol.displayAs,
        dataIndex: "step",
        width: "25%",
        className: "text-ellipsis",
        render: (text: any) => (
          <Tooltip title={text} mouseEnterDelay={0} mouseLeaveDelay={0}>
            {text}
          </Tooltip>
        ),
      },
      {
        title: options.valueCol.displayAs,
        dataIndex: "value",
        width: "45%",
        align: "center",
        render: (value: any, item: any) => (
          // @ts-expect-error ts-migrate(2745) FIXME: This JSX tag's 'children' prop expects type 'never... Remove this comment to see the full error message
          <FunnelBar align="center" color={options.color} value={item.pctMax}>
            {formatValue(value)}
          </FunnelBar>
        ),
      },
      {
        title: "% Max",
        dataIndex: "pctMax",
        width: "15%",
        align: "center",
        render: (value: any) => formatPercentValue(value),
      },
      {
        title: "% Previous",
        dataIndex: "pctPrevious",
        width: "15%",
        align: "center",
        render: (value: any) => (
          // @ts-expect-error ts-migrate(2745) FIXME: This JSX tag's 'children' prop expects type 'never... Remove this comment to see the full error message
          <FunnelBar className="funnel-percent-column" value={(value / maxToPrevious) * 100.0}>
            {formatPercentValue(value)}
          </FunnelBar>
        ),
      },
    ];
  }, [options.stepCol.displayAs, options.valueCol.displayAs, funnelData, formatValue, formatPercentValue]);

  if (funnelData.length === 0) {
    return null;
  }

  return (
    <div className="funnel-visualization-container">
      <Table
        // @ts-expect-error ts-migrate(2322) FIXME: Type '({ title: any; dataIndex: string; width: str... Remove this comment to see the full error message
        columns={columns}
        dataSource={funnelData}
        rowKey={(record, index) => rowKeyPrefix + index}
        pagination={false}
      />
    </div>
  );
}

Renderer.propTypes = RendererPropTypes;
