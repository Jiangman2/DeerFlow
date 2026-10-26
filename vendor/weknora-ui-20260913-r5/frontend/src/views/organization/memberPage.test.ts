import assert from 'node:assert/strict'
import test from 'node:test'
import { memberPageWindow } from './memberPage'

test('large rosters stay bounded and every row is reachable once', () => {
  for (const total of [0, 21, 8417, 84748]) {
    for (const size of [20, 50, 100]) {
      let visited = 0
      for (let page = 1; page <= Math.max(1, Math.ceil(total / size)); page++) {
        const result = memberPageWindow(total, page, size)
        assert.equal(result.start, visited)
        assert.ok(result.end - result.start <= 100)
        visited = result.end
      }
      assert.equal(visited, total)
    }
  }
})

test('search and removal clamp a page that no longer exists', () => {
  assert.deepEqual(memberPageWindow(1, 424, 20), { page: 1, start: 0, end: 1 })
  assert.deepEqual(memberPageWindow(40, 3, 20), { page: 2, start: 20, end: 40 })
  assert.deepEqual(memberPageWindow(0, 3, 20), { page: 1, start: 0, end: 0 })
})

test('invalid controls cannot request an unbounded render', () => {
  for (const page of [NaN, Infinity, -5, 1.5]) {
    for (const size of [0, -1, Infinity, 84748]) {
      assert.deepEqual(memberPageWindow(84748, page, size), { page: 1, start: 0, end: 20 })
    }
  }
})
