import React, { useState } from "react";
import Button from "antd/lib/button";
import Menu from "antd/lib/menu";
import Icon from "antd/lib/icon";
import HelpTrigger from "@/components/HelpTrigger";
import { Auth, currentUser } from "@/services/auth";
import CreateDashboardDialog from "@/components/dashboards/CreateDashboardDialog";
import logoUrl from "@/assets/images/redash_icon_small.png";

import VersionInfo from "./VersionInfo";

function NavbarSection({ inlineCollapsed, children, ...props }) {
  return (
    <Menu
      selectable={false}
      mode={inlineCollapsed ? "inline" : "vertical"}
      inlineCollapsed={inlineCollapsed}
      theme="dark"
      {...props}>
      {children}
    </Menu>
  );
}

export default function DesktopNavbar() {
  const [collapsed, setCollapsed] = useState(true);

  return (
    <React.Fragment>
      <div className="header-logo">
        <a href="./">
          <img src={logoUrl} alt="Redash" style={{ height: collapsed ? "20px" : "40px" }} />
        </a>
      </div>

      <NavbarSection inlineCollapsed={collapsed}>
        {currentUser.hasPermission("list_dashboards") && (
          <Menu.Item key="dashboards">
            <a href="dashboards">
              <Icon type="desktop" />
              <span>Dashboards</span>
            </a>
          </Menu.Item>
        )}
        {currentUser.hasPermission("view_query") && (
          <Menu.Item key="queries">
            <a href="queries">
              <Icon type="code" />
              <span>Queries</span>
            </a>
          </Menu.Item>
        )}
        {currentUser.hasPermission("list_alerts") && (
          <Menu.Item key="alerts">
            <a href="alerts">
              <Icon type="alert" />
              <span>Alerts</span>
            </a>
          </Menu.Item>
        )}

        <Menu.Divider />
      </NavbarSection>

      <NavbarSection inlineCollapsed={collapsed} className="create-menu">
        <Menu.SubMenu
          key="create"
          title={
            <React.Fragment>
              <span data-test="CreateButton">
                <Icon type="plus" />
                <span>Create</span>
              </span>
            </React.Fragment>
          }>
          {currentUser.hasPermission("create_query") && (
            <Menu.Item key="new-query">
              <a href="queries/new" data-test="CreateQueryMenuItem">
                New Query
              </a>
            </Menu.Item>
          )}
          {currentUser.hasPermission("create_dashboard") && (
            <Menu.Item key="new-dashboard">
              <a data-test="CreateDashboardMenuItem" onMouseUp={() => CreateDashboardDialog.showModal()}>
                New Dashboard
              </a>
            </Menu.Item>
          )}
          {currentUser.hasPermission("list_alerts") && (
            <Menu.Item key="new-alert">
              <a data-test="CreateAlertMenuItem" href="alerts/new">
                New Alert
              </a>
            </Menu.Item>
          )}
        </Menu.SubMenu>
      </NavbarSection>

      <NavbarSection inlineCollapsed={collapsed}>
        <Menu.Item key="help">
          <HelpTrigger showTooltip={false} type="HOME">
            <Icon type="question-circle" />
            <span>Help</span>
          </HelpTrigger>
        </Menu.Item>
        {currentUser.isAdmin && (
          <Menu.Item key="settings">
            <a href="data_sources">
              <Icon type="setting" />
              <span>Settings</span>
            </a>
          </Menu.Item>
        )}
        <Menu.Divider />
      </NavbarSection>

      <NavbarSection inlineCollapsed={collapsed} className="profile-menu">
        <Menu.SubMenu
          key="profile"
          title={
            <span data-test="ProfileDropdown">
              <img className="profile__image_thumb" src={currentUser.profile_image_url} alt={currentUser.name} />
              {/* <span>{currentUser.name}</span> */}
            </span>
          }>
          <Menu.Item key="profile">
            <a href="users/me">Profile</a>
          </Menu.Item>
          {currentUser.hasPermission("super_admin") && (
            <Menu.Item key="status">
              <a href="admin/status">System Status</a>
            </Menu.Item>
          )}
          <Menu.Divider />
          <Menu.Item key="logout">
            <a onClick={() => Auth.logout()}>Log out</a>
          </Menu.Item>
          <Menu.Divider />
          <Menu.Item key="version" disabled className="version-info">
            <VersionInfo />
          </Menu.Item>
        </Menu.SubMenu>
      </NavbarSection>

      <Button onClick={() => setCollapsed(!collapsed)} className="collapse-button">
        <Icon type={collapsed ? "menu-unfold" : "menu-fold"} />
      </Button>
    </React.Fragment>
  );
}

DesktopNavbar.propTypes = {};

DesktopNavbar.defaultProps = {};
