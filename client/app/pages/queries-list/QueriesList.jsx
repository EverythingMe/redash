import React, { useEffect, useRef } from "react";
import cx from "classnames";

import routeWithUserSession from "@/components/ApplicationArea/routeWithUserSession";
import Link from "@/components/Link";
import PageHeader from "@/components/PageHeader";
import Paginator from "@/components/Paginator";
import DynamicComponent from "@/components/DynamicComponent";
import { QueryTagsControl } from "@/components/tags-control/TagsControl";
import SchedulePhrase from "@/components/queries/SchedulePhrase";

import { wrap as itemsList, ControllerType } from "@/components/items-list/ItemsList";
import useItemsListExtraActions from "@/components/items-list/hooks/useItemsListExtraActions";
import { ResourceItemsSource } from "@/components/items-list/classes/ItemsSource";
import { UrlStateStorage } from "@/components/items-list/classes/StateStorage";

import * as Sidebar from "@/components/items-list/components/Sidebar";
import ItemsTable, { Columns } from "@/components/items-list/components/ItemsTable";

import Layout from "@/components/layouts/ContentWithSidebar";

import { Query } from "@/services/query";
import { currentUser } from "@/services/auth";
import location from "@/services/location";
import routes from "@/services/routes";

import QueriesListEmptyState from "./QueriesListEmptyState";

import "./queries-list.css";

const sidebarMenu = [
  {
    key: "all",
    href: "queries",
    title: "All Queries",
    icon: () => <Sidebar.MenuIcon icon="fa fa-code" />,
  },
  {
    key: "my",
    href: "queries/my",
    title: "My Queries",
    icon: () => <Sidebar.ProfileImage user={currentUser} />,
  },
  {
    key: "favorites",
    href: "queries/favorites",
    title: "Favorites",
    icon: () => <Sidebar.MenuIcon icon="fa fa-star" />,
  },
  {
    key: "archive",
    href: "queries/archive",
    title: "Archived",
    icon: () => <Sidebar.MenuIcon icon="fa fa-archive" />,
  },
];

const listColumns = [
  Columns.favorites({ className: "p-r-0" }),
  Columns.custom.sortable(
    (text, item) => (
      <React.Fragment>
        <Link className="table-main-title" href={"queries/" + item.id}>
          {item.name}
        </Link>
        <QueryTagsControl className="d-block" tags={item.tags} isDraft={item.is_draft} isArchived={item.is_archived} />
      </React.Fragment>
    ),
    {
      title: "Name",
      field: "name",
      width: null,
    }
  ),
  Columns.custom((text, item) => item.user.name, { title: "Owner", width: "1%" }),
  Columns.dateTime.sortable({ title: "Created At", field: "created_at", width: "1%" }),
  Columns.dateTime.sortable({
    title: "Last Executed At",
    field: "retrieved_at",
    orderByField: "executed_at",
    width: "1%",
  }),
  Columns.custom.sortable((text, item) => <SchedulePhrase schedule={item.schedule} isNew={item.isNew()} />, {
    title: "Refresh Schedule",
    field: "schedule",
    width: "1%",
  }),
];

function QueriesListExtraActions(props) {
  return <DynamicComponent name="QueriesList.Actions" {...props} />;
}

function QueriesList({ controller }) {
  const controllerRef = useRef();
  controllerRef.current = controller;

  useEffect(() => {
    const unlistenLocationChanges = location.listen((unused, action) => {
      const searchTerm = location.search.q || "";
      if (action === "PUSH" && searchTerm !== controllerRef.current.searchTerm) {
        controllerRef.current.updateSearch(searchTerm);
      }
    });

    return () => {
      unlistenLocationChanges();
    };
  }, []);

  const {
    areExtraActionsAvailable,
    listColumns: tableColumns,
    Component: ExtraActionsComponent,
    selectedItems,
  } = useItemsListExtraActions(controller, listColumns, QueriesListExtraActions);

  return (
    <div className="page-queries-list">
      <div className="container">
        <PageHeader
          title={controller.params.pageTitle}
          actions={
            currentUser.hasPermission("create_query") ? (
              <Link.Button block type="primary" href="queries/new">
                <i className="fa fa-plus m-r-5" aria-hidden="true" />
                New Query
              </Link.Button>
            ) : null
          }
        />
        <Layout>
          <Layout.Sidebar className="m-b-0">
            <Sidebar.SearchInput
              placeholder="Search Queries..."
              label="Search queries"
              value={controller.searchTerm}
              onChange={controller.updateSearch}
            />
            <Sidebar.Menu items={sidebarMenu} selected={controller.params.currentPage} />
            <Sidebar.Tags url="api/queries/tags" onChange={controller.updateSelectedTags} showUnselectAll />
          </Layout.Sidebar>
          <Layout.Content>
            {controller.isLoaded && controller.isEmpty ? (
              <QueriesListEmptyState
                page={controller.params.currentPage}
                searchTerm={controller.searchTerm}
                selectedTags={controller.selectedTags}
              />
            ) : (
              <React.Fragment>
                <div className={cx({ "m-b-10": areExtraActionsAvailable })}>
                  <ExtraActionsComponent selectedItems={selectedItems} />
                </div>
                <div className="bg-white tiled table-responsive">
                  <ItemsTable
                    items={controller.pageItems}
                    loading={!controller.isLoaded}
                    columns={tableColumns}
                    orderByField={controller.orderByField}
                    orderByReverse={controller.orderByReverse}
                    toggleSorting={controller.toggleSorting}
                  />
                  <Paginator
                    showPageSizeSelect
                    totalCount={controller.totalItemsCount}
                    pageSize={controller.itemsPerPage}
                    onPageSizeChange={itemsPerPage => controller.updatePagination({ itemsPerPage })}
                    page={controller.page}
                    onChange={page => controller.updatePagination({ page })}
                  />
                </div>
              </React.Fragment>
            )}
          </Layout.Content>
        </Layout>
      </div>
    </div>
  );
}

QueriesList.propTypes = {
  controller: ControllerType.isRequired,
};

const QueriesListPage = itemsList(
  QueriesList,
  () =>
    new ResourceItemsSource({
      getResource({ params: { currentPage } }) {
        return {
          all: Query.query.bind(Query),
          my: Query.myQueries.bind(Query),
          favorites: Query.favorites.bind(Query),
          archive: Query.archive.bind(Query),
        }[currentPage];
      },
      getItemProcessor() {
        return item => new Query(item);
      },
    }),
  () => new UrlStateStorage({ orderByField: "created_at", orderByReverse: true })
);

routes.register(
  "Queries.List",
  routeWithUserSession({
    path: "/queries",
    title: "Queries",
    render: pageProps => <QueriesListPage {...pageProps} currentPage="all" />,
  })
);
routes.register(
  "Queries.Favorites",
  routeWithUserSession({
    path: "/queries/favorites",
    title: "Favorite Queries",
    render: pageProps => <QueriesListPage {...pageProps} currentPage="favorites" />,
  })
);
routes.register(
  "Queries.Archived",
  routeWithUserSession({
    path: "/queries/archive",
    title: "Archived Queries",
    render: pageProps => <QueriesListPage {...pageProps} currentPage="archive" />,
  })
);
routes.register(
  "Queries.My",
  routeWithUserSession({
    path: "/queries/my",
    title: "My Queries",
    render: pageProps => <QueriesListPage {...pageProps} currentPage="my" />,
  })
);
