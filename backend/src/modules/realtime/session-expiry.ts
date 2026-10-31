// A timer keeps its delay on 32 bits: above 2^31-1 ms (about 24.8 days) it
// fires at once.
const MAX_DELAY_MS = 2 ** 31 - 1;

// Calls `disconnect` when the session expires. A date further away than a
// timer can count is waited out in several steps: each wake-up looks at the
// time left again, and never disconnects before the date. Returns what stops
// the wait.
export function disconnectAt(expiresAt: Date, disconnect: () => void) {
  let timer: NodeJS.Timeout | undefined;

  const wait = () => {
    const left = expiresAt.getTime() - Date.now();

    if (left <= 0) {
      disconnect();
      return;
    }

    timer = setTimeout(wait, Math.min(left, MAX_DELAY_MS));
  };

  wait();

  return () => clearTimeout(timer);
}
