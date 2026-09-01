import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import NodeInspector from './NodeInspector.vue'

const node = {
  id: 'document-chunker',
  data: {
    definition: {
      id: 'document-chunker', kind: 'operator', ref: 'document-chunker',
      params: {
        chunk_size: 500, chunk_overlap: 120, split_method: 'recursive', tokenizer_name: 'Qwen/Qwen3-32B',
        knowledge_type: 'qa', graph_mode: null,
      },
    },
    meta: {
      kind: 'operator', code: 'document-chunker', name: '文档切分', nodeRole: 'operator',
      parameterSchema: { properties: {
        chunk_size: { type: 'integer' }, chunk_overlap: { type: 'integer' },
        split_method: { type: 'string' }, tokenizer_name: { type: 'string' },
      } },
      inputs: {}, outputs: {},
    },
  },
}

describe('NodeInspector', () => {
  it('preserves runtime-owned parameters when editable parameters change', async () => {
    const wrapper = mount(NodeInspector, {
      props: { node },
      global: { stubs: {
        OperatorParameterForm: {
          emits: ['update:modelValue'],
          template: '<button class="parameter-update" type="button" @click="$emit(\'update:modelValue\', { chunk_size: 800, chunk_overlap: 120, split_method: \'recursive\', tokenizer_name: \'Qwen/Qwen3-32B\' })">update</button>',
        },
      } },
    })

    await wrapper.get('button.parameter-update').trigger('click')

    expect(wrapper.emitted('apply-parameters')).toEqual([[
      expect.objectContaining({ chunk_size: 800, knowledge_type: 'qa', graph_mode: null }),
    ]])
  })
})
