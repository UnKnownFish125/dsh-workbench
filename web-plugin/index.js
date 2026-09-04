/**
 * Host half of the dsh-workbench UI surface plugin.
 *
 * Registers a same-origin proxy route `/worktree-api/*` that forwards to the
 * local worktree-server (localhost:6271 test / 6270 prod). The browser half
 * only talks to this same-origin route, so no CORS or private-network policy
 * applies, and the upstream server's "reject browser Origin" rule (R-A) is
 * satisfied by stripping the Origin header before forwarding.
 *
 * Security model is mirrored 1:1 from the reference deepmemory host plugin
 * (harness-memory-archive/web-plugin/index.js): the proxy deletes the incoming
 * Origin and Authorization headers and re-injects the service Bearer token from
 * WORKBENCH_API_TOKEN_FILE (default $DSH_HOME/.dsh-workbench-api-token).
 */
import http from 'node:http'
import z from '@deepseek-ai/schemastery'
import { settingsNamespace } from '@deepseek-ai/dsh-settings'
import fs from 'node:fs'

export const name = 'dsh-workbench'

export const inject = ['webServer', 'settings']

const TARGET_HOST = 'localhost'
// prod 6270 / test 6271 (DESIGN D-02, D-06; api-contract §0)
const TARGET_PORT = Number(process.env.WORKBENCH_SERVER_PORT || 6271)
const PREFIX = '/worktree-api'
const TOKEN_FILES = [
  process.env.WORKBENCH_API_TOKEN_FILE,
  process.env.DSH_HOME ? `${process.env.DSH_HOME}/.dsh-workbench-api-token` : '',
  process.env.HOME ? `${process.env.HOME}/.dsh-workbench-api-token` : '',
].filter((path, index, paths) => path && paths.indexOf(path) === index)

function readToken() {
  for (const path of TOKEN_FILES) {
    try {
      const token = fs.readFileSync(path, 'utf8').trim()
      if (token) return token
    } catch {}
  }
  return ''
}

/**
 * Mirror of the reference `memoryRequest`: a host-initiated JSON request to the
 * upstream worktree-server, injecting the Bearer token. Used by the convenience
 * exact route below (the client otherwise drives everything through the prefix
 * proxy, which injects the token inline in its upstream request).
 */
function workbenchRequest(method, path, body) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body))
    const token = readToken()
    const headers = { Accept: 'application/json' }
    if (payload) {
      headers['Content-Type'] = 'application/json'
      headers['Content-Length'] = String(payload.length)
    }
    if (token) headers.Authorization = `Bearer ${token}`
    const req = http.request({
      host: TARGET_HOST,
      port: TARGET_PORT,
      path,
      method,
      headers,
      timeout: 60000,
    }, (res) => {
      const chunks = []
      res.on('data', (chunk) => chunks.push(chunk))
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8')
        let data
        try { data = text ? JSON.parse(text) : {} } catch { data = { error: text || `HTTP ${res.statusCode}` } }
        if ((res.statusCode || 500) >= 400) reject(new Error(data.error || `HTTP ${res.statusCode}`))
        else resolve(data)
      })
    })
    req.on('timeout', () => req.destroy(new Error('worktree-server request timeout')))
    req.on('error', reject)
    if (payload) req.write(payload)
    req.end()
  })
}

function readRequestJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    req.on('data', (chunk) => {
      size += chunk.length
      if (size > 64 * 1024) {
        reject(new Error('request body too large'))
        req.destroy()
      } else chunks.push(chunk)
    })
    req.on('end', () => {
      try { resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {}) }
      catch (error) { reject(error) }
    })
    req.on('error', reject)
  })
}

export function apply(ctx) {
  // The official Plugins settings page discovers configurable cards from the
  // Host settings namespace list, then dispatches settings.plugin.item by key.
  // dsh-workbench keeps its (empty) config purely in the worktree-server, so
  // this empty section is only the discovery contract for the browser-owned
  // configuration card, mirroring the reference deepmemory host half.
  ctx.settings.register(settingsNamespace('dsh-workbench'), z.object({}))

  // Convenience exact route for the most important write (session↔node mount).
  // The prefix proxy below would also handle it, but this exercises the
  // workbenchRequest helper (host-initiated JSON + token injection) and maps
  // errors cleanly. Exact routes are dispatched before the prefix route.
  ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/v1/worktree/session-link',
    handler: (req, res) => {
      if (req.method !== 'POST') {
        res.writeHead(405)
        res.end()
        return
      }
      readRequestJson(req)
        .then((body) => {
          const nodePath = String((body && body.node_path) || '').trim()
          const sessionId = String((body && body.session_id) || '').trim()
          if (!nodePath || !sessionId) throw new Error('node_path and session_id are required')
          return workbenchRequest('POST', '/v1/worktree/session-link', { node_path: nodePath, session_id: sessionId })
        })
        .then((result) => {
          res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
          res.end(JSON.stringify(result))
        })
        .catch((error) => {
          res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' })
          res.end(JSON.stringify({ error: String((error && error.message) || error) }))
        })
    },
  })

  // Same-origin prefix proxy: everything under /worktree-api forwards to the
  // local worktree-server. Strips Origin/Authorization, re-injects the Bearer
  // token, and forwards the body verbatim (a plain stream pipe, so GET tree /
  // nodes / ancestors all pass through unchanged).
  ctx.webServer.register({
    kind: 'prefix',
    path: PREFIX,
    handler: (req, res) => {
      let rel = req.url ?? ''
      if (rel.startsWith(PREFIX)) rel = rel.slice(PREFIX.length)
      if (!rel.startsWith('/')) rel = '/' + rel
      const upstreamPath = rel || '/v1/health'
      const headers = { ...req.headers }
      delete headers.origin
      delete headers.authorization
      const token = readToken()
      if (token) headers.authorization = `Bearer ${token}`
      headers.host = `${TARGET_HOST}:${TARGET_PORT}`
      const upstream = http.request(
        {
          host: TARGET_HOST,
          port: TARGET_PORT,
          path: upstreamPath,
          method: req.method ?? 'GET',
          headers,
          timeout: 30000,
        },
        (upRes) => {
          res.writeHead(upRes.statusCode ?? 502, upRes.headers)
          upRes.pipe(res)
        },
      )
      upstream.on('timeout', () => upstream.destroy(new Error('worktree-server request timeout')))
      upstream.on('error', (error) => {
        try {
          res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' })
          res.end(JSON.stringify({ error: String((error && error.message) || error) }))
        } catch {}
      })
      req.pipe(upstream)
    },
  })
}
