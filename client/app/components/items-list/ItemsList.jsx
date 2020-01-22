import { omit, debounce } from "lodash";
import React from "react";
import PropTypes from "prop-types";
import hoistNonReactStatics from "hoist-non-react-statics";
import { clientConfig } from "@/services/auth";

export const ControllerType = PropTypes.shape({
  // values of props declared by wrapped component, current route's locals (`resolve: { ... }`) and title
  params: PropTypes.object.isRequired,

  isLoaded: PropTypes.bool.isRequired,
  isEmpty: PropTypes.bool.isRequired,

  // search
  searchTerm: PropTypes.string,
  updateSearch: PropTypes.func.isRequired, // (searchTerm: string) => void

  // tags
  selectedTags: PropTypes.array.isRequired,
  updateSelectedTags: PropTypes.func.isRequired, // (selectedTags: array of tags) => void

  // sorting
  orderByField: PropTypes.string,
  orderByReverse: PropTypes.bool.isRequired,
  toggleSorting: PropTypes.func.isRequired, // (orderByField: string) => void

  // pagination
  page: PropTypes.number.isRequired,
  itemsPerPage: PropTypes.number.isRequired,
  totalItemsCount: PropTypes.number.isRequired,
  pageSizeOptions: PropTypes.arrayOf(PropTypes.number).isRequired,
  pageItems: PropTypes.array.isRequired,
  updatePagination: PropTypes.func.isRequired, // ({ page: number, itemsPerPage: number }) => void

  handleError: PropTypes.func.isRequired, // (error) => void
});

export function wrap(WrappedComponent, createItemsSource, createStateStorage) {
  class ItemsListWrapper extends React.Component {
    static propTypes = {
      ...omit(WrappedComponent.propTypes, ["controller"]),
      onError: PropTypes.func,
      children: PropTypes.node,
    };

    static defaultProps = {
      ...omit(WrappedComponent.defaultProps, ["controller"]),
      onError: error => {
        // Allow calling chain to roll up, and then throw the error in global context
        setTimeout(() => {
          throw error;
        });
      },
      children: null,
    };

    constructor(props) {
      super(props);

      const stateStorage = createStateStorage();
      const itemsSource = createItemsSource();
      this._itemsSource = itemsSource;

      itemsSource.setState({ ...stateStorage.getState(), validate: false });
      itemsSource.getCallbackContext = () => this.state;

      itemsSource.onBeforeUpdate = () => {
        const state = itemsSource.getState();
        stateStorage.setState(state);
        this.setState(this.getState({ ...state, isLoaded: false }));
      };

      itemsSource.onAfterUpdate = () => {
        const state = itemsSource.getState();
        this.setState(this.getState({ ...state, isLoaded: true }));
      };

      itemsSource.onError = error => this.props.onError(error);

      const initialState = this.getState({ ...itemsSource.getState(), isLoaded: false });
      const { updatePagination, toggleSorting, updateSearch, updateSelectedTags, update, handleError } = itemsSource;
      this.state = {
        ...initialState,
        toggleSorting, // eslint-disable-line react/no-unused-state
        updateSearch: debounce(updateSearch, 200), // eslint-disable-line react/no-unused-state
        updateSelectedTags, // eslint-disable-line react/no-unused-state
        updatePagination, // eslint-disable-line react/no-unused-state
        update, // eslint-disable-line react/no-unused-state
        handleError, // eslint-disable-line react/no-unused-state
      };
    }

    componentDidMount() {
      this.state.update();
    }

    componentWillUnmount() {
      this._itemsSource.onBeforeUpdate = () => {};
      this._itemsSource.onAfterUpdate = () => {};
      this._itemsSource.onError = () => {};
    }

    // eslint-disable-next-line class-methods-use-this
    getState({ isLoaded, totalCount, pageItems, params, ...rest }) {
      params = {
        // Custom params from items source
        ...params,

        title: this.props.currentRoute.title,
        ...this.props.routeParams,

        // Add to params all props except of own ones
        ...omit(this.props, ["onError", "children", "currentRoute", "routeParams"]),
      };
      return {
        ...rest,

        params,

        isLoaded,
        isEmpty: !isLoaded || totalCount === 0,
        totalItemsCount: isLoaded ? totalCount : 0,
        pageSizeOptions: clientConfig.pageSizeOptions,
        pageItems: isLoaded ? pageItems : [],
      };
    }

    render() {
      // don't pass own props to wrapped component
      const { children, onError, ...props } = this.props;
      props.controller = this.state;
      return <WrappedComponent {...props}>{children}</WrappedComponent>;
    }
  }

  // Copy static methods from `WrappedComponent`
  hoistNonReactStatics(ItemsListWrapper, WrappedComponent);

  return ItemsListWrapper;
}
