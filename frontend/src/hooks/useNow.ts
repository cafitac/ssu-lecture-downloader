import { useEffect, useState } from 'react';

/** The current time, refreshed once a minute so countdowns and "not open yet" states stay right. */
export function useNow(intervalMs = 60_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      setNow(new Date());
      timer = setTimeout(tick, intervalMs);
    };
    timer = setTimeout(tick, intervalMs);
    return () => clearTimeout(timer);
  }, [intervalMs]);
  return now;
}
