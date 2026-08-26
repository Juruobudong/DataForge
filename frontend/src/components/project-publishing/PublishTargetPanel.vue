<script setup>
import DomainTerm from '../common/DomainTerm.vue'
import EnvironmentTabs from './EnvironmentTabs.vue'
import { FIELD_HELP } from '../../constants/fieldHelp'
import { statusLabel } from '../../constants/statusLabels'

defineProps({ deployment: { type: Object, default: null }, selectedStage: { type: String, required: true }, targetUri: { type: String, default: '' } })
defineEmits(['update:selectedStage'])
</script>

<template>
  <section class="panel stack">
    <div class="panel-head"><div><h3><DomainTerm term="deployment" :help="FIELD_HELP.deployment" /></h3><p>{{ deployment?.name || '暂无发布目标' }}</p></div><span v-if="deployment" class="badge blue">{{ statusLabel(deployment.scope) }}</span></div>
    <template v-if="deployment">
      <p v-if="deployment.scope==='institution'"><b>机构代码</b> · <code>{{ deployment.institution_code }}</code></p>
      <EnvironmentTabs :model-value="selectedStage" :target-uri="targetUri" @update:model-value="$emit('update:selectedStage',$event)" />
      <details><summary>高级信息</summary><div class="grid2"><p>Deployment Code<br><code>{{ deployment.code }}</code></p><p>ProjectDeployment ID<br><code>{{ deployment.id }}</code></p></div></details>
    </template>
    <slot />
  </section>
</template>
