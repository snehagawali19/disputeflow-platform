import { useEffect, useRef, useState } from "react";
import type { PipelineEvent } from "../types/api";
import { wsUrl } from "../utils/api";

export function usePipelineSocket(sessionId: string | null, onEvent: (event: PipelineEvent) => void) {
  const [ready, setReady] = useState(false);
  const lastSequence = useRef(0);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!sessionId) {
      setReady(false);
      return;
    }
    let closed = false;
    let socket: WebSocket | null = null;
    let attempts = 0;

    const connect = () => {
      socket = new WebSocket(wsUrl(sessionId, lastSequence.current));
      socket.onopen = () => {
        attempts = 0;
        setReady(true);
      };
      socket.onmessage = (event) => {
        const data = JSON.parse(event.data) as PipelineEvent;
        if (data.event === "snapshot" && data.events) {
          data.events.forEach((item) => {
            if (item.sequence && item.sequence > lastSequence.current) {
              lastSequence.current = item.sequence;
            }
          });
          handler.current(data);
          return;
        }
        if (data.sequence && data.sequence > lastSequence.current) {
          lastSequence.current = data.sequence;
        }
        handler.current(data);
      };
      socket.onclose = () => {
        setReady(false);
        if (!closed && attempts < 5) {
          attempts += 1;
          setTimeout(connect, 500 * attempts);
        }
      };
    };
    connect();
    return () => {
      closed = true;
      setReady(false);
      socket?.close();
    };
  }, [sessionId]);

  return ready;
}
