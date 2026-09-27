import { readFileSync } from 'node:fs'

const port = Number(readFileSync(process.argv[2], 'utf8').split(/\r?\n/)[0])
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid local debugger port')
const pages = await fetch(`http://127.0.0.1:${port}/json/list`).then(r => r.json())
const page = pages.find(page => page.type === 'page' && page.url.startsWith('oc://renderer'))
if (!page) throw new Error('Installed renderer not found')
const socket = new WebSocket(page.webSocketDebuggerUrl)
const timer = setTimeout(() => { socket.close(); process.exit(1) }, 90000)
try {
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })
  const result = new Promise((resolve, reject) => {
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data)
      if (message.id !== 1) return
      if (message.error || message.result.exceptionDetails) return reject(new Error('Installed renderer initialization failed'))
      resolve(message.result.result.value)
    })
  })
  socket.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: {
    expression: '(async () => ({ connection: await window.api.awaitInitialization(), text: document.body.innerText }))()',
    awaitPromise: true, returnByValue: true,
  } }))
  // Captured only by the parent test process. Never write this connection to evidence.
  process.stdout.write(JSON.stringify(await result))
} finally {
  clearTimeout(timer)
  socket.close()
}
