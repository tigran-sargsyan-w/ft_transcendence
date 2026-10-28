import { disconnectAt } from '../../src/modules/realtime/session-expiry.js';

const DAY_MS = 24 * 60 * 60 * 1000;

describe('disconnectAt', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('disconnects when the session expires, not before', () => {
    const disconnect = vi.fn();

    disconnectAt(new Date(Date.now() + 1000), disconnect);
    vi.advanceTimersByTime(999);

    expect(disconnect).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);

    expect(disconnect).toHaveBeenCalledTimes(1);
  });

  it('waits out an expiry further than a timer can count', () => {
    const disconnect = vi.fn();

    disconnectAt(new Date(Date.now() + 30 * DAY_MS), disconnect);
    // Past the 24.8 days of the longest timer, and not expired yet.
    vi.advanceTimersByTime(26 * DAY_MS);

    expect(disconnect).not.toHaveBeenCalled();

    vi.advanceTimersByTime(4 * DAY_MS);

    expect(disconnect).toHaveBeenCalledTimes(1);
  });

  it('disconnects at once when the session has already expired', () => {
    const disconnect = vi.fn();

    disconnectAt(new Date(Date.now() - 1), disconnect);

    expect(disconnect).toHaveBeenCalledTimes(1);
  });

  it('stops waiting when asked to', () => {
    const disconnect = vi.fn();
    const stop = disconnectAt(new Date(Date.now() + 1000), disconnect);

    stop();
    vi.advanceTimersByTime(2000);

    expect(disconnect).not.toHaveBeenCalled();
  });
});
