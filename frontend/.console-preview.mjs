import http from 'node:http'
import { readFileSync } from 'node:fs'

// Temporary visual QA: render the real Console template/styles with local fixtures only.
const view = readFileSync(new URL('./src/views/developer/DataFlowDebugView.vue', import.meta.url), 'utf8')
const consoleTemplate = view.match(/<section class="console">[\s\S]*?<\/section>/)[0]
const style = view.match(/<style scoped>([\s\S]*?)<\/style>/)[1]
const baseStyle = readFileSync(new URL('./src/styles-v7.css', import.meta.url), 'utf8')
const events = Array.from({ length: 13 }, (_, index) => ({
  cursor: index + 1, created_at: '2026-08-27T10:05:43', level: 'info',
  type: index ? 'node.completed' : 'run.queued',
  node_id: index ? 'generate-text' : null,
  message: index ? '节点 generate-text completed' : '完整调试 Run 已进入队列',
}))
events[2].node_id = 'schema-validator-with-long-node-identifier'
events[2].type = 'node.validation.completed'
events[2].message = '长日志换行校验：' + 'abcdefghijklmnopqrstuvwxyz0123456789'.repeat(8) + '\n第二行：预览完成，本次调试不会写入正式知识。'
const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Console 本地样式验证</title>
<style>${baseStyle}\n${style}\nbody{padding:24px}#app{min-height:0}.qa-panel{max-width:1600px;height:260px;margin-bottom:24px}.qa-panel .console{height:100%}.qa-narrow{max-width:1120px}</style>
<main id="app"><h2>Console 本地样式验证（模拟日志，无后端请求）</h2>
<div class="qa-panel">${consoleTemplate}</div><h3>较窄 PC 内容区</h3>
<div class="qa-panel qa-narrow">${consoleTemplate}</div>
<button @click="events=[]">查看空日志</button></main>
<script type="module">import { createApp } from '/vue.js';createApp({data:()=>({events:${JSON.stringify(events)},cursor:13})}).mount('#app')</script></html>`

http.createServer((request, response) => {
  response.setHeader('Content-Type', request.url === '/vue.js' ? 'text/javascript' : 'text/html; charset=utf-8')
  response.end(request.url === '/vue.js'
    ? readFileSync(new URL('./node_modules/vue/dist/vue.esm-browser.js', import.meta.url))
    : html)
}).listen(5179, '127.0.0.1', () => console.log('Console QA ready: http://127.0.0.1:5179'))
