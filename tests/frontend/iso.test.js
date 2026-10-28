import test from 'node:test';
import assert from 'node:assert/strict';
import { worldToIso, isoToWorld, screenInputToWorldDelta, segmentIntersectsAABB, computeVisibilityPolygon } from '../../frontend/src/js/iso.js';

test('projection round trips independent of viewport and camera', () => {
  for (const screenW of [375, 1280, 2560]) {
    const camera = { x: 8, y: 11, screenW, screenH: 720 };
    const point = worldToIso(19, -3, camera);
    assert.deepEqual(isoToWorld(point.x, point.y, camera), { x: 19, y: -3 });
  }
});

test('opposing input cancels and diagonal input stays normalized', () => {
  assert.deepEqual(screenInputToWorldDelta(true, true, false, false), { dx: 0, dy: 0 });
  const { dx, dy } = screenInputToWorldDelta(true, false, true, false);
  assert.equal(Math.hypot(dx, dy), 1);
});

test('raycast includes origin hits and ignores inactive sight geometry', () => {
  const wall = { x: 2, y: -1, w: 1, h: 2 };
  assert.equal(segmentIntersectsAABB(0, 0, 4, 0, wall), 0.5);
  assert.equal(segmentIntersectsAABB(2, 0, 4, 0, wall), 0);
  assert.equal(segmentIntersectsAABB(0, 4, 4, 4, wall), null);
  assert.equal(computeVisibilityPolygon(0, 0, 10, [{ ...wall, active: false }], 4)[0].x, 10);
});
