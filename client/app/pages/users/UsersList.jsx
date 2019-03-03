import { map } from 'lodash';
import React from 'react';
import PropTypes from 'prop-types';
import { react2angular } from 'react2angular';

import Button from 'antd/lib/button';
import { Paginator } from '@/components/Paginator';
import DynamicComponent from '@/components/DynamicComponent';
import { UserPreviewCard } from '@/components/PreviewCard';

import { wrap as itemsList, ControllerType } from '@/components/items-list/ItemsList';
import { ResourceItemsSource } from '@/components/items-list/classes/ItemsSource';
import { UrlStateStorage } from '@/components/items-list/classes/StateStorage';

import LoadingState from '@/components/items-list/components/LoadingState';
import EmptyState from '@/components/items-list/components/EmptyState';
import * as Sidebar from '@/components/items-list/components/Sidebar';
import ItemsTable, { Columns } from '@/components/items-list/components/ItemsTable';

import Layout from '@/components/layouts/ContentWithSidebar';
import CreateUserDialog from '@/components/users/CreateUserDialog';

import settingsMenu from '@/services/settingsMenu';
import { currentUser } from '@/services/auth';
import { policy } from '@/services/policy';
import { User } from '@/services/user';
import navigateTo from '@/services/navigateTo';
import { $location } from '@/services/ng';

function UsersListActions({ user, enableUser, disableUser, deleteUser }) {
  if (user.id === currentUser.id) {
    return null;
  }
  if (user.is_invitation_pending) {
    return (
      <Button type="danger" className="w-100" onClick={event => deleteUser(event, user)}>Delete</Button>
    );
  }
  return user.is_disabled ? (
    <Button type="primary" className="w-100" onClick={event => enableUser(event, user)}>Enable</Button>
  ) : (
    <Button className="w-100" onClick={event => disableUser(event, user)}>Disable</Button>
  );
}

UsersListActions.propTypes = {
  user: PropTypes.shape({
    id: PropTypes.number,
    is_invitation_pending: PropTypes.bool,
    is_disabled: PropTypes.bool,
  }).isRequired,
  enableUser: PropTypes.func.isRequired,
  disableUser: PropTypes.func.isRequired,
  deleteUser: PropTypes.func.isRequired,
};

class UsersList extends React.Component {
  static propTypes = {
    controller: ControllerType.isRequired,
  };

  sidebarMenu = [
    {
      key: 'active',
      href: 'users',
      title: 'Active Users',
    },
    {
      key: 'pending',
      href: 'users/pending',
      title: 'Pending Invitations',
    },
    {
      key: 'disabled',
      href: 'users/disabled',
      title: 'Disabled Users',
      isAvailable: () => policy.canCreateUser(),
    },
  ];

  listColumns = [
    Columns.custom.sortable((text, user) => (
      <UserPreviewCard user={user} withLink />
    ), {
      title: 'Name',
      field: 'name',
      width: null,
    }),
    Columns.custom.sortable((text, user) => map(user.groups, group => (
      <a key={'group' + group.id} className="label label-tag" href={'groups/' + group.id}>{group.name}</a>
    )), {
      title: 'Groups',
      field: 'groups',
    }),
    Columns.timeAgo.sortable({
      title: 'Joined',
      field: 'created_at',
      className: 'text-nowrap',
      width: '1%',
    }),
    Columns.timeAgo.sortable({
      title: 'Last Active At',
      field: 'active_at',
      className: 'text-nowrap',
      width: '1%',
    }),
    Columns.custom((text, user) => (
      <UsersListActions
        user={user}
        enableUser={this.enableUser}
        disableUser={this.disableUser}
        deleteUser={this.deleteUser}
      />
    ), {
      width: '1%',
      isAvailable: () => policy.canCreateUser(),
    }),
  ];

  componentDidMount() {
    if ($location.path() === '/users/new') {
      this.createUser();
    }
  }

  createUser = () => {
    if (policy.isCreateUserEnabled()) {
      CreateUserDialog.showModal().result.then((success) => {
        if (success) {
          this.props.controller.update();
        }
      });
    }
  }

  onTableRowClick = (event, item) => navigateTo('users/' + item.id);

  enableUser = (event, user) => {
    // prevent default click action on table rows
    event.preventDefault();
    event.stopPropagation();
    return User.enableUser(user)
      .then(() => this.props.controller.update());
  };

  disableUser = (event, user) => {
    // prevent default click action on table rows
    event.preventDefault();
    event.stopPropagation();
    return User.disableUser(user)
      .then(() => this.props.controller.update());
  };

  deleteUser = (event, user) => {
    // prevent default click action on table rows
    event.preventDefault();
    event.stopPropagation();
    return User.deleteUser(user)
      .then(() => this.props.controller.update());
  };

  // eslint-disable-next-line class-methods-use-this
  renderPageHeader() {
    if (!policy.canCreateUser()) {
      return null;
    }
    return (
      <div className="m-b-15">
        <Button type="primary" disabled={!policy.isCreateUserEnabled()} onClick={this.createUser}>
          <i className="fa fa-plus m-r-5" />
          New User
        </Button>
        <DynamicComponent name="UsersListExtra" />
      </div>
    );
  }

  render() {
    const { controller } = this.props;
    return (
      <React.Fragment>
        {this.renderPageHeader()}
        <Layout>
          <Layout.Sidebar className="m-b-0">
            <Sidebar.SearchInput
              value={controller.searchTerm}
              onChange={controller.updateSearch}
            />
            <Sidebar.Menu items={this.sidebarMenu} selected={controller.params.currentPage} />
            <Sidebar.PageSizeSelect
              options={controller.pageSizeOptions}
              value={controller.itemsPerPage}
              onChange={itemsPerPage => controller.updatePagination({ itemsPerPage })}
            />
          </Layout.Sidebar>
          <Layout.Content>
            {!controller.isLoaded && <LoadingState className="" />}
            {controller.isLoaded && controller.isEmpty && <EmptyState className="" />}
            {
              controller.isLoaded && !controller.isEmpty && (
                <div className="table-responsive">
                  <ItemsTable
                    items={controller.pageItems}
                    columns={this.listColumns}
                    onRowClick={this.onTableRowClick}
                    context={this.actions}
                    orderByField={controller.orderByField}
                    orderByReverse={controller.orderByReverse}
                    toggleSorting={controller.toggleSorting}
                  />
                  <Paginator
                    totalCount={controller.totalItemsCount}
                    itemsPerPage={controller.itemsPerPage}
                    page={controller.page}
                    onChange={page => controller.updatePagination({ page })}
                  />
                </div>
              )
            }
          </Layout.Content>
        </Layout>
      </React.Fragment>
    );
  }
}

export default function init(ngModule) {
  settingsMenu.add({
    permission: 'list_users',
    title: 'Users',
    path: 'users',
    isActive: path => path.startsWith('/users') && (path !== '/users/me'),
    order: 2,
  });

  ngModule.component('pageUsersList', react2angular(itemsList(
    UsersList,
    new ResourceItemsSource({
      getRequest(request, { params: { currentPage } }) {
        switch (currentPage) {
          case 'active':
            request.pending = false;
            break;
          case 'pending':
            request.pending = true;
            break;
          case 'disabled':
            request.disabled = true;
            break;
          // no default
        }
        return request;
      },
      getResource() {
        return User.query.bind(User);
      },
      getItemProcessor() {
        return (item => new User(item));
      },
    }),
    new UrlStateStorage({ orderByField: 'created_at', orderByReverse: true }),
  )));
}

init.init = true;
