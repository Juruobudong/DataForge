import { defineStore } from 'pinia'

export const useWorkspaceStore = defineStore('workspace', {
  state: () => ({ workspace: 'business', selectedLibraryId: '', selectedPipelineId: '', selectedRunId: '' }),
  actions: {
    switchTo(workspace) { this.workspace = workspace; this.selectedLibraryId = ''; this.selectedPipelineId = ''; this.selectedRunId = '' },
  },
})
