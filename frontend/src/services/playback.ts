import type { ApiIterationMeta } from "./api";

type TimedFrame = Pick<ApiIterationMeta, "iteration" | "sim_time_utc">;

const timestampMs = (frame: TimedFrame): number =>
  frame.sim_time_utc === null ? NaN : Date.parse(frame.sim_time_utc);

// Timeline offsets are simulated seconds, so irregular or skipped frames retain
// their recorded durations. A configured step fills gaps in missing timestamps.
export function buildPlaybackTimeline(frames: TimedFrame[], stepSeconds: number | null): number[] | null {
  if (frames.length === 0) return null;
  const timeline = [0];
  for (let i = 1; i < frames.length; i++) {
    const recordedSeconds = (timestampMs(frames[i]) - timestampMs(frames[i - 1])) / 1000;
    const fallbackSeconds = stepSeconds === null ? NaN :
      (frames[i].iteration - frames[i - 1].iteration) * stepSeconds;
    const seconds = Number.isFinite(recordedSeconds) && recordedSeconds > 0 ? recordedSeconds : fallbackSeconds;
    if (!Number.isFinite(seconds) || seconds <= 0) return null;
    timeline.push(timeline[i - 1] + seconds);
  }
  return timeline;
}

function cursorTimeSeconds(cursor: number, timeline: number[]): number {
  const position = Math.max(0, Math.min(cursor, timeline.length - 1));
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, timeline.length - 1);
  return timeline[lower] + (timeline[upper] - timeline[lower]) * (position - lower);
}

export function advancePlaybackCursor(cursor: number, simulatedSeconds: number, timeline: number[]): number {
  const duration = timeline.at(-1) ?? 0;
  if (duration <= 0) return 0;
  let time = cursorTimeSeconds(cursor, timeline) + Math.max(0, simulatedSeconds);
  if (time > duration) time %= duration;
  let lower = 0;
  let upper = timeline.length - 1;
  while (upper - lower > 1) {
    const middle = Math.floor((lower + upper) / 2);
    if (timeline[middle] <= time) lower = middle;
    else upper = middle;
  }
  return lower + (time - timeline[lower]) / (timeline[upper] - timeline[lower]);
}

export function simulationTimeAtCursor(frames: TimedFrame[], cursor: number, timeline: number[] | null): number | null {
  if (frames.length === 0) return null;
  const lower = Math.max(0, Math.min(Math.floor(cursor), frames.length - 1));
  let anchor = lower;
  if (!Number.isFinite(timestampMs(frames[anchor]))) {
    anchor = frames.findIndex((frame) => Number.isFinite(timestampMs(frame)));
  }
  if (anchor < 0) return null;
  const timestamp = timestampMs(frames[anchor]);
  return timeline === null ? timestamp :
    timestamp + (cursorTimeSeconds(cursor, timeline) - timeline[anchor]) * 1000;
}
