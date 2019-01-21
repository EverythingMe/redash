import settingsMenu from '@/lib/settings-menu';
import { Paginator } from '@/lib/pagination';
import template from './list.html';

function SnippetsCtrl($scope, $location, currentUser, QuerySnippet) {
  this.snippets = new Paginator([], { itemsPerPage: 20 });
  this.setCurrentPage = (page) => {
    this.snippets.setPage(page);
    $scope.$applyAsync();
  };

  QuerySnippet.query((snippets) => {
    this.snippets.updateRows(snippets);
  });
}

export default function init(ngModule) {
  settingsMenu.add({
    permission: 'create_query',
    title: 'Query Snippets',
    path: 'query_snippets',
    order: 5,
  });

  ngModule.component('snippetsListPage', {
    template,
    controller: SnippetsCtrl,
  });

  return {
    '/query_snippets': {
      template: '<snippets-list-page></snippets-list-page>',
      title: 'Query Snippets',
    },
  };
}

init.init = true;

