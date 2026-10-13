import {
  parseEvent,
  websocketUrl,
  type ClientEvent,
  type ConnectionStatus,
  type ServerEvent,
} from "./protocol";

export interface Transport {
  send(event: ClientEvent): boolean;
  close(): void;
}
export type OnEvent = (event: ServerEvent) => void;
export type OnStatus = (status: ConnectionStatus) => void;

export function connectSocket(
  backend: string,
  onEvent: OnEvent,
  onStatus: OnStatus,
  onError: (message: string) => void,
): Transport {
  let socket: WebSocket | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  let delay = 1000;
  const connect = () => {
    if (stopped) return;
    onStatus("connecting");
    socket = new WebSocket(websocketUrl(backend));
    socket.onopen = () => {
      if (stopped) return;
      delay = 1000;
      socket?.send(
        JSON.stringify({
          type: "hello",
          client: "extension",
          version: "0.1",
        } satisfies ClientEvent),
      );
      onStatus("connected");
    };
    socket.onmessage = (message: MessageEvent<unknown>) => {
      if (stopped) return;
      try {
        if (typeof message.data !== "string")
          throw new Error("Unexpected binary event");
        onEvent(parseEvent(message.data));
      } catch {
        onError("Sunucudan gelen olay protokole uymuyor.");
      }
    };
    socket.onerror = () => {
      /* onclose owns reconnect; no duplicate timers. */
    };
    socket.onclose = () => {
      if (stopped) return;
      onStatus("disconnected");
      timer = setTimeout(connect, delay);
      delay = Math.min(delay * 2, 10000);
    };
  };
  connect();
  return {
    send(event) {
      if (stopped || socket?.readyState !== WebSocket.OPEN) return false;
      try {
        socket.send(JSON.stringify(event));
        return true;
      } catch {
        onError("Mesaj gönderilemedi. Bağlantıyı kontrol et.");
        return false;
      }
    },
    close() {
      stopped = true;
      clearTimeout(timer);
      if (socket) {
        socket.onclose = null;
        socket.onmessage = null;
        socket.onopen = null;
        socket.close();
      }
    },
  };
}
