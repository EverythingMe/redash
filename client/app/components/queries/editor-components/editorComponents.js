import { isArray, isNil, each } from "lodash";
import { useMemo } from "react";

const componentsRegistry = new Map();

export const QueryEditorComponents = {
  SCHEMA_BROWSER: "SchemaBrowser",
  QUERY_EDITOR: "QueryEditor",
}

export function registerEditorComponent(componentName, component, dataSourceTypes) {
  if (isNil(dataSourceTypes)) {
    dataSourceTypes = [null]; // use `null` entry for the default set of components
  }

  if (!isArray(dataSourceTypes)) {
    dataSourceTypes = [dataSourceTypes];
  }

  each(dataSourceTypes, dataSourceType => {
    componentsRegistry.set(dataSourceType, { ...componentsRegistry.get(dataSourceType), [componentName]: component });
  });
}

export function useEditorComponents(dataSourceType) {
  return useMemo(() => ({ ...componentsRegistry.get(null), ...componentsRegistry.get(dataSourceType) }), [
    dataSourceType,
  ]);
}
