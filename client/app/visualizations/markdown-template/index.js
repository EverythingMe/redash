import { registerVisualization } from '@/visualizations';
import MarkdownTemplateRenderer from './MarkdownTemplateRenderer';
import MarkdownTemplateEditor from './MarkdownTemplateEditor';

const DEFAULT_OPTIONS = {};

export default function init() {
  registerVisualization({
    type: 'MARKDOWN',
    name: 'Markdown Template',
    getOptions: options => ({ ...DEFAULT_OPTIONS, ...options }),
    Renderer: MarkdownTemplateRenderer,
    Editor: MarkdownTemplateEditor,

    defaultColumns: 2,
    defaultRows: 2,
  });
}

init.init = true;
