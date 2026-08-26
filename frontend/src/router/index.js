import { createRouter, createWebHistory } from 'vue-router'
import WorkspaceLayout from '../layouts/WorkspaceLayout.vue'
import DashboardView from '../views/business/DashboardView.vue'
import DocumentManagementView from '../views/business/DocumentManagementView.vue'
import DocumentLibraryDetailView from '../views/business/DocumentLibraryDetailView.vue'
import DocumentReviewWorkbenchView from '../views/business/DocumentReviewWorkbenchView.vue'
import JobListView from '../views/business/JobListView.vue'
import KnowledgeBaseView from '../views/business/KnowledgeBaseView.vue'
import KnowledgeLibraryDetailView from '../views/business/KnowledgeLibraryDetailView.vue'
import VectorStorageView from '../views/business/VectorStorageView.vue'
import ProjectAuthorizationView from '../views/business/ProjectAuthorizationView.vue'
import InstitutionDeploymentView from '../views/business/InstitutionDeploymentView.vue'
import LocalInitializationView from '../views/business/LocalInitializationView.vue'
import ImportTaskDetailView from '../views/business/ImportTaskDetailView.vue'
import PipelineListView from '../views/developer/PipelineListView.vue'
import TemplateListView from '../views/developer/TemplateListView.vue'
import DataFlowDebugView from '../views/developer/DataFlowDebugView.vue'
import SubgraphView from '../views/developer/SubgraphView.vue'
import ModelServicesView from '../views/developer/ModelServicesView.vue'
import OperatorCatalogView from '../views/developer/OperatorCatalogView.vue'

const routes = [{ path: '/', redirect: '/business/dashboard' }, {
  path: '/', component: WorkspaceLayout, children: [
    { path: '/business/dashboard', component: DashboardView },
    { path: '/business/documents', component: DocumentManagementView },
    { path: '/business/documents/:libraryId', component: DocumentLibraryDetailView, props: true },
    { path: '/business/documents/:libraryId/sources/:sourceId/versions/:versionId/review', component: DocumentReviewWorkbenchView, props: true, meta: { hideTopbar: true } },
    { path: '/business/jobs', component: JobListView },
    { path: '/business/knowledge', component: KnowledgeBaseView },
    { path: '/business/knowledge/:libraryId', component: KnowledgeLibraryDetailView, props: true },
    { path: '/business/vector-storage', component: VectorStorageView },
    { path: '/business/authorization', component: ProjectAuthorizationView },
    { path: '/business/migrations', redirect: '/institution-deployments/new' },
    { path: '/institution-deployments/new', component: InstitutionDeploymentView },
    { path: '/institution-deployments/drafts/:draftId', component: InstitutionDeploymentView },
    { path: '/institution-deployments/releases/:releaseId/build', component: InstitutionDeploymentView },
    { path: '/institution-deployments/releases/:releaseId', component: InstitutionDeploymentView },
    { path: '/local/initialization', component: LocalInitializationView },
    { path: '/local/imports/:jobId', component: ImportTaskDetailView },
    { path: '/developer/model-services', component: ModelServicesView },
    { path: '/developer/knowledge-types', redirect: '/developer/flow-templates?tab=knowledge-types' },
    { path: '/developer/standard-pipelines', component: PipelineListView },
    { path: '/developer/flow-templates', component: TemplateListView },
    { path: '/developer/flow-templates/subgraphs/:subflowId/revisions/:revision', component: SubgraphView },
    { path: '/developer/operator-catalog', component: OperatorCatalogView },
    { path: '/developer/dataflow-debug', component: DataFlowDebugView },
    // Preserve old V7 bookmarks while keeping the fixed developer navigation intact.
    { path: '/developer/vector-indexes', redirect: '/developer/dataflow-debug' },
  ],
}]
export default createRouter({ history: createWebHistory(), routes })
