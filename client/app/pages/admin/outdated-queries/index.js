import moment from 'moment';

import { Paginator } from '@/lib/pagination';
import template from './outdated-queries.html';

function OutdatedQueriesCtrl($scope, $http, $timeout) {
  $scope.autoUpdate = true;

  this.queries = new Paginator([], { itemsPerPage: 50 });
  this.setCurrentPage = (page) => {
    this.queries.setPage(page);
    $scope.$applyAsync();
  };

  const refresh = () => {
    if ($scope.autoUpdate) {
      $scope.refresh_time = moment().add(1, 'minutes');
      $http.get('/api/admin/queries/outdated').success((data) => {
        this.queries.updateRows(data.queries);
        $scope.updatedAt = data.updated_at * 1000.0;
      });
    }

    const timer = $timeout(refresh, 59 * 1000);

    $scope.$on('$destroy', () => {
      if (timer) {
        $timeout.cancel(timer);
      }
    });
  };

  refresh();
}

export default function init(ngModule) {
  ngModule.component('outdatedQueriesPage', {
    template,
    controller: OutdatedQueriesCtrl,
  });

  return {
    '/admin/queries/outdated': {
      template: '<outdated-queries-page></outdated-queries-page>',
      title: 'Outdated Queries',
    },
  };
}

init.init = true;
