import assert from "node:assert/strict";
import test from "node:test";
import { advancePlaybackCursor, buildPlaybackTimeline, simulationTimeAtCursor } from "../src/services/playback.ts";

const start = Date.parse("2026-02-15T00:00:30Z");
const frame = (iteration, seconds) => ({ iteration, sim_time_utc: new Date(start + seconds * 1000).toISOString() });
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);

test("30-second steps play at 0.1x, 1x, and 10x real time", () => {
  const timeline = buildPlaybackTimeline([frame(0, 0), frame(1, 30), frame(2, 60)], 30);
  for (const speed of [0.1, 1, 10]) {
    near(advancePlaybackCursor(0, 3 * speed, timeline), 3 * speed / 30);
  }
});

test("unequal intervals and skipped iterations retain their recorded time", () => {
  const frames = [frame(0, 0), frame(1, 30), frame(4, 120)];
  const timeline = buildPlaybackTimeline(frames, 30);
  assert.deepEqual(timeline, [0, 30, 120]);
  near(advancePlaybackCursor(0, 75, timeline), 1.5);
  near(simulationTimeAtCursor(frames, 1.5, timeline), start + 75000);
});

test("looping retains excess elapsed time and resuming keeps the selected position", () => {
  const timeline = [0, 30, 60];
  near(advancePlaybackCursor(1.5, 20, timeline), 5 / 30);
  near(advancePlaybackCursor(0.5, 15, timeline), 1);
  near(advancePlaybackCursor(1, 0, timeline), 1);
  near(advancePlaybackCursor(0, 125, timeline), 5 / 30);
});

test("parameters fill missing timestamps without inventing a step", () => {
  const frames = [frame(0, 0), { iteration: 2, sim_time_utc: null }, frame(3, 90)];
  const timeline = buildPlaybackTimeline(frames, 30);
  assert.deepEqual(timeline, [0, 60, 90]);
  assert.equal(simulationTimeAtCursor(frames, 1.5, timeline), start + 75000);
  assert.equal(buildPlaybackTimeline(frames, null), null);
  assert.equal(buildPlaybackTimeline([], 30), null);
  assert.equal(advancePlaybackCursor(0, 10, [0]), 0);
});
