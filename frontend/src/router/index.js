import { createRouter, createWebHistory } from 'vue-router'
import WorkspaceLayout from '../layouts/WorkspaceLayout.vue'
import DashboardView from '../views/business/DashboardView.vue'
import DocumentManagementView from '../views/business/DocumentManagementView.vue'
import DocumentLibraryDetailView from '../views/business/DocumentLibraryDetailView.vue'
import JobListView from '../views/business/JobListView.vue'
import KnowledgeBaseView from '../views/business/KnowledgeBaseView.vue'
import KnowledgeLibraryDetailView from '../views/business/KnowledgeLibraryDetailView.vue'
import ProjectAuthorizationView from '../views/business/ProjectAuthorizationView.vue'
import KnowledgeTypesView from '../views/developer/KnowledgeTypesView.vue'
import PipelineListView from '../views/developer/PipelineListView.vue'
import TemplateListView from '../views/developer/TemplateListView.vue'
import DataFlowDebugView from '../views/developer/DataFlowDebugView.vue'
import SubgraphView from '../views/developer/SubgraphView.vue'

const routes = [{ path: '/', redirect: '/business/dashboard' }, {
  path: '/', component: WorkspaceLayout, children: [
    { path: '/business/dashboard', component: DashboardView },
    { path: '/business/documents', component: DocumentManagementView },
    { path: '/business/documents/:libraryId', component: DocumentLibraryDetailView, props: true },
    { path: '/business/jobs', component: JobListView },
    { path: '/business/knowledge', component: KnowledgeBaseView },
    { path: '/business/knowledge/:libraryId', component: KnowledgeLibraryDetailView, props: true },
    { path: '/business/authorization', component: ProjectAuthorizationView },
    { path: '/developer/knowledge-types', component: KnowledgeTypesView },
    { path: '/developer/standard-pipelines', component: PipelineListView },
    { path: '/developer/flow-templates', component: TemplateListView },
    { path: '/developer/flow-templates/subgraphs/:subflowId/revisions/:revision', component: SubgraphView },
    { path: '/developer/dataflow-debug', component: DataFlowDebugView },
    // Preserve old V7 bookmarks while keeping the fixed developer navigation intact.
    { path: '/developer/vector-indexes', redirect: '/developer/dataflow-debug' },
  ],
}]
export default createRouter({ history: createWebHistory(), routes })
