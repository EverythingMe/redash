import React from "react";
import renderer from "react-test-renderer";
import { mount, shallow } from 'enzyme';
import Button from "antd/lib/button";

import DataSourcesList from "./DataSourcesList";

const mockDataSources = [
    {
        "id": 2,
        "name": "Cloudwatch-East-ASVPARTNERSHIPSREWARDS-nonprod-xqd918",
        "type": "pg",
        "syntax": "sql",
        "paused": 0,
        "pause_reason": null,
        "supports_auto_limit": true,
        "view_only": false
    },
    {
        "id": 1,
        "name": "Naveed Tests DS",
        "type": "pg",
        "syntax": "sql",
        "paused": 0,
        "pause_reason": null,
        "supports_auto_limit": true,
        "view_only": false
    }
]
const mockCreate = () => {
    console.log("mocking ds creation")
}


describe("Tests DatasourceList", () => {

  it("renders correctly", () => {
    const wrapper  = shallow( <DataSourcesList />)
    expect(wrapper).toHaveLength(1);
  });

  it("list filtering works correctly", () => {
    const wrapper  = shallow( <DataSourcesList />)

    wrapper.instance().state = {
        dataSourceTypes: [],
        dataSources: mockDataSources,
        displayList:mockDataSources,
        loading: false,
      }
    wrapper.instance().filterDatasourceList("naveed",mockDataSources)
    expect(wrapper.instance().state.displayList).toHaveLength(1);
  });

});